"""Testes do motor e da leitura de planilha.

Rodar:  py -3.12 -m unittest discover -s tests -v
(precisa do Playwright com Chromium instalado; não acessa nenhum roteador real)
"""
import asyncio
import os
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import Error as PwError  # noqa: E402
from playwright.async_api import TimeoutError as PwTimeout  # noqa: E402

from core.automation_engine import (AutomationEngine, ErrorKind, LoginError, RetryPolicy,  # noqa: E402
                                    classify_error, render_script, validate_template)
from core.devices import (Credential, Device, missing_variables, normalize_var_name,  # noqa: E402
                          parse_credentials, parse_defaults, parse_rows, resolve_variables)
from tests.fake_router import BASIC_PASS, BASIC_USER, FakeRouter  # noqa: E402

FORM_TEMPLATE = """await page.goto("http://{{IP}}:{{PORT}}/login")
await page.fill("#user", "{{USERNAME}}")
await page.fill("#pass", "{{PASSWORD}}")
await page.click("#go")
await page.wait_for_selector("#done")
await page.fill("#ssid", "{{SSID}}")
await page.click("#save")
await page.wait_for_selector("#saved")"""


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestDevices(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_var_name("Senha Wi-Fi 5G"), "SENHA_WI_FI_5G")
        self.assertEqual(normalize_var_name("Usuário"), "USUARIO")

    def test_parse_rows_columns_and_credentials(self):
        cols = ["IP", "Porta", "Usuário", "Senha", "SSID", "Senha Wi-Fi"]
        rows = [["10.0.0.1", 8080.0, "admin", "x", "Casa", "12345678"],
                ["10.0.0.2", None, None, None, "Loja", None],
                [None, 80, "a", "b", "c", "d"]]
        devs, warns = parse_rows(cols, rows)
        self.assertEqual(len(devs), 2)
        self.assertEqual(devs[0].port, "8080")
        self.assertEqual(devs[0].credential, Credential("admin", "x"))
        self.assertEqual(devs[0].variables, {"SSID": "Casa", "SENHA_WI_FI": "12345678"})
        self.assertEqual(devs[1].port, "")   # vazio = porta padrão do template
        self.assertIsNone(devs[1].credential)
        self.assertIn("1 linha", warns[0])

    def test_parse_rows_requires_ip(self):
        with self.assertRaises(ValueError):
            parse_rows(["Host Name", "Porta"], [["a", 80]])

    def test_parse_credentials(self):
        creds = parse_credentials("admin:admin\n# comentário\nsuporte;s3:nh@\n\nroot\tcpe")
        self.assertEqual(creds, [Credential("admin", "admin"), Credential("suporte", "s3:nh@"),
                                 Credential("root", "cpe")])

    def test_defaults_and_ports(self):
        d = parse_defaults("PORT=8443\n# x\nscheme = https\nPORTA_REMOTA=7547")
        self.assertEqual(d, {"PORT": "8443", "SCHEME": "https", "PORTA_REMOTA": "7547"})
        v = resolve_variables(Device("10.0.0.1"), Credential("a", "b"), d)
        self.assertEqual(v["BASE_URL"], "https://10.0.0.1:8443")
        self.assertEqual(v["PORTA_REMOTA"], "7547")
        # planilha sobrescreve padrão
        v = resolve_variables(Device("10.0.0.1", "8080", variables={"SCHEME": "http", "PORTA_REMOTA": ""}), None, d)
        self.assertEqual((v["BASE_URL"], v["PORTA_REMOTA"]), ("http://10.0.0.1:8080", "7547"))
        # automático
        self.assertEqual(resolve_variables(Device("h"))["BASE_URL"], "http://h:80")
        self.assertEqual(resolve_variables(Device("h", "443"))["SCHEME"], "https")
        self.assertEqual(resolve_variables(Device("h"), None, {"SCHEME": "https"})["PORT"], "443")
        self.assertEqual(missing_variables("{{X}} {{Y}}", [Device("h")], {"X": "1"}), {"Y": 1})

    def test_missing_variables(self):
        devs = [Device("1", variables={"SSID": "a"}), Device("2", variables={"SSID": ""})]
        self.assertEqual(missing_variables("{{IP}} {{SSID}} {{ VLAN }}", devs), {"SSID": 1, "VLAN": 2})


