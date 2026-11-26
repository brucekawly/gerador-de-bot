"""Scanner de rede com identificação de fabricante/modelo/firmware SEM login.

Fontes (todas só leitura):
  1. HTTP: título da página, header Server, realm do login HTTP, textos da página
     de login (segue redirecionamentos simples, inclusive meta refresh / JS).
  2. UPnP/SSDP: XML de descrição (manufacturer, modelName, modelNumber, às vezes firmware).
  3. SNMP v1 sysDescr (community configurável, padrão 'public').
  4. MAC pela tabela ARP do Windows (só funciona na mesma rede local).

Firmware quase sempre só aparece depois do login; aqui ele vem quando o equipamento expõe.
"""
from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from core.platform_utils import IS_WINDOWS, no_window_flags

# --------------------------------------------------------------------------- assinaturas
# (fabricante, padrão que identifica o fabricante, padrão do modelo)
VENDOR_RULES: list[tuple[str, str, str]] = [
    ("Huawei", r"huawei|\bHG8\d{3}|\bEG8\d{3}|\bHS8\d{3}|\bWA8\d{3}|echolife",
     r"\b((?:HG|EG|HS|HN|WA|K)\d{3,4}[A-Z0-9-]*)\b"),
    ("ZTE", r"\bzte\b|zxhn|\bF6\d{2}[A-Z]?\b|\bF670L?\b|\bH\d{3}[A-Z]?\b.*zte",
     r"\b(ZXHN\s?[A-Z]?\d{3,4}[A-Z0-9]*|F\d{3,4}[A-Z]{0,2}\d?(?:V\d(?:\.\d)?)?)\b"),
    ("TP-Link", r"tp-?link|archer|\bdeco\b|mercusys|\bEX\d{3}\b|\bXC\d{3}\b|\bXX\d{3}\b",
     r"\b(Archer\s?[A-Z]{1,3}\d{1,4}[A-Za-z]*(?:\s?Pro)?|EX\d{3}[A-Z]?|XC\d{3}[A-Z]?|XX\d{3}[A-Z]?(?:-G\d)?|"
     r"Deco\s?[A-Z]?\d+|TL-[A-Z0-9]+|MR\d{2,3}[A-Z]?)\b"),
    ("Mercusys", r"mercusys", r"\b(M[RW]\d{2,3}[A-Z]{0,2})\b"),
    ("Intelbras", r"intelbras|\bwiseFi\b|\bACtion\s?RG|\bWiFiber\b|\bTwiBi\b|\bW5-\d",
     r"\b(WiFiber\s?[A-Z0-9 ]{2,20}?|ACtion\s?R[GF]\s?\d+|RG\s?\d{3,4}|W[56]-\d{3,4}[A-Z]*|GF\s?\d{3,4}|TwiBi\s?\w+)\b"),
    ("Fiberhome", r"fiberhome|\bAN55\d{2}|\bHG6\d{3}|\bSR1\d{3}",
     r"\b(AN55\d{2}-[A-Z0-9]+|HG6\d{3}[A-Z0-9]*|SR1\d{3}[A-Z0-9]*)\b"),
    ("Nokia", r"nokia|alcatel|\bALCL\b|\bG-\d{3,4}[A-Z]+", r"\b(G-\d{3,4}[A-Z]+(?:-[A-Z])?|XS-\d{4}[A-Z-]*)\b"),
    ("Ruijie", r"ruijie|reyee", r"\b((?:RG-)?EW\d{3,4}[A-Z]*(?:\s?PRO)?|RG-[A-Z0-9-]+)\b"),
    ("Zyxel", r"zyxel", r"\b((?:PMG|VMG|EX|AX|NBG|VMG)\d{4}[A-Z0-9-]*)\b"),
    ("D-Link", r"d-?link", r"\b((?:DIR|DWR|DSL|COVR)-?\d{3,4}[A-Z0-9]*)\b"),
    ("Tenda", r"\btenda\b", r"\b((?:AC|F|N|TX|HG|nova\s?MW)\d{1,4}[A-Z]?(?:V\d)?)\b"),
    ("MikroTik", r"mikrotik|routeros", r"\b(RB\d{3,4}[A-Za-z0-9+-]*|hAP\s?\w+|CCR\d{4}[\w-]*|hEX\s?\w*)(?![\w+])"),
    ("Ubiquiti", r"ubiquiti|unifi|airos|edgeos", r"\b(UAP-[\w-]+|U6-[\w-]+|ER-[\w-]+|NanoStation\s?\w+|LiteBeam\s?\w+)\b"),
    ("Parks", r"\bparks\b|fiberlink", r"\b(Fiberlink\s?\d+[A-Z]*)\b"),
    ("V-SOL", r"v-?sol\b", r"\b(V\d{4}[A-Z0-9]*|HG\d{3,4}[A-Z0-9]*)\b"),
    ("Datacom", r"datacom|\bDM9\d{2}", r"\b(DM\d{3,4}[A-Z0-9-]*)\b"),
    ("Multilaser", r"multilaser|\bRE\d{3}\b", r"\b(RE\d{3}[A-Z]?)\b"),
    ("Cianet", r"cianet", r"\b(ONU\s?[A-Z0-9-]+|GW\d+[A-Z]*)\b"),
]

