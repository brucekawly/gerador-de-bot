"""Acesso pelo servidor do cliente: SSH falso local + roteador falso."""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fingerprint as fpm  # noqa: E402
from core.automation_engine import AutomationEngine, RetryPolicy  # noqa: E402
from core.devices import Credential, Device  # noqa: E402
from core.remote import RemoteError, Server, SshGateway, candidate_networks  # noqa: E402
from tests.fake_router import FakeRouter  # noqa: E402
from tests.fake_ssh import PASSWORD, USER, FakeSSH  # noqa: E402
from tests.test_engine import FORM_TEMPLATE  # noqa: E402


def gateway(ssh):
    return SshGateway(Server("127.0.0.1", ssh.port, USER, auth="password", password=PASSWORD)).connect()


class TestCandidateNetworks(unittest.TestCase):
    ADDRS = """1: lo    inet 127.0.0.1/8 scope host lo
2: ens18    inet 198.51.100.4/24 brd 198.51.100.255 scope global ens18
3: tun0    inet 10.8.0.1/24 scope global tun0
4: docker0    inet 10.134.235.1/24 scope global docker0
5: br-c409cb7442a2    inet 10.135.0.1/24 scope global br-c409cb7442a2"""
    ROUTES = """default via 198.51.100.1 dev ens18 proto static
10.8.0.0/24 dev tun0 proto kernel scope link src 10.8.0.1
10.52.0.0/16 via 10.70.70.1 dev ppp2
172.23.10.0/24 via 10.70.70.1 dev ppp2
10.134.235.0/24 dev docker0 proto kernel scope link src 10.134.235.1 linkdown"""
    NEIGH = """10.135.0.2 dev br-c409cb7442a2 lladdr 02:42:0a:87:00:02 REACHABLE
172.23.10.5 dev ppp2 lladdr 00:11:22:33:44:55 STALE"""
    PEERS = "172.21.36.62:51234 172.21.36.80:40000 10.135.0.2:5432 127.0.0.1:6379 203.0.113.77:3333"

    def test_sources_and_filters(self):
        nets = {n["cidr"]: n for n in candidate_networks(self.ADDRS, self.ROUTES, self.NEIGH, self.PEERS)}
        self.assertIn("10.8.0.0/24", nets)
        self.assertIn("172.23.10.0/24", nets)
        self.assertEqual(nets["172.23.10.0/24"]["alive"], 1)
        self.assertFalse(nets["10.52.0.0/16"]["suggested"])         # rede grande vem desmarcada
        self.assertNotIn("10.134.235.0/24", nets)                   # docker
        self.assertNotIn("10.135.0.0/24", nets)                     # bridge de contêiner (nem por ARP)
        self.assertNotIn("198.51.100.0/24", nets)                    # pública
        self.assertIn("172.21.36.0/24", nets)                       # CPEs conectadas ao ACS
        self.assertEqual(nets["172.21.36.0/24"]["alive"], 2)


class TestGateway(unittest.TestCase):
    def test_wrong_password(self):
        with FakeSSH() as ssh:
            with self.assertRaises(RemoteError):
                SshGateway(Server("127.0.0.1", ssh.port, USER, auth="password", password="errada")).connect()

    def test_dial_and_fingerprint_through_server(self):
        with FakeRouter() as r, FakeSSH() as ssh:
            gw = gateway(ssh)
            try:
                fp = fpm.fingerprint_host("127.0.0.1", r.port, snmp_community=None, gateway=gw)
            finally:
                gw.close()
        self.assertEqual(fp.title, "FakeRouter AX-1000")
        self.assertIn(("127.0.0.1", r.port), ssh.forwards)

    def test_batch_through_socks_proxy(self):
        with FakeRouter() as r, FakeSSH() as ssh, tempfile.TemporaryDirectory() as tmp:
            gw = gateway(ssh)
            try:
                proxy = gw.start_socks()
                eng = AutomationEngine(max_concurrent=2, retry=RetryPolicy(max_attempts=1, backoff_s=0),
                                       logs_dir=tmp)
                devs = [Device("127.0.0.1", str(r.port), variables={"SSID": f"via-servidor-{i}"}) for i in range(3)]
                res = asyncio.run(eng.run_batch(devs, FORM_TEMPLATE, [Credential("admin", "1234")],
                                                browser_type="chromium", timeout_ms=8000, proxy=proxy))
            finally:
                gw.close()
        self.assertEqual([x.status for x in res], ["success"] * 3, [x.message for x in res])
        self.assertEqual(sorted(s["ssid"] for s in r.state.saved), [f"via-servidor-{i}" for i in range(3)])
        self.assertTrue(ssh.forwards)                                # passou mesmo pelo SSH

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout") and shutil.which("xargs"),
                         "precisa de bash/timeout/xargs locais para simular o servidor")
    def test_remote_tcp_scan(self):
        with FakeRouter() as r, FakeSSH() as ssh:
            gw = gateway(ssh)
            try:
                found = gw.tcp_scan(["127.0.0.1"], [1, r.port], timeout=1)
            finally:
                gw.close()
        self.assertEqual(found, {"127.0.0.1": r.port})
        self.assertTrue(any("/dev/tcp" in c for c in ssh.commands))

    def test_checkup_reports_missing(self):
        from core.remote import checkup_summary
        with FakeSSH() as ssh:
            gw = gateway(ssh)
            try:
                gw._tools = {"bash", "timeout", "xargs"}          # simula servidor sem ip/ss/snmpget
                items = {i["item"]: i for i in gw.checkup()}
            finally:
                gw.close()
        self.assertTrue(items["Encaminhamento de porta (AllowTcpForwarding)"]["ok"])
        self.assertFalse(items["ip"]["ok"])
        text, level = checkup_summary(list(items.values()))
        self.assertEqual(level, "aviso")
        self.assertIn("apt install iproute2", text)
        items["bash"]["ok"] = False
        self.assertEqual(checkup_summary(list(items.values()))[1], "erro")

    def test_tcp_scan_rejects_injection(self):
        gw = SshGateway(Server("127.0.0.1"))
        with self.assertRaises(ValueError):
            gw.tcp_scan(["127.0.0.1; rm -rf /"], [80])
        with self.assertRaises(ValueError):
            gw.tcp_scan(["127.0.0.1"], ["80; reboot"])


class TestTargets(unittest.TestCase):
    def test_parse_targets(self):
        self.assertEqual(fpm.parse_targets("10.0.0.1-3, 10.0.0.2; 10.0.0.9"),
                         ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.9"])
        self.assertEqual(len(fpm.parse_targets("192.168.0.0/24")), 254)
        with self.assertRaises(ValueError):
            fpm.parse_targets("10.0.0.0/8", limit=1000)


if __name__ == "__main__":
    unittest.main()
