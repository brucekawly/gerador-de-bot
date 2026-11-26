"""Testes do scanner / identificação (servidores HTTP e SNMP falsos, locais)."""
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fingerprint as fpm  # noqa: E402
from core.fingerprint import Fingerprint, identify, match_template  # noqa: E402

HUAWEI_LOGIN = """<html><head><title>HG8245Q2</title></head><body>
<script>var x = 1;</script><div>HUAWEI Echolife</div><span>Software Version: V3R017C10S125</span></body></html>"""


def serve(pages: dict):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            status, headers, body = pages.get(self.path, (404, {}, ""))
            data = body.encode()
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestIdentify(unittest.TestCase):
    def ident(self, **raw):
        fp = Fingerprint("1.1.1.1", 80, title=raw.pop("title", ""), realm=raw.pop("realm", ""))
        fp.raw.update(raw)
        identify(fp)
        return fp

    def test_huawei_from_web(self):
        fp = self.ident(title="HG8245Q2", http="HG8245Q2\nHUAWEI Software Version: V3R017C10S125")
        self.assertEqual((fp.vendor, fp.model, fp.firmware), ("Huawei", "HG8245Q2", "V3R017C10S125"))

    def test_zte(self):
        fp = self.ident(title="F670L", http_visible="ZTE Corporation F670L login")
        self.assertEqual((fp.vendor, fp.model), ("ZTE", "F670L"))

    def test_tplink_realm(self):
        fp = self.ident(realm="TP-LINK Archer C6")
        self.assertEqual((fp.vendor, fp.model), ("TP-Link", "Archer C6"))

    def test_upnp_wins(self):
        fp = self.ident(upnp_manufacturer="TP-Link", upnp_model="EX141 1.0", upnp_fw="1.1.0 Build 2023",
                        http_visible="Login")
        self.assertEqual((fp.vendor, fp.model, fp.firmware), ("TP-Link", "EX141", "1.1.0 Build 2023"))
        self.assertIn("UPnP", fp.sources)

    def test_snmp_firmware(self):
        fp = self.ident(snmp="MikroTik RouterOS 7.12.1 (stable) RB4011iGS+")
        self.assertEqual(fp.vendor, "MikroTik")
        self.assertEqual(fp.model, "RB4011iGS+")

    def test_servers_are_not_devices(self):
        fp = Fingerprint("10.0.0.11", 80, title="301 Moved Permanently", server="nginx/1.18.0 (Ubuntu)")
        fp.raw["redirect_to"] = "https://cliente.oltcloud.co/"
        identify(fp)
        self.assertTrue(fpm.is_system(fp))
        self.assertEqual(fp.vendor, "OLT Cloud")
        self.assertIn("cliente.oltcloud.co", fp.raw["note"])
        nginx = Fingerprint("10.0.0.13", 80, title="Welcome to nginx!")
        identify(nginx)
        self.assertTrue(fpm.is_system(nginx))
        # TP-Link redireciona para o domínio dele, mas é o roteador
        tp = Fingerprint("10.0.0.20", 80, title="Opening...")
        tp.raw["redirect_to"] = "http://tplinkwifi.net/"
        tp.raw["http_visible"] = "TP-Link Archer C6"
        identify(tp)
        self.assertFalse(fpm.is_system(tp))
        self.assertEqual(tp.vendor, "TP-Link")
        self.assertIn("tplinkwifi.net", tp.raw["note"])

    def test_redirect_to_other_host_is_recorded(self):
        srv = serve({"/": (302, {"Location": "https://cliente.oltcloud.co/"}, "")})
        try:
            fp = fpm.fingerprint_host("127.0.0.1", srv.server_port, snmp_community=None, use_upnp=False)
        finally:
            srv.shutdown()
        self.assertEqual(fp.raw.get("redirect_to"), "https://cliente.oltcloud.co/")
        self.assertTrue(fpm.is_system(fp))

    def test_unknown(self):
        fp = self.ident(http_visible="Login page")
        self.assertEqual(fp.vendor, "")
        self.assertEqual(fp.label, "Não identificado")

    def test_match_template(self):
        tpls = [(1, "Huawei", "EG8145V5", "", "", ""), (2, "Huawei", "HG8245Q2", "", "", ""),
                (3, "ZTE", "F670L", "", "", "")]
        fp = Fingerprint("x", 80, vendor="Huawei", model="HG8245Q2")
        self.assertEqual(match_template(fp, tpls), (tpls[1], "modelo"))
        fp2 = Fingerprint("x", 80, vendor="Huawei", model="HS8145X6")
        self.assertEqual(match_template(fp2, tpls)[1], "fabricante")
        self.assertEqual(match_template(Fingerprint("x", 80, vendor="Nokia"), tpls), (None, ""))


