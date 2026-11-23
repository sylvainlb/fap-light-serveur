"""Tests F6.2 : token exchange RFC 8693 (arbitrage J15) + repli forward Bearer."""
import json
import os
import sys
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import identity  # noqa: E402
from app.identity import IdentityError  # noqa: E402


class FakeIdP(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())
        if form.get("grant_type") != ["urn:ietf:params:oauth:grant-type:token-exchange"]:
            self.send_response(400); self.end_headers(); return
        if form.get("requested_subject", [""])[0] != "https://pod/users/1":
            self.send_response(403); self.end_headers(); return
        raw = json.dumps({"access_token": "EXCHANGED-TOKEN", "token_type": "Bearer"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


class TestIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeIdP)
        import threading
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/token"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_token_exchange_rfc8693(self):
        tok = identity.exchange_token("SUBJECT", "https://pod/users/1",
                                      token_url=self.url, client_id="ai-service",
                                      client_secret="s3cret")
        self.assertEqual(tok, "EXCHANGED-TOKEN")

    def test_sans_config_erreur(self):
        old = os.environ.get("IDP_TOKEN_URL")
        os.environ.pop("IDP_TOKEN_URL", None)
        try:
            with self.assertRaises(IdentityError):
                identity.exchange_token("x", "y")
        finally:
            if old:
                os.environ["IDP_TOKEN_URL"] = old

    def test_ldp_token_for_repli_forward(self):
        os.environ["AI_TOKEN_EXCHANGE"] = "0"
        self.assertEqual(identity.ldp_token_for("BEARER", "https://pod/users/1"), "BEARER")

    def test_ldp_token_for_exchange(self):
        os.environ["AI_TOKEN_EXCHANGE"] = "1"
        os.environ["IDP_TOKEN_URL"] = self.url
        os.environ["AI_CLIENT_ID"] = "ai-service"
        os.environ["AI_CLIENT_SECRET"] = "s3cret"
        try:
            self.assertEqual(identity.ldp_token_for("SUBJECT", "https://pod/users/1"), "EXCHANGED-TOKEN")
        finally:
            os.environ["AI_TOKEN_EXCHANGE"] = "0"


if __name__ == "__main__":
    unittest.main()
