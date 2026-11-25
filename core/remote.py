"""Acesso aos equipamentos pelo servidor do cliente (rede privada que só o servidor alcança).

Como funciona (sem instalar nada no servidor):
- SSH no servidor (chave ou senha).
- Proxy SOCKS5 local (igual `ssh -D`): os navegadores do app (gravar, abrir, executar) saem pelo
  servidor usando `socks5://127.0.0.1:<porta>`.
- `dial(ip, porta)`: um socket local ligado ao equipamento pelo servidor (usado pelo scanner).
- Consultas no servidor: só uma lista FIXA de comandos de leitura, montados pelo app com valores
  validados (IPs e portas). Nada que altere o servidor ou os equipamentos.
"""
from __future__ import annotations

import ipaddress
import os
import re
import select
import shlex
import socket
import struct
import threading
from dataclasses import dataclass, field

DEFAULT_KEY = os.path.join(os.path.expanduser("~"), ".ssh", "oltsoft_acs")
KNOWN_HOSTS = os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts_geradorbot")


@dataclass
class Server:
    host: str
    port: int = 22
    user: str = "support"
    auth: str = "key"                         # "key" ou "password"
    key_path: str = DEFAULT_KEY
    name: str = ""
    password: str | None = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return self.name or f"{self.user}@{self.host}:{self.port}"


class RemoteError(RuntimeError):
    pass


def _ip(value: str) -> str:
    return str(ipaddress.IPv4Address(str(value).strip()))


def _port(value) -> int:
    p = int(value)
    if not 1 <= p <= 65535:
        raise ValueError(f"porta inválida: {value}")
    return p