class TestEngineUnits(unittest.TestCase):
    def _run_tpl(self, tpl, values):
        """Executa o corpo do template e devolve `variables`-like dict com o que foi atribuído."""
        from core.automation_engine import compile_template, prepare_template
        body = tpl + "\nvariables['_out'] = dict(locals())"
        fn = compile_template(prepare_template(body), values)
        out = {}
        asyncio.run(fn(None, None, None, out))
        return out["_out"]

    def test_values_are_never_code(self):
        evil = '{__import__("os").getcwd()}"); __import__("os").getcwd() #\\ \'x\' {{y}}'
        tpl = ('a = "{{SSID}}"\n'
               "b = 'Rede: {{SSID}} / {x}'\n"
               'c = f"{{IP}}:{{PORT}}"\n'
               "d = {{SSID}}\n"
               'e = "{{NAO_EXISTE}}"\n'
               'f = r"C:\\{{SSID}}"')
        out = self._run_tpl(tpl, {"SSID": evil, "IP": "10.0.0.1", "PORT": "80"})
        self.assertEqual(out["a"], evil)
        self.assertEqual(out["b"], f"Rede: {evil} / {{x}}")
        self.assertEqual(out["c"], "10.0.0.1:80")
        self.assertEqual(out["d"], evil)
        self.assertEqual(out["e"], "")
        self.assertEqual(out["f"], "C:\\" + evil)

    def test_legacy_fstring_template(self):
        # templates da v1.2 usavam f"http://{{IP}}:{{PORT}}/"
        out = self._run_tpl('u = f"http://{{IP}}:{{PORT}}/"', {"IP": "1.2.3.4", "PORT": "8080"})
        self.assertEqual(out["u"], "http://1.2.3.4:8080/")

    def test_render_compat(self):
        self.assertNotIn("{{", render_script('x = "{{SSID}}"', {}))

    def test_validate_template(self):
        self.assertIsNone(validate_template(FORM_TEMPLATE))
        self.assertIn("Linha", validate_template("await page.goto(("))

    def test_classify(self):
        self.assertEqual(classify_error(PwTimeout("Timeout 15000ms exceeded")), ErrorKind.TIMEOUT)
        self.assertEqual(classify_error(PwError("net::ERR_CONNECTION_REFUSED at http://x")), ErrorKind.UNREACHABLE)
        self.assertEqual(classify_error(PwError("NS_ERROR_CONNECTION_REFUSED")), ErrorKind.UNREACHABLE)
        self.assertEqual(classify_error(PwError("Element is not attached")), ErrorKind.PAGE)
        self.assertEqual(classify_error(LoginError()), ErrorKind.AUTH)
        self.assertEqual(classify_error(NameError("x")), ErrorKind.SCRIPT)
        self.assertEqual(classify_error(ValueError(), cancelled=True), ErrorKind.CANCELLED)

    def test_retry_delay(self):
        r = RetryPolicy(max_attempts=5, backoff_s=2, max_backoff_s=10)
        self.assertEqual([r.delay(a) for a in (1, 2, 3, 4)], [2, 4, 8, 10])