class TestProbes(unittest.TestCase):
    def test_http_probe_follows_redirect(self):
        srv = serve({"/": (302, {"Location": "/login.html", "Server": "Boa/0.94"}, ""),
                     "/login.html": (200, {"Content-Type": "text/html"}, HUAWEI_LOGIN)})
        try:
            fp = fpm.fingerprint_host("127.0.0.1", srv.server_port, snmp_community=None, use_upnp=False)
        finally:
            srv.shutdown()
        self.assertEqual(fp.server, "Boa/0.94")
        self.assertEqual((fp.vendor, fp.model, fp.firmware), ("Huawei", "HG8245Q2", "V3R017C10S125"))

    def test_http_realm_401(self):
        srv = serve({"/": (401, {"WWW-Authenticate": 'Basic realm="TP-LINK Wireless N Router WR840N"'}, "")})
        try:
            fp = fpm.fingerprint_host("127.0.0.1", srv.server_port, snmp_community=None, use_upnp=False)
        finally:
            srv.shutdown()
        self.assertEqual(fp.vendor, "TP-Link")
        self.assertIn("WR840N", fp.realm)

    def test_upnp_xml(self):
        xml = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
        <manufacturer>Intelbras</manufacturer><modelName>WiFiber 121 AC</modelName>
        <modelNumber>121AC</modelNumber><softwareVersion>1.2.3</softwareVersion></device></root>"""
        srv = serve({"/desc.xml": (200, {"Content-Type": "text/xml"}, xml), "/": (200, {}, "<title>x</title>")})
        try:
            fp = fpm.fingerprint_host("127.0.0.1", srv.server_port, snmp_community=None,
                                      ssdp_location=f"http://127.0.0.1:{srv.server_port}/desc.xml")
        finally:
            srv.shutdown()
        self.assertEqual(fp.vendor, "Intelbras")
        self.assertEqual(fp.firmware, "1.2.3")

    def test_snmp_sysdescr(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        desc = "Huawei EG8145V5 V5R020C10S115"

        def responder():
            data, addr = sock.recvfrom(2048)
            vb = fpm._tlv(0x30, fpm._tlv(0x06, fpm.SYSDESCR_OID) + fpm._tlv(0x04, desc.encode()))
            pdu = fpm._tlv(0xA2, fpm._tlv(0x02, b"\x01") + fpm._tlv(0x02, b"\x00") + fpm._tlv(0x02, b"\x00")
                           + fpm._tlv(0x30, vb))
            sock.sendto(fpm._tlv(0x30, fpm._tlv(0x02, b"\x00") + fpm._tlv(0x04, b"public") + pdu), addr)
        threading.Thread(target=responder, daemon=True).start()

        orig = fpm.socket.socket.sendto
        # redireciona a porta 161 para o respondedor local
        def sendto(self, data, addr):
            if addr[1] == 161:
                addr = (addr[0], port)
            return orig(self, data, addr)
        fpm.socket.socket.sendto = sendto
        try:
            self.assertEqual(fpm.snmp_sysdescr("127.0.0.1"), desc)
        finally:
            fpm.socket.socket.sendto = orig
            sock.close()

    def test_fiberhome_probe_beats_js_false_positive(self):
        login = """<html><head><script src="./js/xhr.js"></script><script>
        XHR.get("get_device_name", null, f); if(op=="BZ_INTELBRAS"){} if(op=="HUAWEI_X"){}</script></head></html>"""
        srv = serve({"/": (200, {}, login), "/js/xhr.js": (200, {}, 'var ajaxcgi = "../cgi-bin/ajax";'),
                     "/cgi-bin/ajax?ajaxmethod=get_device_name": (200, {}, '{"ModelName":"HG6145F3"}'),
                     "/cgi-bin/ajax?ajaxmethod=get_operator": (200, {}, '{"operator_name":"BZ_INTELBRAS"}')})
        try:
            fp = fpm.fingerprint_host("127.0.0.1", srv.server_port, snmp_community=None, use_upnp=False)
        finally:
            srv.shutdown()
        self.assertEqual((fp.vendor, fp.model), ("Fiberhome", "HG6145F3"))
        self.assertEqual(fp.raw["note"], "Operadora: BZ_INTELBRAS")

    def test_one_bad_host_does_not_kill_scan(self):
        srv = serve({"/": (200, {}, HUAWEI_LOGIN)})
        orig = fpm.fingerprint_host

        def flaky(ip, port, **kw):
            if port == srv.server_port:
                raise ValueError("resposta UPnP malformada")
            return orig(ip, port, **kw)
        srv2 = serve({"/": (200, {}, HUAWEI_LOGIN)})
        fpm.fingerprint_host = flaky
        try:
            # dois "equipamentos" no mesmo IP: usa portas diferentes em hosts diferentes simulados
            res = []
            for port in (srv.server_port, srv2.server_port):
                res += fpm.scan(["127.0.0.1"], [port], snmp_community=None, include_silent=False)
        finally:
            fpm.fingerprint_host = orig
            srv.shutdown()
            srv2.shutdown()
        self.assertEqual(len(res), 2)
        self.assertIn("identificação falhou", res[0].raw.get("note", ""))
        self.assertEqual(res[1].vendor, "Huawei")

    def test_expand_range(self):
        self.assertEqual(fpm.expand_range("10.0.0.254", "10.0.1.1"),
                         ["10.0.0.254", "10.0.0.255", "10.0.1.0", "10.0.1.1"])
        with self.assertRaises(ValueError):
            fpm.expand_range("10.0.0.2", "10.0.0.1")

    def test_scan_finds_open_port(self):
        srv = serve({"/": (200, {}, HUAWEI_LOGIN)})
        try:
            res = fpm.scan(["127.0.0.1"], [1, srv.server_port], snmp_community=None)
        finally:
            srv.shutdown()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].port, srv.server_port)
        self.assertEqual(res[0].vendor, "Huawei")


if __name__ == "__main__":
    unittest.main()