class SshGateway:
    """Conexão SSH com o servidor + proxy SOCKS5 local + consultas de leitura."""

    def __init__(self, server: Server, timeout: float = 15):
        self.server = server
        self.timeout = timeout
        self.client = None
        self.socks_port: int | None = None
        self._socks_srv: socket.socket | None = None
        self._closed = threading.Event()
        self._tools: set[str] | None = None

    # ------------------------------------------------------------------ conexão
    def connect(self) -> "SshGateway":
        import paramiko
        c = paramiko.SSHClient()
        try:
            if os.path.exists(KNOWN_HOSTS):
                c.load_host_keys(KNOWN_HOSTS)
        except Exception:  # noqa: BLE001
            pass
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())   # primeiro acesso: guarda a chave (accept-new)
        s = self.server
        auth = dict(look_for_keys=False, allow_agent=False)
        if s.auth == "password":
            if not s.password:
                raise RemoteError("Senha do servidor não informada.")
            auth["password"] = s.password
        else:
            key = os.path.expanduser(s.key_path or DEFAULT_KEY)
            if not os.path.exists(key):
                raise RemoteError(f"Chave SSH não encontrada: {key}")
            auth["key_filename"] = key
        try:
            c.connect(s.host, port=int(s.port or 22), username=s.user, timeout=self.timeout,
                      banner_timeout=self.timeout, auth_timeout=self.timeout, **auth)
        except paramiko.AuthenticationException:
            raise RemoteError(f"Login recusado em {s.label} (usuário/chave/senha).") from None
        except (socket.timeout, TimeoutError):
            raise RemoteError(f"{s.host}:{s.port} não respondeu. Precisa de VPN ou liberar o IP "
                              f"na porta {s.port}?") from None
        except OSError as e:
            raise RemoteError(f"Não conectou em {s.host}:{s.port}: {e}") from None
        try:
            os.makedirs(os.path.dirname(KNOWN_HOSTS), exist_ok=True)
            c.save_host_keys(KNOWN_HOSTS)
        except Exception:  # noqa: BLE001
            pass
        c.get_transport().set_keepalive(30)
        self.client = c
        return self

    def _transport(self):
        if self.client is None:
            self.connect()
        t = self.client.get_transport()
        if t is None or not t.is_active():
            self.connect()
            t = self.client.get_transport()
        return t

    def open_channel(self, ip: str, port: int, timeout: float | None = None):
        return self._transport().open_channel("direct-tcpip", (ip, int(port)), ("127.0.0.1", 0),
                                              timeout=timeout or self.timeout)

    def close(self):
        self._closed.set()
        for obj in (self._socks_srv, self.client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        self._socks_srv = self.client = None
        self.socks_port = None

    def __enter__(self):
        return self.connect() if self.client is None else self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------------ socket pelo servidor
    def dial(self, ip: str, port: int, timeout: float = 5.0) -> socket.socket:
        """Socket local de verdade (funciona com ssl.wrap_socket) ligado a ip:porta pelo servidor."""
        try:
            chan = self.open_channel(ip, port, timeout)
        except Exception as e:  # noqa: BLE001
            raise ConnectionRefusedError(f"servidor não abriu {ip}:{port}: {e}") from None
        a, b = socket.socketpair()
        threading.Thread(target=_pipe, args=(b, chan, self._closed), daemon=True).start()
        a.settimeout(timeout)
        return a

    # ------------------------------------------------------------------ proxy SOCKS5
    def start_socks(self) -> str:
        """Abre (uma vez) o proxy SOCKS5 em 127.0.0.1 e devolve 'socks5://127.0.0.1:<porta>'."""
        if self.socks_port:
            return self.proxy_url
        self._transport()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        srv.settimeout(0.5)
        self._socks_srv, self.socks_port = srv, srv.getsockname()[1]
        threading.Thread(target=self._socks_loop, daemon=True).start()
        return self.proxy_url

    @property
    def proxy_url(self) -> str | None:
        return f"socks5://127.0.0.1:{self.socks_port}" if self.socks_port else None

    def _socks_loop(self):
        while not self._closed.is_set():
            try:
                cli, _ = self._socks_srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._socks_client, args=(cli,), daemon=True).start()

    def _socks_client(self, cli: socket.socket):
        try:
            cli.settimeout(self.timeout)
            ver, n = _recv(cli, 2)
            if ver != 5:
                return cli.close()
            _recv(cli, n)                                  # métodos de auth oferecidos
            cli.sendall(b"\x05\x00")                       # sem autenticação (só escuta em 127.0.0.1)
            ver, cmd, _, atyp = _recv(cli, 4)
            if atyp == 1:
                host = socket.inet_ntoa(bytes(_recv(cli, 4)))
            elif atyp == 3:
                host = bytes(_recv(cli, _recv(cli, 1)[0])).decode("idna")
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, bytes(_recv(cli, 16)))
            else:
                return _socks_reply(cli, 8)
            port = struct.unpack(">H", bytes(_recv(cli, 2)))[0]
            if cmd != 1:                                   # só CONNECT
                return _socks_reply(cli, 7)
            try:
                chan = self.open_channel(host, port)
            except Exception:  # noqa: BLE001
                return _socks_reply(cli, 5)                # conexão recusada
            _socks_reply(cli, 0, keep=True)
            cli.settimeout(None)
            _pipe(cli, chan, self._closed)
        except Exception:  # noqa: BLE001
            try:
                cli.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ consultas de leitura
    def _exec(self, cmd: str, timeout: float = 120) -> str:
        """Roda um comando de LEITURA montado por este módulo (nunca texto vindo do usuário)."""
        self._transport()
        _in, out, _err = self.client.exec_command(cmd, timeout=timeout)
        return out.read().decode("utf-8", "replace")

    def tools(self) -> set[str]:
        if self._tools is None:
            names = " ".join(t for t, *_ in CHECKUP_TOOLS)
            out = self._exec(f"for t in {names}; do command -v $t >/dev/null 2>&1 && echo $t; done", timeout=20)
            self._tools = set(out.split())
        return self._tools

    def forwarding_ok(self) -> bool:
        """O SSH deixa encaminhar conexões? (abre um canal até a própria porta SSH do servidor)"""
        try:
            chan = self.open_channel("127.0.0.1", int(self.server.port or 22), timeout=8)
            chan.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    def checkup(self) -> list[dict]:
        """Verifica se o servidor tem o que o app precisa. Cada item:
        {"item", "ok", "level" (obrigatório|recomendado|opcional), "used_for", "fix"}."""
        items = [{"item": "Login SSH", "ok": True, "level": "obrigatório",
                  "used_for": "conectar no servidor", "fix": ""}]
        fwd = self.forwarding_ok()
        items.append({"item": "Encaminhamento de porta (AllowTcpForwarding)", "ok": fwd, "level": "obrigatório",
                      "used_for": "abrir equipamentos no navegador, gravar templates e executar o lote",
                      "fix": "no /etc/ssh/sshd_config: AllowTcpForwarding yes (e reiniciar o sshd)"})
        have = self.tools()
        for tool, level, used_for, fix in CHECKUP_TOOLS:
            items.append({"item": tool, "ok": tool in have, "level": level, "used_for": used_for, "fix": fix})
        return items


    def tcp_scan(self, ips: list[str], ports: list[int], timeout: float = 1.5, parallel: int = 64) -> dict[str, int]:
        """Testa portas TCP a partir do servidor. Devolve {ip: primeira porta aberta}."""
        ips = [_ip(x) for x in ips]
        ports = [_port(p) for p in ports]
        if not ips:
            return {}
        missing = {"bash", "timeout", "xargs"} - self.tools()
        if missing:
            raise RemoteError(f"Falta no servidor para o teste de portas: {', '.join(sorted(missing))} "
                              "(veja Verificar servidor na tela Servidores).")
        t = max(0.3, min(float(timeout), 10.0))
        par = max(1, min(int(parallel), 128))
        port_list = " ".join(str(p) for p in ports)
        # script fixo: só abre e fecha conexões TCP de teste
        inner = (f'for p in {port_list}; do timeout {t} bash -c "</dev/tcp/$0/$p" 2>/dev/null '
                 f'&& {{ echo "$0 $p"; exit 0; }}; done')
        cmd = (f"printf '%s\\n' {' '.join(ips)} | xargs -P {par} -n 1 bash -c {shlex.quote(inner)}")
        budget = 30 + len(ips) * len(ports) * t / par * 2
        out = self._exec(cmd, timeout=budget)
        res = {}
        for line in out.splitlines():
            m = re.fullmatch(r"(\d+\.\d+\.\d+\.\d+) (\d+)", line.strip())
            if m and m.group(1) not in res:
                res[m.group(1)] = int(m.group(2))
        return res

    def arp(self) -> dict[str, str]:
        out = self._exec("ip -4 neigh show 2>/dev/null || cat /proc/net/arp", timeout=20)
        table = {}
        for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s.*?([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", out):
            table[m.group(1)] = m.group(2).upper()
        return table

    def snmp_sysdescr(self, ip: str, community: str = "public") -> str | None:
        if "snmpget" not in self.tools():
            return None
        if not re.fullmatch(r"[\w.\-@#!$%^&*+=]{1,64}", community or ""):
            return None
        out = self._exec(f"snmpget -v2c -c {shlex.quote(community)} -On -Oqv -t 2 -r 0 {_ip(ip)} "
                         "1.3.6.1.2.1.1.1.0 2>/dev/null", timeout=15)
        val = out.strip().strip('"')
        return val if val and "No Such" not in val and "Timeout" not in val else None

    def networks(self) -> list[dict]:
        """Redes que o servidor alcança, para a busca automática: interfaces, rotas, vizinhos (ARP) e
        os IPs privados conectados ao servidor (no servidor do ACS, são as CPEs)."""
        out = self._exec("ip -4 -o addr show; echo ===; ip -4 route; echo ===; ip -4 neigh show; echo ===; "
                         "ss -Htn state established 2>/dev/null | awk '{print $4}'", timeout=30)
        addrs, routes, neigh, peers = (out.split("===") + ["", "", "", ""])[:4]
        self._own_ips = set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", addrs))
        return candidate_networks(addrs, routes, neigh, peers)

    def own_ips(self) -> set[str]:
        """IPs do próprio servidor (não são equipamentos do cliente)."""
        if getattr(self, "_own_ips", None) is None:
            out = self._exec("ip -4 -o addr show", timeout=20)
            self._own_ips = set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", out))
        return self._own_ips


# ---------------------------------------------------------------------- helpers de rede
def _recv(sock, n) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("fechou")
        buf += chunk
    return buf


def _socks_reply(cli, code, keep=False):
    try:
        cli.sendall(b"\x05" + bytes([code]) + b"\x00\x01" + b"\x00" * 6)
    finally:
        if not keep:
            cli.close()


def _pipe(sock, chan, closed: threading.Event):
    try:
        while not closed.is_set():
            r, _, _ = select.select([sock, chan], [], [], 1.0)
            if sock in r:
                data = sock.recv(65536)
                if not data:
                    break
                chan.sendall(data)
            if chan in r:
                data = chan.recv(65536)
                if not data:
                    break
                sock.sendall(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        for s in (chan, sock):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]
_VIRTUAL = re.compile(r"vmware|virtualbox|vbox|hyper-v|vethernet|wsl|parallels|utun", re.I)
_IGNORE_IF = re.compile(r"^(lo|docker\d*|br-|veth|virbr|cni|flannel|kube|tailscale|vEthernet|Loopback)", re.I)


def candidate_networks(addrs: str, routes: str, neigh: str = "", peers: str = "") -> list[dict]:
    """Monta a lista de redes candidatas a partir da saída de `ip addr`, `ip route` e `ip neigh`.

    Cada item: {"cidr", "source", "hosts", "suggested"}. Redes de contêiner e públicas ficam de fora;
    redes grandes (> /22) vêm com suggested=False para não varrer sem querer.
    """
    found: dict[str, dict] = {}

    def add(net, source):
        try:
            n = ipaddress.ip_network(net, strict=False)
        except ValueError:
            return
        if n.version != 4 or n.prefixlen < 8 or not any(n.subnet_of(p) for p in _PRIVATE if n.prefixlen >= p.prefixlen):
            return
        if n.prefixlen >= 31:
            return
        key = str(n)
        item = found.setdefault(key, {"cidr": key, "source": set(), "hosts": n.num_addresses - 2,
                                      "suggested": n.prefixlen >= 22, "alive": 0})
        item["source"].add(source)

    ignored_nets = []        # redes de contêiner/virtuais do próprio servidor
    for line in addrs.splitlines():
        m = re.search(r"^\d*:?\s*(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line.strip())
        if not m:
            continue
        if _IGNORE_IF.match(m.group(1)):
            ignored_nets.append(ipaddress.ip_network(m.group(2), strict=False))
        else:
            add(m.group(2), f"interface {m.group(1)}")
    for line in routes.splitlines():
        parts = line.split()
        if not parts or parts[0] == "default":
            continue
        dev = ""
        if " dev " in line:
            dev = re.split(r"\s+(?:proto|scope|src|metric|linkdown|onlink)\b", line.split(" dev ", 1)[1])[0].strip()
        if _IGNORE_IF.match(dev) or "linkdown" in parts:
            continue
        if "/" in parts[0]:
            add(parts[0], "rota" + (f" via {dev}" if dev else ""))
    def group_by_24(ips, source):
        counts: dict[str, int] = {}
        for raw in ips:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if ip.version == 4 and any(ip in p for p in _PRIVATE) and not any(ip in n for n in ignored_nets):
                net = str(ipaddress.ip_network(f"{ip}/24", strict=False))
                counts[net] = counts.get(net, 0) + 1
        for net, count in counts.items():
            covered = [k for k in found if ipaddress.ip_network(net).subnet_of(ipaddress.ip_network(k))]
            if covered:
                for k in covered:
                    found[k]["alive"] += count
            else:
                add(net, source)
                if net in found:
                    found[net]["alive"] = count

    # vizinhos (ARP), ignorando interfaces de contêiner/virtuais
    neigh_ips = []
    for line in neigh.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s.*lladdr", line)
        dev = re.search(r"\bdev\s+(\S+)", line)
        if m and not (dev and _IGNORE_IF.match(dev.group(1))):
            neigh_ips.append(m.group(1))
    group_by_24(neigh_ips, "vizinhos (ARP)")
    # conexões abertas no servidor (no ACS: as CPEs que conversam com ele)
    peer_ips = [re.sub(r":\d+$", "", p.strip()).strip("[]") for p in peers.split()]
    group_by_24([p for p in peer_ips if not p.startswith("127.")], "conexões no servidor (ex.: CPEs no ACS)")
    out = []
    for item in found.values():
        item["source"] = ", ".join(sorted(item["source"]))
        if _VIRTUAL.search(item["source"]):      # VMware/VirtualBox/Hyper-V: rede virtual do próprio PC
            item["suggested"] = False
            item["source"] += " (virtual)"
        out.append(item)
    return sorted(out, key=lambda x: (not x["suggested"], ipaddress.ip_network(x["cidr"])))


def local_networks() -> list[dict]:
    """Redes deste computador (interfaces, rotas e ARP) — Windows, Linux e macOS."""
    import subprocess

    from core.platform_utils import IS_WINDOWS, no_window_flags
    addrs, routes, neigh = "", "", ""
    try:
        if IS_WINDOWS:
            ps = ("Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '169.254*'} | "
                  "ForEach-Object { \"$($_.InterfaceAlias) inet $($_.IPAddress)/$($_.PrefixLength)\" }; "
                  "'==='; Get-NetRoute -AddressFamily IPv4 | Where-Object {$_.DestinationPrefix -ne '0.0.0.0/0'} | "
                  "ForEach-Object { \"$($_.DestinationPrefix) dev $($_.InterfaceAlias)\" }")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                 timeout=20, creationflags=no_window_flags()).stdout
            addrs, _, routes = out.partition("===")
            addrs = "\n".join(f"0: {ln.replace(' ', '_', ln.count(' ') - 2)}" for ln in addrs.splitlines() if ln.strip())
        else:
            addrs = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
            routes = subprocess.run(["ip", "-4", "route"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        pass
    if not addrs:   # macOS/sem `ip`: usa o IP da interface principal com /24
        from core.fingerprint import local_ip
        ip = local_ip()
        if ip:
            addrs = f"0: en0 inet {ip}/24"
    from core.fingerprint import arp_table
    neigh = "\n".join(f"{ip} lladdr {mac}" for ip, mac in arp_table().items())
    nets = candidate_networks(addrs, routes, neigh)
    # rotas de host (/32) e multicast não interessam; mantém o que tem endereço
    return [n for n in nets if not n["cidr"].startswith(("224.", "239.", "255."))]


# ---------------------------------------------------------------------- conexões compartilhadas (app)
class GatewayPool:
    """Uma conexão SSH por servidor cadastrado, reaproveitada por scanner, navegador, gravador e lote."""

    def __init__(self):
        self._gws: dict[int, SshGateway] = {}
        self._lock = threading.Lock()

    @staticmethod
    def server_from_row(row) -> Server:
        """row = (id, name, host, port, user, auth, key_path) do banco; senha vem do cofre."""
        from core import vault
        sid, name, host, port, user, auth, key_path = row
        return Server(host=host, port=int(port), user=user, auth=auth, key_path=key_path or DEFAULT_KEY,
                      name=name, password=vault.get_password(sid) if auth == "password" else None)

    def get(self, row) -> SshGateway:
        """Conecta (se preciso) e devolve o gateway do servidor. Bloqueia: chame fora da thread da tela."""
        sid = row[0]
        with self._lock:
            gw = self._gws.get(sid)
            if gw is not None and gw.client is not None:
                t = gw.client.get_transport()
                if t is not None and t.is_active():
                    return gw
                gw.close()
            gw = SshGateway(self.server_from_row(row)).connect()
            self._gws[sid] = gw
            return gw

    def proxy(self, row) -> str:
        return self.get(row).start_socks()

    def close(self, sid=None):
        with self._lock:
            for k in ([sid] if sid is not None else list(self._gws)):
                gw = self._gws.pop(k, None)
                if gw:
                    gw.close()


POOL = GatewayPool()


# (ferramenta, nível, para que serve, como instalar em Debian/Ubuntu)
CHECKUP_TOOLS = [
    ("bash", "obrigatório", "teste de portas do scanner", "apt install bash"),
    ("timeout", "obrigatório", "teste de portas do scanner", "apt install coreutils"),
    ("xargs", "obrigatório", "teste de portas em paralelo", "apt install findutils"),
    ("ip", "recomendado", "descobrir redes (interfaces, rotas, vizinhos) e MAC dos equipamentos",
     "apt install iproute2"),
    ("ss", "recomendado", "descobrir as redes das CPEs pelas conexões no ACS", "apt install iproute2"),
    ("snmpget", "opcional", "identificar modelo por SNMP", "apt install snmp"),
]


def checkup_summary(items: list[dict]) -> tuple[str, str]:
    """Texto do checkup para a tela e o nível geral: 'ok' | 'aviso' | 'erro'."""
    lines, worst = [], "ok"
    for it in items:
        if it["ok"]:
            lines.append(f"✔ {it['item']}")
            continue
        mark = "✖" if it["level"] == "obrigatório" else "⚠"
        worst = "erro" if it["level"] == "obrigatório" else (worst if worst == "erro" else "aviso")
        lines.append(f"{mark} {it['item']} ({it['level']}) — falta para: {it['used_for']}."
                     + (f" Instalar: {it['fix']}" if it["fix"] else ""))
    return "\n".join(lines), worst


def auto_selection(nets: list[dict], budget: int = 4096) -> list[dict]:
    """Escolhe quais redes já vêm marcadas na varredura automática, sem passar de `budget` IPs.

    Prioridade: redes com sinal de vida (ARP / CPEs no ACS) e redes de interface; depois as demais
    redes pequenas, na ordem. Redes grandes (> /22) nunca entram sozinhas.
    """
    def score(n):
        return (0 if n.get("alive") else 1, 0 if "interface" in n["source"] else 1)

    used = 0
    chosen = set()
    for n in sorted((n for n in nets if n["suggested"]), key=score):
        if used + n["hosts"] <= budget or (not chosen and n.get("alive")):
            chosen.add(n["cidr"])
            used += n["hosts"]
    out = []
    for n in nets:
        n = dict(n)
        n["checked"] = n["cidr"] in chosen
        out.append(n)
    return out
