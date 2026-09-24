"""Family CA + native HTTPS (V2 CP2, spec A6).

The installer's default: the server answers HTTPS itself with a certificate
from a family CA, so the PIN works with no proxy and never travels in the
clear. Pinned here: CA/server cert shape, key file modes, idempotent re-init
(CA reused so trusted devices stay trusted), reissue on name change, a real
TLS handshake that verifies against the CA, PIN login over native TLS,
HTTP→HTTPS redirect for private pages, /ca.pem served, and that a busy HTTPS
port never takes the picture frame down.

Run: python3 -m pytest tests/test_tls.py -q
"""

import http.client
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography import x509  # noqa: E402

from memoryvault import config, db  # noqa: E402
from memoryvault.constellation import auth, tls  # noqa: E402
from memoryvault.constellation import server as srv  # noqa: E402

PIN = "5150"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class CertTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="mv-tls-"))

    def test_ca_and_server_cert_shape(self):
        r = tls.init(names=["box.local", "localhost"], ips=["127.0.0.1", "10.0.0.5"], d=self.d)
        self.assertTrue(r["created_ca"])
        p = tls.paths(self.d)
        ca = x509.load_pem_x509_certificate(p["ca_cert"].read_bytes())
        leaf = x509.load_pem_x509_certificate(p["cert"].read_bytes())
        self.assertTrue(ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertFalse(leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertEqual(leaf.issuer, ca.subject)
        dns, ips = tls._sans(leaf)
        self.assertEqual(dns, {"box.local", "localhost"})
        self.assertEqual(ips, {"127.0.0.1", "10.0.0.5"})
        life = leaf.not_valid_after_utc - leaf.not_valid_before_utc
        self.assertLessEqual(life.days, 398)  # browsers reject longer leaf certs

    def test_private_keys_are_0600_public_certs_readable(self):
        tls.init(names=["localhost"], ips=["127.0.0.1"], d=self.d)
        p = tls.paths(self.d)
        for k in ("ca_key", "key"):
            self.assertEqual(p[k].stat().st_mode & 0o777, 0o600, k)
        for k in ("ca_cert", "cert"):
            self.assertEqual(p[k].stat().st_mode & 0o777, 0o644, k)

    def test_reinit_keeps_ca_and_only_reissues_on_change(self):
        tls.init(names=["localhost"], ips=["127.0.0.1"], d=self.d)
        p = tls.paths(self.d)
        ca_before = p["ca_cert"].read_bytes()
        leaf_before = p["cert"].read_bytes()
        r = tls.init(names=["localhost"], ips=["127.0.0.1"], d=self.d)
        self.assertFalse(r["created_ca"])
        self.assertFalse(r["reissued_server_cert"])
        self.assertEqual(p["cert"].read_bytes(), leaf_before)
        r = tls.init(names=["localhost", "new.local"], ips=["127.0.0.1"], d=self.d)
        self.assertTrue(r["reissued_server_cert"])
        self.assertEqual(p["ca_cert"].read_bytes(), ca_before, "CA must survive, devices trust it")

    def test_garbage_ip_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            tls.init(names=["localhost"], ips=["not-an-ip"], d=self.d)
        self.assertFalse(tls.paths(self.d)["ca_key"].exists())


class NativeHttpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mv-https-"))
        cls._saved = (config.LIBRARY_ROOT, config.DB_PATH)
        config.LIBRARY_ROOT = cls.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        db.init(config.DB_PATH).close()
        os.environ["MEMORYVAULT_PIN_FILE"] = str(cls.tmp / "pin.json")
        os.environ["MEMORYVAULT_TLS_DIR"] = str(cls.tmp / "tls")
        os.environ.pop("MEMORYVAULT_REQUIRE_TLS", None)
        tls.init(names=["localhost"], ips=["127.0.0.1"])
        auth.set_pin(PIN)
        cls.http_port, cls.https_port = _free_port(), _free_port()
        cls._saved_db = srv.Handler.condb
        cls.t = threading.Thread(target=srv.serve, kwargs=dict(
            host="127.0.0.1", port=cls.http_port, tls_port=cls.https_port), daemon=True)
        cls.t.start()
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.https_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        cls.ctx = ssl.create_default_context(cafile=str(tls.paths()["ca_cert"]))

    @classmethod
    def tearDownClass(cls):
        config.LIBRARY_ROOT, config.DB_PATH = cls._saved
        srv.Handler.condb = cls._saved_db
        for k in ("MEMORYVAULT_PIN_FILE", "MEMORYVAULT_TLS_DIR"):
            os.environ.pop(k, None)

    def setUp(self):
        auth.SESSIONS.clear()
        auth.LOCKOUT.clear()

    def https(self, method, path, headers=None, body=None):
        c = http.client.HTTPSConnection("localhost", self.https_port, context=self.ctx, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return r.status, r, r.read()

    def http(self, method, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.http_port, timeout=10)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        return r.status, r, r.read()

    def test_handshake_verifies_against_family_ca(self):
        with socket.create_connection(("127.0.0.1", self.https_port), timeout=5) as raw:
            with self.ctx.wrap_socket(raw, server_hostname="localhost") as s:
                self.assertIn(s.version(), ("TLSv1.2", "TLSv1.3"))

    def test_untrusted_client_rejects_the_cert(self):
        bare = ssl.create_default_context()
        with self.assertRaises(ssl.SSLError):
            with socket.create_connection(("127.0.0.1", self.https_port), timeout=5) as raw:
                bare.wrap_socket(raw, server_hostname="localhost").close()

    def test_pin_login_works_over_native_tls_without_proxy(self):
        status, r, _ = self.https("POST", "/api/auth/login", body={"pin": PIN})
        self.assertEqual(status, 200)
        cookie = "; ".join(c.split(";", 1)[0] for c in r.msg.get_all("Set-Cookie"))
        self.assertIn("Secure", r.msg.get_all("Set-Cookie")[0])
        status, _, _ = self.https("GET", "/api/curation", {"Cookie": cookie})
        self.assertEqual(status, 200)

    def test_http_private_page_redirects_to_https(self):
        status, r, _ = self.http("GET", "/curation", {"Host": f"localhost:{self.http_port}"})
        self.assertEqual(status, 308)
        self.assertEqual(r.getheader("Location"), f"https://localhost:{self.https_port}/curation")

    def test_http_private_api_refused_with_https_hint(self):
        status, _, body = self.http("GET", "/api/curation", {"Host": f"localhost:{self.http_port}"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["https"], f"https://localhost:{self.https_port}/api/curation")

    def test_http_login_refused(self):
        c = http.client.HTTPConnection("127.0.0.1", self.http_port, timeout=10)
        c.request("POST", "/api/auth/login", body=json.dumps({"pin": PIN}),
                  headers={"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 403)

    def test_picture_frame_stays_on_plain_http(self):
        status, _, _ = self.http("GET", "/wall")
        self.assertEqual(status, 200)

    def test_ca_cert_served_for_devices(self):
        status, _, body = self.http("GET", "/ca.pem")
        self.assertEqual(status, 200)
        self.assertEqual(body, tls.paths()["ca_cert"].read_bytes())
        self.assertNotIn(b"PRIVATE KEY", body)

    def test_hostile_host_header_does_not_steer_redirect(self):
        # A redirect may only ever point at a bare host on our HTTPS port.
        # Anything with a path, userinfo or whitespace in Host gets no
        # redirect at all (403 with no https hint), never a Location header
        # that sends the family somewhere else.
        for hostile in ("evil.example/x", "evil.example@good", "a b", "x\\y"):
            status, r, _ = self.http("GET", "/curation", {"Host": hostile})
            self.assertIsNone(r.getheader("Location"), hostile)
            self.assertEqual(status, 403, hostile)


class BusyPortTest(unittest.TestCase):
    def test_busy_https_port_keeps_frame_up(self):
        tmp = Path(tempfile.mkdtemp(prefix="mv-busy-"))
        saved = (config.LIBRARY_ROOT, config.DB_PATH)
        config.LIBRARY_ROOT = tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        db.init(config.DB_PATH).close()
        os.environ["MEMORYVAULT_TLS_DIR"] = str(tmp / "tls")
        tls.init(names=["localhost"], ips=["127.0.0.1"])
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        busy = blocker.getsockname()[1]
        http_port = _free_port()
        try:
            threading.Thread(target=srv.serve, kwargs=dict(
                host="127.0.0.1", port=http_port, tls_port=busy), daemon=True).start()
            ok = False
            for _ in range(50):
                try:
                    c = http.client.HTTPConnection("127.0.0.1", http_port, timeout=2)
                    c.request("GET", "/wall")
                    ok = c.getresponse().status == 200
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertTrue(ok, "picture frame must come up even if the HTTPS port is taken")
        finally:
            blocker.close()
            config.LIBRARY_ROOT, config.DB_PATH = saved
            os.environ.pop("MEMORYVAULT_TLS_DIR", None)


if __name__ == "__main__":
    unittest.main()
