"""Minimal LDP client towards the data server (standard path, F2.1/F4.4).

The requester's identity is propagated to the server (F6.2): in v1 dev,
the received Bearer token is forwarded as-is (AI_JWT_FORWARD_BEARER=1,
default); the definitive mechanism (RFC 8693 token exchange or service
account) is arbitrated at J15 — see spec §F6.2 rev 5.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error


class LDPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"LDP {status}: {body[:200]}")
        self.status = status
        self.body = body


class LDPClient:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Accept": "application/ld+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def _req(self, method: str, iri: str, body: dict | None = None, extra: dict | None = None):
        url = iri if iri.startswith("http") else self.base_url + iri
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(extra))
        if data is not None:
            req.add_header("Content-Type", "application/ld+json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, dict(resp.headers), (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raise LDPError(e.code, e.read().decode(errors="replace")) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LDPError(502, f"data server unreachable: {e}") from e

    def get_json(self, iri: str) -> dict:
        _, _, doc = self._req("GET", iri)
        return doc

    def get_container_members(self, iri: str) -> list[str]:
        doc = self.get_json(iri)
        contains = doc.get("http://www.w3.org/ns/ldp#contains", doc.get("ldp:contains", []))
        return [c["@id"] if isinstance(c, dict) else c for c in contains]

    def patch(self, iri: str, merge_doc: dict) -> None:
        self._req("PATCH", iri, merge_doc, {"Content-Type": "application/merge-patch+json"})

    def post(self, container: str, doc: dict) -> str:
        _, headers, _ = self._req("POST", container, doc)
        loc = headers.get("Location") or headers.get("location")
        if not loc:
            raise LDPError(500, "POST without Location header")
        return loc
