"""Servidor SSH falso para testes: aceita senha, encaminha conexões (direct-tcpip) e roda comandos
com o bash local (usado pelo teste de portas remoto; sem bash o teste correspondente é pulado)."""
import select
import shutil
import socket
import subprocess
import threading

import paramiko

USER, PASSWORD = "support", "teste123"


class _Iface(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner
        self.dest = {}      # chanid -> (host, porta)
        self.cmd = {}       # chanid -> comando

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == (USER, PASSWORD) else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.dest[chanid] = destination
        self.owner.forwards.append(destination)
        return paramiko.OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        cmd = command.decode() if isinstance(command, bytes) else command
        self.cmd[channel.get_id()] = cmd
        self.owner.commands.append(cmd)
        threading.Thread(target=self.owner._run_exec, args=(channel, cmd), daemon=True).start()
        return True


class FakeSSH:
    def __init__(self):
        self.host_key = paramiko.RSAKey.generate(2048)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(10)
        self.port = self.sock.getsockname()[1]
        self.forwards = []
        self.commands = []
        self._stop = threading.Event()

    def __enter__(self):
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self.sock.close()

    def _accept(self):
        while not self._stop.is_set():
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        t = paramiko.Transport(client)
        t.add_server_key(self.host_key)
        iface = _Iface(self)
        try:
            t.start_server(server=iface)
        except Exception:  # noqa: BLE001
            return
        while t.is_active() and not self._stop.is_set():
            chan = t.accept(1)
            if chan is None:
                continue
            dest = iface.dest.get(chan.get_id())
            if dest is not None:
                threading.Thread(target=self._forward, args=(chan, dest), daemon=True).start()
            # canais "session" são tratados em _run_exec (chamado no pedido de exec)

    def _run_exec(self, chan, cmd):
        bash = shutil.which("bash")
        out = subprocess.run([bash, "-c", cmd], capture_output=True, timeout=120).stdout if bash else b""
        try:
            chan.sendall(out)
            chan.send_exit_status(0)
        finally:
            chan.close()

    @staticmethod
    def _forward(chan, dest):
        try:
            up = socket.create_connection(dest, timeout=5)
        except OSError:
            chan.close()
            return
        try:
            while True:
                r, _, _ = select.select([up, chan], [], [], 1)
                if up in r:
                    d = up.recv(65536)
                    if not d:
                        break
                    chan.sendall(d)
                if chan in r:
                    d = chan.recv(65536)
                    if not d:
                        break
                    up.sendall(d)
        except Exception:  # noqa: BLE001
            pass
        finally:
            up.close()
            chan.close()