FIRMWARE_RE = re.compile(
    r"(?:firmware|software|sw|fw|vers[aã]o(?:\s+do\s+(?:firmware|software))?)\s*(?:version|ver\.?)?\s*[:=]?\s*"
    r"\"?\s*([vV]?\d{1,4}(?:\.\d{1,5}){1,5}(?:[\w.\-()]{0,24})?)",
    re.IGNORECASE,
)
# Versões que não vêm depois da palavra "firmware/versão"
FIRMWARE_EXTRA_RES = [
    re.compile(r"\b(V\d{1,3}R\d{3}C\d{2,3}(?:S\d{2,4})?(?:SPC\d{3})?[A-Z0-9]*)\b"),  # Huawei V3R017C10S125
    re.compile(r"RouterOS\s+v?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE),                  # MikroTik
    re.compile(r"\b(V\d\.\d(?:\.\d+)?P\d+T\d+)\b"),                                  # ZTE V6.0.10P3T7
]
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_REFRESH_RE = re.compile(r"<meta[^>]+http-equiv=[\"']?refresh[^>]+url=([^\"'>\s;]+)", re.IGNORECASE)
JS_REDIRECT_RE = re.compile(r"(?:window\.|document\.|top\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass
class Fingerprint:
    ip: str
    port: int
    open: bool = False
    scheme: str = "http"
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    title: str = ""
    server: str = ""
    realm: str = ""
    mac: str = ""
    sources: list[str] = field(default_factory=list)   # de onde veio a identificação
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        parts = [p for p in (self.vendor, self.model) if p]
        return " ".join(parts) or (self.title or self.server or "Não identificado")


# --------------------------------------------------------------------------- sondas
def check_port(ip: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# Variáveis que muitas páginas de login trazem no JavaScript (TP-Link Aginet: modelName="EX141";
# outros: productName, device_model, softwareVersion...). O valor precisa ter um dígito para não
# pegar textos genéricos como model="Router".
_JS_MODEL_RE = re.compile(r"""\b(?:model_?name|product_?name|device_?model|dev_?model|model_?num(?:ber)?|"""
                          r"""board_?name|modelo)\s*[=:]\s*["']([A-Za-z0-9][\w .+/\-]{1,39})["']""", re.I)
_JS_DESC_RE = re.compile(r"""\b(?:model_?desc|product_?desc|device_?desc)\s*[=:]\s*["']([^"'<>]{3,80})["']""", re.I)
_JS_FW_RE = re.compile(r"""\b(?:soft(?:ware)?_?ver(?:sion)?|firmware_?ver(?:sion)?|fw_?ver(?:sion)?|"""
                       r"""sw_?ver(?:sion)?|curr_?ver(?:sion)?)\s*[=:]\s*["']([^"'<>]{3,48})["']""", re.I)


def extract_js_info(page: str, fp: "Fingerprint") -> None:
    for rx, key in ((_JS_MODEL_RE, "js_model"), (_JS_FW_RE, "js_fw")):
        for m in rx.finditer(page or ""):
            value = m.group(1).strip()
            if re.search(r"\d", value) and "{" not in value and "$" not in value:
                fp.raw[key] = value
                break
    m = _JS_DESC_RE.search(page or "")
    if m:
        fp.raw["js_desc"] = m.group(1).strip()


def _direct_dial(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, port), timeout=timeout)


def _http_get(ip: str, port: int, path: str, timeout: float, tls: bool, dial=None) -> tuple[int, dict, str]:
    """GET bruto (aceita 401 e certificado inválido). Retorna (status, headers, corpo<=128KB).

    `dial(ip, porta, timeout)` permite sair por outro caminho (ex.: túnel pelo servidor do cliente).
    """
    sock = (dial or _direct_dial)(ip, port, timeout)
    try:
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=ip)
        req = (f"GET {path} HTTP/1.1\r\nHost: {ip}:{port}\r\nUser-Agent: Mozilla/5.0 GeradorDeBOT-Scanner\r\n"
               "Accept: text/html,*/*\r\nConnection: close\r\n\r\n")
        sock.sendall(req.encode())
        data = b""
        deadline = time.monotonic() + timeout * 2
        while len(data) < 131072 and time.monotonic() < deadline:
            chunk = sock.recv(16384)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1", "replace").split("\r\n")
    m = re.match(r"HTTP/\d\.\d\s+(\d+)", lines[0] if lines else "")
    status = int(m.group(1)) if m else 0
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        headers[k.strip().lower()] = v.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = _dechunk(body)
    charset = re.search(r"charset=([\w-]+)", headers.get("content-type", ""))
    try:
        text = body.decode(charset.group(1) if charset else "utf-8", "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    return status, headers, text


def _dechunk(body: bytes) -> bytes:
    out, rest = b"", body
    while rest:
        size_line, _, rest = rest.partition(b"\r\n")
        try:
            size = int(size_line.split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out += rest[:size]
        rest = rest[size + 2:]
    return out


def probe_http(fp: Fingerprint, timeout: float = 2.5, dial=None) -> None:
    tls = fp.port in (443, 8443)
    fp.scheme = "https" if tls else "http"
    path, texts = "/", []
    for _ in range(4):  # até 3 redirecionamentos
        try:
            status, headers, body = _http_get(fp.ip, fp.port, path, timeout, tls, dial)
        except ssl.SSLError:
            if tls:
                return
            tls, fp.scheme = True, "https"
            continue
        except OSError:
            return
        fp.server = fp.server or headers.get("server", "")
        realm = re.search(r'realm="?([^",]+)', headers.get("www-authenticate", ""), re.IGNORECASE)
        if realm:
            fp.realm = realm.group(1)
        t = TITLE_RE.search(body)
        if t and not fp.title:
            fp.title = re.sub(r"\s+", " ", html.unescape(t.group(1))).strip()[:120]
        texts.append(body)
        nxt = headers.get("location") if status in (301, 302, 303, 307, 308) else None
        if not nxt:
            m = META_REFRESH_RE.search(body) or (JS_REDIRECT_RE.search(body) if len(body) < 4000 else None)
            nxt = m.group(1) if m else None
        if not nxt:
            break
        u = urllib.parse.urlparse(urllib.parse.urljoin(f"{fp.scheme}://{fp.ip}:{fp.port}{path}", nxt))
        if u.hostname and u.hostname != fp.ip:
            fp.raw["redirect_to"] = u.geturl()       # manda para outro site (ex.: sistema web, não equipamento)
            break
        if u.scheme == "https":
            tls, fp.scheme = True, "https"
        new_path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        if new_path == path:
            break
        path = new_path
    full = "\n".join(texts)
    no_code = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", full, flags=re.S | re.I)
    fp.raw["http_visible"] = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", no_code))).strip()[:4000]
    fp.raw["http_html"] = full[:120000]
    fp.raw["http_path"] = path
    extract_js_info(full, fp)          # modelName="EX141" etc. em scripts (página inteira, antes de cortar)
    run_vendor_probes(fp, tls, timeout, dial)


# --------------------------------------------------------------------------- sondas por fabricante
# Endpoints que a própria página de login chama SEM autenticação. Para adicionar um
# fabricante novo: marcador (regex no HTML da página de login) + lista de GETs + parser.
def _json(body: str) -> dict:
    import json
    try:
        start = body.find("{")
        return json.loads(body[start:]) if start >= 0 else {}
    except ValueError:
        return {}


def _fiberhome_device(body: str, fp: Fingerprint):
    model = _json(body).get("ModelName", "")
    if model:
        fp.raw["probe_vendor"], fp.raw["probe_model"] = "Fiberhome", model


def _fiberhome_operator(body: str, fp: Fingerprint):
    op = _json(body).get("operator_name", "")
    if op:
        fp.raw["probe_operator"] = op


VENDOR_PROBES = [
    {
        "name": "Fiberhome (cgi-bin/ajax)",
        "marker": r"cgi-bin/ajax|fh_access|fhencrypt|get_device_name",
        "requests": [
            ("/cgi-bin/ajax?ajaxmethod=get_device_name", _fiberhome_device),
            ("/cgi-bin/ajax?ajaxmethod=get_operator", _fiberhome_operator),
        ],
    },
]


def run_vendor_probes(fp: Fingerprint, tls: bool, timeout: float, dial=None) -> None:
    page = fp.raw.get("http_html", "")
    for probe in VENDOR_PROBES:
        if not re.search(probe["marker"], page, re.IGNORECASE):
            continue
        extra_html = ""
        if probe["name"].startswith("Fiberhome") and "cgi-bin/ajax" not in page:
            # o marcador pode estar num .js; confere o xhr.js antes de chamar
            try:
                _, _, extra_html = _http_get(fp.ip, fp.port, "/js/xhr.js", timeout, tls, dial)
            except OSError:
                pass
            if "cgi-bin/ajax" not in extra_html and "get_device_name" not in page:
                continue
        for path, parser in probe["requests"]:
            try:
                status, _, body = _http_get(fp.ip, fp.port, path, timeout, tls, dial)
            except OSError:
                continue
            if status == 200:
                parser(body, fp)
        if fp.raw.get("probe_vendor"):
            fp.raw["probe_name"] = probe["name"]
            return


def ssdp_discover(timeout: float = 3.0) -> dict[str, str]:
    """M-SEARCH multicast: retorna {ip: url_do_xml}."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    found: dict[str, str] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(0.5)
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, (ip, _) = s.recvfrom(4096)
            except socket.timeout:
                continue
            loc = re.search(rb"(?im)^location:\s*(\S+)", data)
            if loc and ip not in found:
                found[ip] = loc.group(1).decode("latin-1")
        s.close()
    except OSError:
        pass
    return found


def ssdp_unicast(ip: str, timeout: float = 1.5) -> str | None:
    msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: {ip}:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\n"
           "ST: upnp:rootdevice\r\n\r\n").encode()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(msg, (ip, 1900))
            data, _ = s.recvfrom(4096)
            loc = re.search(rb"(?im)^location:\s*(\S+)", data)
            return loc.group(1).decode("latin-1") if loc else None
    except OSError:
        return None


def probe_upnp(fp: Fingerprint, location: str, timeout: float = 2.5) -> None:
    u = urllib.parse.urlparse(location)
    if not u.hostname or u.hostname != fp.ip:
        return
    try:
        _, _, body = _http_get(u.hostname, u.port or 80, (u.path or "/") + (f"?{u.query}" if u.query else ""),
                               timeout, u.scheme == "https")
        root = ET.fromstring(body[body.find("<"):].encode())
    except (OSError, ET.ParseError, ValueError):
        return
    vals: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("manufacturer", "modelName", "modelNumber", "modelDescription", "friendlyName",
                   "softwareVersion", "firmwareVersion", "hardwareVersion") and (el.text or "").strip():
            vals.setdefault(tag, el.text.strip())
    if vals:
        fp.raw["upnp"] = " | ".join(f"{k}={v}" for k, v in vals.items())
        fp.raw["upnp_manufacturer"] = vals.get("manufacturer", "")
        model = vals.get("modelName", "")
        num = vals.get("modelNumber", "")
        if num and num.lower() not in model.lower():
            model = f"{model} {num}".strip()
        fp.raw["upnp_model"] = model
        fp.raw["upnp_fw"] = vals.get("softwareVersion") or vals.get("firmwareVersion", "")


# --- SNMP v1 GET sysDescr.0 (BER feito à mão, sem dependências)
def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(value)) + value


SYSDESCR_OID = bytes([0x2B, 6, 1, 2, 1, 1, 1, 0])  # 1.3.6.1.2.1.1.1.0


def snmp_sysdescr(ip: str, community: str = "public", timeout: float = 1.2) -> str | None:
    varbind = _tlv(0x30, _tlv(0x06, SYSDESCR_OID) + b"\x05\x00")
    pdu = _tlv(0xA0, _tlv(0x02, b"\x01") + _tlv(0x02, b"\x00") + _tlv(0x02, b"\x00") + _tlv(0x30, varbind))
    packet = _tlv(0x30, _tlv(0x02, b"\x00") + _tlv(0x04, community.encode()) + pdu)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (ip, 161))
            data, _ = s.recvfrom(8192)
    except OSError:
        return None
    i = data.find(SYSDESCR_OID)
    if i < 0:
        return None
    j = i + len(SYSDESCR_OID)
    if j >= len(data) or data[j] != 0x04:
        return None
    ln = data[j + 1]
    k = j + 2
    if ln & 0x80:
        nb = ln & 0x7F
        ln = int.from_bytes(data[k:k + nb], "big")
        k += nb
    return data[k:k + ln].decode("utf-8", "replace").strip() or None


def arp_table() -> dict[str, str]:
    """{ip: mac} pela tabela ARP (Windows: `arp -a`; Linux: /proc/net/arp; macOS: `arp -an`)."""
    out = ""
    if os.path.exists("/proc/net/arp"):
        try:
            with open("/proc/net/arp", encoding="ascii", errors="ignore") as f:
                out = "\n".join(" ".join(ln.split()[:4:3]) for ln in f.read().splitlines()[1:])  # "ip mac"
        except OSError:
            out = ""
    if not out:
        try:
            cmd = ["arp", "-a"] if IS_WINDOWS else ["arp", "-an"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                                 creationflags=no_window_flags()).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
    table = {}
    # Windows: "192.168.1.1   54-ba-d9-..."  | Linux/mac: "? (192.168.1.1) at 54:ba:d9:..." | /proc: "ip mac"
    for m in re.finditer(r"\(?(\d+\.\d+\.\d+\.\d+)\)?\s+(?:at\s+)?([0-9a-fA-F]{1,2}(?:[-:][0-9a-fA-F]{1,2}){5})", out):
        mac = ":".join(x.zfill(2) for x in re.split("[-:]", m.group(2))).upper()
        if mac not in ("FF:FF:FF:FF:FF:FF",) and not mac.startswith("01:00:5E"):
            table[m.group(1)] = mac
    return table


# --------------------------------------------------------------------------- identificação
_SERVER_TITLES = re.compile(r"welcome to nginx|apache2? .*default page|it works!|iis windows server|"
                            r"test page for the|welcome to centos|plesk|cpanel|grafana|zabbix|proxmox|portainer",
                            re.IGNORECASE)
_SERVER_HEADER_OS = re.compile(r"\((ubuntu|debian|centos|red hat|rocky|almalinux|fedora|win32|win64)\)",
                               re.IGNORECASE)


def is_system(fp: "Fingerprint") -> bool:
    """True quando o IP é um servidor/sistema (não um equipamento para automatizar)."""
    return fp.raw.get("kind") == "sistema"


def _looks_like_server(fp: "Fingerprint") -> bool:
    """Marca servidores e sistemas web (ex.: o próprio OLT Cloud) para não confundir com equipamento."""
    redirect = fp.raw.get("redirect_to", "")
    host = (urllib.parse.urlparse(redirect).hostname or "").lower() if redirect else ""
    reason = ""
    if "oltcloud" in host or re.search(r"\bOLT Cloud\b", fp.title or ""):
        fp.vendor, fp.model, reason = "OLT Cloud", "sistema (não é equipamento)", f"redireciona para {host or 'OLT Cloud'}"
    elif _SERVER_TITLES.search(fp.title or "") or _SERVER_HEADER_OS.search(fp.server or ""):
        reason = "servidor web (" + (fp.title or fp.server) + ")"
    if not reason:
        # outro site (ex.: tplinkwifi.net do TP-Link) pode ser o próprio roteador: só anota
        if host and not re.fullmatch(r"[\d.]+", host):
            fp.raw["note"] = f"redireciona para {host}"
        return False
    fp.raw["kind"] = "sistema"
    fp.raw["note"] = reason
    if not fp.vendor:
        fp.vendor, fp.model = "Servidor", "não é equipamento"
    _add_source(fp, "Web")
    return True


def identify(fp: Fingerprint) -> None:
    """Preenche vendor/model/firmware a partir das fontes coletadas.

    Prioridade: sonda do fabricante > UPnP > SNMP > título/realm/Server > texto visível > HTML/JS.
    Procurar em camadas evita falso positivo (ex.: lista de operadoras dentro do JavaScript).
    """
    upnp_vendor = fp.raw.get("upnp_manufacturer", "")
    upnp_model = fp.raw.get("upnp_model", "")
    snmp = fp.raw.get("snmp", "")
    visible = fp.raw.get("http_visible", "") or fp.raw.get("http", "")
    page_html = fp.raw.get("http_html", "")
    if _looks_like_server(fp):
        return
    if fp.raw.get("probe_operator"):
        fp.raw["note"] = f"Operadora: {fp.raw['probe_operator']}"

    if fp.raw.get("probe_vendor"):
        fp.vendor = fp.raw["probe_vendor"]
        fp.model = fp.raw.get("probe_model", "")
        _add_source(fp, "Sonda " + fp.vendor)
    else:
        tiers = [
            "\n".join(x for x in (upnp_vendor, upnp_model, fp.raw.get("upnp", ""), snmp,
                                  fp.title, fp.realm, fp.server, fp.raw.get("js_model", ""),
                                  fp.raw.get("js_desc", "")) if x),
            visible,
            page_html,
        ]
        rule = None
        for corpus in tiers:
            if not corpus:
                continue
            rule = next((r for r in VENDOR_RULES if re.search(r[1], corpus, re.IGNORECASE)), None)
            if rule:
                break
        if rule:
            vendor, _, model_pat = rule
            fp.vendor = vendor
            for src_name, src in (("UPnP", upnp_model), ("Web", fp.raw.get("js_model", "")), ("SNMP", snmp),
                                  ("Web", fp.title), ("Web", fp.realm), ("Web", visible), ("Web", page_html)):
                m = re.search(model_pat, src or "", re.IGNORECASE)
                if m:
                    fp.model = m.group(1).strip()
                    _add_source(fp, src_name)
                    break

    # modelo declarado no script da página de login (ex.: modelName="EX141") é o mais confiável da Web
    if fp.raw.get("js_model") and (not fp.model or fp.model.lower() in fp.raw["js_model"].lower()):
        fp.model = fp.raw["js_model"]
        _add_source(fp, "Web")
    if fp.raw.get("js_desc") and not fp.raw.get("note"):
        fp.raw["note"] = fp.raw["js_desc"]

    if not fp.vendor and upnp_vendor:
        fp.vendor = upnp_vendor.split(",")[0].strip()[:30]
    if not fp.model and upnp_model:
        fp.model = upnp_model[:40]
        _add_source(fp, "UPnP")
    if fp.vendor and not fp.sources:
        _add_source(fp, "UPnP" if upnp_vendor else ("SNMP" if snmp else "Web"))

    fw = fp.raw.get("upnp_fw", "")
    if fw:
        fp.firmware = fw
        _add_source(fp, "UPnP")
    elif fp.raw.get("js_fw"):
        fp.firmware = fp.raw["js_fw"]
        _add_source(fp, "Web")
    else:
        for rx in (FIRMWARE_RE, *FIRMWARE_EXTRA_RES):
            hit = next(((n, m) for n, src in (("SNMP", snmp), ("Web", visible), ("Web", page_html))
                        if (m := rx.search(src or ""))), None)
            if hit:
                fp.firmware = hit[1].group(1).rstrip(".-(")
                _add_source(fp, hit[0])
                break


def _add_source(fp: Fingerprint, name: str) -> None:
    if name not in fp.sources:
        fp.sources.append(name)


def fingerprint_host(ip: str, port: int, *, ssdp_location: str | None = None, snmp_community: str | None = "public",
                     use_upnp: bool = True, timeout: float = 2.5, gateway=None) -> Fingerprint:
    """Identifica um equipamento. Com `gateway` (servidor do cliente), tudo passa pelo servidor:
    HTTP pelo túnel e SNMP rodando no servidor; UPnP fica de fora (UDP não passa no túnel)."""
    fp = Fingerprint(ip=ip, port=int(port), open=True)
    dial = gateway.dial if gateway is not None else None
    probe_http(fp, timeout + (3 if gateway is not None else 0), dial)
    if use_upnp and gateway is None:
        loc = ssdp_location or ssdp_unicast(ip)
        if loc:
            probe_upnp(fp, loc, timeout)
    if snmp_community:
        desc = (gateway.snmp_sysdescr(ip, snmp_community) if gateway is not None
                else snmp_sysdescr(ip, snmp_community))
        if desc:
            fp.raw["snmp"] = desc
    identify(fp)
    return fp


def match_template(fp: Fingerprint, templates: list[tuple]) -> tuple[tuple | None, str]:
    """Sugere o template (id, vendor, model, firmware, ...) que melhor bate com o equipamento.

    Retorna (template, confianca) com confianca em: 'modelo' (fabricante+modelo batem),
    'fabricante' (só o fabricante bate; conferir) ou '' (nenhum).
    """
    if not fp.vendor:
        return None, ""

    def norm(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    best, best_score = None, 0
    for t in templates:
        t_vendor, t_model, t_fw = norm(t[1]), norm(t[2]), norm(t[3])
        if not t_vendor or (t_vendor not in norm(fp.vendor) and norm(fp.vendor) not in t_vendor):
            continue
        score = 1
        if fp.model and t_model and (t_model in norm(fp.model) or norm(fp.model) in t_model):
            score += 2
        if fp.firmware and t_fw and t_fw in norm(fp.firmware):
            score += 1
        if score > best_score:
            best, best_score = t, score
    if best is None:
        return None, ""
    return best, ("modelo" if best_score >= 3 else "fabricante")


# --------------------------------------------------------------------------- varredura
def expand_range(start: str, end: str, limit: int = 4096) -> list[str]:
    a, b = int(ipaddress.IPv4Address(start)), int(ipaddress.IPv4Address(end))
    if a > b:
        raise ValueError("O IP inicial é maior que o IP final.")
    if b - a + 1 > limit:
        raise ValueError(f"Faixa muito grande ({b - a + 1} IPs). Máximo: {limit}.")
    return [str(ipaddress.IPv4Address(i)) for i in range(a, b + 1)]


def local_ip() -> str:
    """IP deste computador na rede (não envia nada)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def _safe_fingerprint(ip, port, **kw) -> Fingerprint:
    """Identificação que nunca derruba a varredura: erro num equipamento vira só uma nota."""
    try:
        return fingerprint_host(ip, port, **kw)
    except Exception as e:  # noqa: BLE001
        fp = Fingerprint(ip=ip, port=int(port), open=True)
        fp.raw["note"] = f"identificação falhou: {e.__class__.__name__}"
        return fp


def scan(ips: list[str], ports: list[int], *, identify_models: bool = True, snmp_community: str | None = "public",
         workers: int = 64, connect_timeout: float = 1.0, retry_timeout: float = 3.0,
         include_silent: bool = True,
         progress: Callable[[int, int, Fingerprint | None], None] | None = None,
         stop: threading.Event | None = None, gateway=None) -> list[Fingerprint]:
    """Varre ips×portas. Para cada IP usa a primeira porta aberta (na ordem dada).

    1ª passada rápida em todos os IPs; 2ª passada, com timeout maior, só nos IPs que a
    tabela ARP mostra que existem (equipamento lento não some da lista).
    Com include_silent, IPs que estão na rede (ARP) mas sem web nas portas voltam com open=False.
    """
    stop = stop or threading.Event()
    progress = progress or (lambda *_: None)
    total = len(ips)
    if gateway is not None:
        return _scan_via_gateway(ips, ports, gateway, identify_models=identify_models,
                                 snmp_community=snmp_community, include_silent=include_silent,
                                 connect_timeout=max(connect_timeout, 1.5), progress=progress, stop=stop)
    ssdp: dict[str, str] = {}
    ssdp_thread = None
    if identify_models:
        def _ssdp():
            try:
                ssdp.update(ssdp_discover(3.0))
            except Exception:  # noqa: BLE001
                pass
        ssdp_thread = threading.Thread(target=_ssdp, daemon=True)
        ssdp_thread.start()

    def first_open(ip, timeout):
        for p in ports:
            if stop.is_set():
                return None
            if check_port(ip, p, timeout):
                return p
        return None

    alive: dict[str, int] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(first_open, ip, connect_timeout): ip for ip in ips}
        for f in as_completed(futs):
            done += 1
            try:
                p = f.result()
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                alive[futs[f]] = p
            progress(done, total, None)
            if stop.is_set():
                break

    in_range = set(ips)
    macs = {ip: mac for ip, mac in arp_table().items() if ip in in_range}
    retry = [ip for ip in macs if ip not in alive and ip != local_ip()]
    if retry and not stop.is_set():
        with ThreadPoolExecutor(max_workers=min(workers, 32)) as ex:
            futs = {ex.submit(first_open, ip, retry_timeout): ip for ip in retry}
            for f in as_completed(futs):
                try:
                    p = f.result()
                except Exception:  # noqa: BLE001
                    p = None
                if p is not None:
                    alive[futs[f]] = p

    def ip_key(fp):
        return int(ipaddress.IPv4Address(fp.ip))

    silent = []
    if include_silent:
        me = local_ip()
        for ip, mac in macs.items():
            if ip not in alive and ip != me:
                fp = Fingerprint(ip=ip, port=0, open=False, mac=mac)
                fp.raw["note"] = "na rede, sem interface web nas portas informadas"
                silent.append(fp)

    if not identify_models or stop.is_set():
        res = [Fingerprint(ip, p, open=True, mac=macs.get(ip, "")) for ip, p in alive.items()] + silent
        for fp in res:
            progress(total, total, fp)
        return sorted(res, key=ip_key)

    if ssdp_thread:
        ssdp_thread.join(timeout=4)
    results: list[Fingerprint] = []
    with ThreadPoolExecutor(max_workers=min(workers, 24)) as ex:
        futs = [ex.submit(_safe_fingerprint, ip, p, ssdp_location=ssdp.get(ip), snmp_community=snmp_community)
                for ip, p in alive.items()]
        for f in as_completed(futs):
            fp = f.result()
            fp.mac = macs.get(fp.ip, "") or fp.mac
            results.append(fp)
            progress(total, total, fp)
            if stop.is_set():
                break
    for fp in silent:
        progress(total, total, fp)
    return sorted(results + silent, key=ip_key)


def _scan_via_gateway(ips, ports, gateway, *, identify_models, snmp_community, include_silent,
                      connect_timeout, progress, stop) -> list[Fingerprint]:
    """Varredura rodando NO servidor do cliente (testes TCP lá) + identificação pelo túnel."""
    try:
        own = gateway.own_ips()                  # o próprio servidor não é equipamento do cliente
    except Exception:  # noqa: BLE001
        own = set()
    ips = [ip for ip in ips if ip not in own]
    total = len(ips)
    alive: dict[str, int] = {}
    chunk = 256
    for i in range(0, total, chunk):          # em blocos, para mostrar progresso e poder parar
        if stop.is_set():
            break
        alive.update(gateway.tcp_scan(ips[i:i + chunk], ports, timeout=connect_timeout))
        progress(min(i + chunk, total), total, None)
    in_range = set(ips)
    try:
        macs = {ip: mac for ip, mac in gateway.arp().items() if ip in in_range}
    except Exception:  # noqa: BLE001
        macs = {}

    def ip_key(fp):
        return int(ipaddress.IPv4Address(fp.ip))

    silent = []
    if include_silent:
        for ip, mac in macs.items():
            if ip not in alive:
                fp = Fingerprint(ip=ip, port=0, open=False, mac=mac)
                fp.raw["note"] = "na rede, sem interface web nas portas informadas"
                silent.append(fp)
    if not identify_models or stop.is_set():
        res = [Fingerprint(ip, p, open=True, mac=macs.get(ip, "")) for ip, p in alive.items()] + silent
        for fp in res:
            progress(total, total, fp)
        return sorted(res, key=ip_key)

    results: list[Fingerprint] = []
    with ThreadPoolExecutor(max_workers=8) as ex:   # poucos em paralelo: tudo passa por uma conexão SSH
        futs = [ex.submit(_safe_fingerprint, ip, p, snmp_community=snmp_community, gateway=gateway)
                for ip, p in alive.items()]
        for f in as_completed(futs):
            fp = f.result()
            fp.mac = macs.get(fp.ip, "") or fp.mac
            results.append(fp)
            progress(total, total, fp)
            if stop.is_set():
                break
    for fp in silent:
        progress(total, total, fp)
    return sorted(results + silent, key=ip_key)


def parse_targets(text: str, limit: int = 65536) -> list[str]:
    """'192.168.1.0/24, 10.0.0.1-10.0.0.50; 172.16.5.9' -> lista de IPs (sem repetir, em ordem)."""
    out: list[str] = []
    seen = set()
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            net = ipaddress.ip_network(part, strict=False)
            hosts = [str(h) for h in (net.hosts() if net.num_addresses > 2 else net)]
        elif "-" in part:
            a, b = part.split("-", 1)
            if "." not in b:                       # 10.0.0.1-50
                b = a.rsplit(".", 1)[0] + "." + b
            hosts = expand_range(a, b, limit)
        else:
            hosts = [str(ipaddress.IPv4Address(part))]
        for h in hosts:
            if h not in seen:
                seen.add(h)
                out.append(h)
                if len(out) > limit:
                    raise ValueError(f"Mais de {limit} IPs. Diminua as faixas.")
    return out
