"""Roteador falso para testes: login por formulário, login HTTP Basic e página lenta."""
import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

FORM_USER, FORM_PASS = "admin", "1234"
BASIC_USER, BASIC_PASS = "suporte", "basic"

LOGIN_PAGE = """<html><head><title>FakeRouter AX-1000</title></head><body>
<form method=post action=/login><input id=user name=user><input id=pass name=pass type=password>
<button id=go>Entrar</button></form>{err}</body></html>"""
WIFI_PAGE = """<html><body><div id=done>Logado</div>
<form method=post action=/save><input id=ssid name=ssid><button id=save>Salvar</button></form></body></html>"""
SAVED_PAGE = "<html><body><div id=saved>Salvo</div></body></html>"


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.saved: list[dict] = []
        self.logins: list[str] = []
        self.active = 0
        self.max_active = 0


def make_handler(state: _State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, status=200, headers=None):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _cookie_ok(self):
            return "sess=ok" in (self.headers.get("Cookie") or "")

        def do_GET(self):
            with state.lock:
                state.active += 1
                state.max_active = max(state.max_active, state.active)
            try:
                if self.path.startswith("/login"):
                    err = "<p id=err>Senha incorreta</p>" if "err" in self.path else ""
                    self._send(LOGIN_PAGE.format(err=err))
                elif self.path == "/wifi":
                    if self._cookie_ok():
                        time.sleep(0.3)  # segura um pouco para medir concorrência
                        self._send(WIFI_PAGE)
                    else:
                        self._send("", 302, {"Location": "/login"})
                elif self.path == "/basic":
                    expected = base64.b64encode(f"{BASIC_USER}:{BASIC_PASS}".encode()).decode()
                    if self.headers.get("Authorization") == f"Basic {expected}":
                        self._send(WIFI_PAGE)
                    else:
                        self._send("<title>401</title>", 401, {"WWW-Authenticate": 'Basic realm="FakeRouter"'})
                elif self.path == "/slow":
                    time.sleep(5)
                    self._send("<html><body>lento</body></html>")
                else:
                    self._send("<html><head><title>FakeRouter AX-1000</title></head></html>",
                               headers={"Server": "FakeHTTPd/2.1"})
            finally:
                with state.lock:
                    state.active -= 1

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            if self.path == "/login":
                state.logins.append(form.get("user", ""))
                if form.get("user") == FORM_USER and form.get("pass") == FORM_PASS:
                    self._send("", 302, {"Location": "/wifi", "Set-Cookie": "sess=ok; Path=/"})
                else:
                    self._send("", 302, {"Location": "/login?err=1"})
            elif self.path == "/save":
                with state.lock:
                    state.saved.append({"ssid": form.get("ssid", ""), "port": self.server.server_port})
                self._send(SAVED_PAGE)
            else:
                self._send("", 404)
    return H


class FakeRouter:
    def __init__(self):
        self.state = _State()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