class TestEngineIntegration(unittest.TestCase):
    BROWSER = os.environ.get("TEST_BROWSER", "chromium")

    def run_batch(self, engine, devices, script=FORM_TEMPLATE, creds=None, timeout_ms=4000):
        events = []
        res = asyncio.run(engine.run_batch(devices, script, creds, browser_type=self.BROWSER,
                                           timeout_ms=timeout_ms,
                                           progress_callback=lambda i, s, d: events.append((i, s, d))))
        return res, events

    def engine(self, tmp, **kw):
        kw.setdefault("retry", RetryPolicy(max_attempts=2, backoff_s=0))
        return AutomationEngine(logs_dir=tmp, **kw)

    def test_mixed_batch_one_browser(self):
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            dead = closed_port()
            devices = [
                Device("127.0.0.1", str(r.port), variables={"SSID": 'Casa "Silva"'}),
                Device("127.0.0.1", str(dead), variables={"SSID": "x"}),
                Device("127.0.0.1", str(r.port), variables={"SSID": "Loja"}),
                Device("127.0.0.1", str(r.port), credential=Credential("admin", "errada"),
                       variables={"SSID": "nunca"}),
            ]
            eng = self.engine(tmp, max_concurrent=4)
            res, events = self.run_batch(eng, devices, creds=[Credential("admin", "1234")])

            self.assertEqual(eng.browser_launches, 1)
            self.assertEqual([x.status for x in res], ["success", "error", "success", "error"])
            self.assertEqual(res[1].error_kind, ErrorKind.UNREACHABLE)
            self.assertEqual(res[1].attempts, 2)                       # retry em sem resposta
            self.assertEqual(res[3].error_kind, ErrorKind.TIMEOUT)    # senha errada = #done nunca aparece
            self.assertTrue(res[3].screenshot and os.path.exists(res[3].screenshot))
            self.assertEqual(sorted(s["ssid"] for s in r.state.saved), ['Casa "Silva"', "Loja"])
            self.assertTrue(any(s == "Sucesso" for _, s, _ in events))

    def test_credential_list_tries_next(self):
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp, retry=RetryPolicy(max_attempts=1, backoff_s=0))
            creds = parse_credentials("admin:admin\nsuporte:x\nadmin:1234")
            res, _ = self.run_batch(eng, [Device("127.0.0.1", str(r.port), variables={"SSID": "A"})],
                                    creds=creds, timeout_ms=2000)
            self.assertEqual(res[0].status, "success")
            self.assertEqual(res[0].attempts, 3)
            self.assertEqual(r.state.logins, ["admin", "suporte", "admin"])

    def test_http_basic_auth(self):
        tpl = 'await page.goto("http://{{IP}}:{{PORT}}/basic")\nawait page.wait_for_selector("#done")'
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp, retry=RetryPolicy(max_attempts=1, backoff_s=0))
            ok, _ = self.run_batch(eng, [Device("127.0.0.1", str(r.port))], tpl,
                                   creds=[Credential("admin", "no"), Credential(BASIC_USER, BASIC_PASS)])
            self.assertEqual(ok[0].status, "success")
            self.assertEqual(ok[0].credential, BASIC_USER)
            bad, _ = self.run_batch(eng, [Device("127.0.0.1", str(r.port))], tpl,
                                    creds=[Credential("admin", "no")])
            self.assertEqual(bad[0].error_kind, ErrorKind.AUTH)

    def test_template_default_port_and_base_url(self):
        tpl = FORM_TEMPLATE.replace("http://{{IP}}:{{PORT}}", "{{BASE_URL}}")
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp)
            res = asyncio.run(eng.run_batch([Device("127.0.0.1", variables={"SSID": "P"})], tpl,
                                            [Credential("admin", "1234")], browser_type=self.BROWSER,
                                            timeout_ms=4000, defaults={"PORT": str(r.port)}))
            self.assertEqual(res[0].status, "success", res[0].message)
            self.assertEqual(res[0].port, str(r.port))

    def test_script_error_no_retry_no_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp)
            res, _ = self.run_batch(eng, [Device("127.0.0.1", "80")], "await page.goto((")
            self.assertEqual(res[0].error_kind, ErrorKind.SCRIPT)
            self.assertEqual(eng.browser_launches, 0)

    def test_runtime_script_error_not_retried(self):
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp, retry=RetryPolicy(max_attempts=3, backoff_s=0))
            res, _ = self.run_batch(eng, [Device("127.0.0.1", str(r.port))], "x = variavel_inexistente")
            self.assertEqual(res[0].error_kind, ErrorKind.SCRIPT)
            self.assertEqual(res[0].attempts, 1)

    def test_concurrency_limit(self):
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp, max_concurrent=2)
            devs = [Device("127.0.0.1", str(r.port), variables={"SSID": str(i)}) for i in range(6)]
            res, _ = self.run_batch(eng, devs, creds=[Credential("admin", "1234")])
            self.assertTrue(all(x.status == "success" for x in res))
            self.assertLessEqual(r.state.max_active, 2)

    def test_cancel_stops_batch(self):
        tpl = 'await page.goto("http://{{IP}}:{{PORT}}/slow")'
        with FakeRouter() as r, tempfile.TemporaryDirectory() as tmp:
            eng = self.engine(tmp, max_concurrent=1)
            devs = [Device("127.0.0.1", str(r.port)) for _ in range(4)]

            async def go():
                task = asyncio.create_task(eng.run_batch(devs, tpl, browser_type=self.BROWSER, timeout_ms=10000))
                await asyncio.sleep(1.5)
                eng.cancel()
                return await task
            res = asyncio.run(go())
            self.assertTrue(all(x.status == "cancelled" for x in res), [x.status for x in res])


if __name__ == "__main__":
    unittest.main()
