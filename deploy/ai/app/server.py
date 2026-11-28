"""API of the ORCE AI stack service (F2.4, annex C): POST /ai/run, GET /ai/jobs/<id>.

Authentication: requester OIDC (Bearer JWT). In dev (AI_JWT_VERIFY=0),
the JWT payload is decoded without signature verification (internal TLS
network); AI_DEV_INSECURE=1 accepts X-Webid/X-Role headers. Propagation to
the LDP forwards the Bearer (F6.2, definitive arbitration at J15).
"""
from __future__ import annotations

import base64
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import Engine, RunError, component_ref_for
from .identity import IdentityError, ldp_token_for
from .jobs import StateStore
from .ldp import LDPClient, LDPError
from .registry import ModelRegistry
from .template import ComponentInvalid  # noqa: F401 (re-export)


def identity_from_headers(headers) -> tuple[str, str]:
    """Requester's (webid, role). 401 if missing."""
    auth = headers.get("Authorization", "")
    dev_insecure = os.environ.get("AI_DEV_INSECURE", "0") == "1"
    if auth.startswith("Bearer ") and os.environ.get("AI_JWT_VERIFY", "0") == "0":
        try:
            payload = auth[7:].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
            webid = claims.get("webid")
            role = claims.get("role", "user")
            if webid:
                return webid, role
        except Exception:
            pass
    if dev_insecure:
        webid = headers.get("X-Webid")
        if webid:
            return webid, headers.get("X-Role", "user")
    raise RunError(401, "unauthorized", "identity required (Bearer OIDC)")


class App:
    def __init__(self, ldp_base_url: str, models_container: str, state: StateStore,
                 registry: ModelRegistry, model_caller=None):
        self.ldp_base_url = ldp_base_url
        self.models_container = models_container
        self.state = state
        self.registry = registry
        self.engine = Engine(self._ldp_factory, registry, state, model_caller=model_caller)

    def _ldp_factory(self, token: str | None) -> LDPClient:
        return LDPClient(self.ldp_base_url, token)


class AIHandler(BaseHTTPRequestHandler):
    server_version = "fap-ai/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    # ---------------------------------------------------------------- helpers
    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, e: RunError) -> None:
        self._send(e.http, {"error": {"code": e.code, "message": e.message, **e.extra}})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            doc = json.loads(raw or b"{}")
            if not isinstance(doc, dict):
                raise ValueError
            return doc
        except ValueError:
            raise RunError(400, "invalid_request", "JSON object body required") from None

    def log_message(self, fmt, *args):  # concise log
        print("ai:", fmt % args, flush=True)

    # ------------------------------------------------------------------ routes
    def do_GET(self):
        try:
            if self.path == "/ai/health":
                return self._send(200, {"status": "ok"})
            m = re.fullmatch(r"/ai/jobs/([0-9a-f]+)", self.path)
            if m:
                webid, _ = identity_from_headers(self.headers)
                job = self.app.state.get_job(m.group(1))
                if job is None:
                    raise RunError(404, "not_found", "unknown job")
                if job["requester"] != webid:
                    raise RunError(403, "forbidden", "job visible to the requester only (annex C)")
                return self._send(200, job)
            if self.path == "/ai/models":
                webid, _ = identity_from_headers(self.headers)
                # hot reload: the service may start before the LDP
                token = self.headers.get("Authorization", "").replace("Bearer ", "") or None
                try:
                    self.app.registry.reload(self.app._ldp_factory(token), self.app.models_container)
                except LDPError as e:
                    raise RunError(502, "ldp_error", f"registry unavailable: {e}") from e
                return self._send(200, {"models": self.app.registry.list_public()})
            if self.path == "/ai/admin/counters":
                _, role = identity_from_headers(self.headers)
                if role != "admin":
                    raise RunError(403, "forbidden", "admin role required (F5.3)")
                return self._send(200, {"counters": self.app.state.counters()})
            raise RunError(404, "not_found", "unknown route")
        except RunError as e:
            self._error(e)
        except LDPError as e:
            self._error(RunError(502, "ldp_error", f"data server: {e}"))
        except Exception as e:  # noqa: BLE001
            self._error(RunError(500, "internal", str(e)))

    def do_POST(self):
        try:
            if self.path != "/ai/run":
                raise RunError(404, "not_found", "unknown route")
            webid, _ = identity_from_headers(self.headers)
            body = self._body()
            component, draft = body.get("component"), body.get("componentDraft")
            # annex C rev 5: exactly one of the two
            if (component and draft) or (not component and not draft):
                raise RunError(400, "invalid_request", "exactly one of 'component' / 'componentDraft' required")
            source = body.get("source")
            if not source:
                raise RunError(400, "invalid_request", "'source' required")
            locale = body.get("locale", "en")
            bearer = self.headers.get("Authorization", "").replace("Bearer ", "") or None
            try:
                token = ldp_token_for(bearer, webid)  # F6.2: RFC 8693 token exchange if AI_TOKEN_EXCHANGE=1
            except IdentityError as e:
                raise RunError(502, "identity_exchange_failed", str(e)) from e

            doc = draft if draft else self.app.engine.ldp_factory(token).get_json(component)
            ref = component_ref_for(component, draft)
            is_test = draft is not None  # editor path = test (annex C, F5.3)

            if doc.get("cmp:cardinality") == "cmp:perContainer":
                job = self.app.engine.run_async(doc, ref, source, locale, token, webid, is_test)
                return self._send(202, {"job": f"/ai/jobs/{job['job']}"})
            result = self.app.engine.run_sync(doc, ref, source, locale, token)
            return self._send(200, result)  # annex C: {"patched": {"resource", "field"}}
        except RunError as e:
            self._error(e)
        except LDPError as e:
            if e.status == 404:
                self._error(RunError(404, "not_found", f"unknown component or source: {e.body[:100]}"))
            else:
                self._error(RunError(502, "ldp_error", f"data server: {e}"))
        except Exception as e:  # noqa: BLE001
            self._error(RunError(500, "internal", str(e)))


def main() -> None:
    ldp_base = os.environ.get("LDP_BASE_URL", "http://localhost:8443")
    container = os.environ.get("AI_MODELS_CONTAINER", "/ai_models/")
    state = StateStore(
        os.environ.get("AI_STATE_DIR", "/data"),
        budget_max_runs=int(os.environ.get("AI_BUDGET_MAX_RUNS", "0")),
        budget_window_days=int(os.environ.get("AI_BUDGET_WINDOW_DAYS", "30")),
        retention_days=int(os.environ.get("AI_JOB_RETENTION_DAYS", "7")),
    )
    ldp_admin = LDPClient(ldp_base, os.environ.get("AI_LDP_ADMIN_TOKEN") or None)
    registry = ModelRegistry({})
    try:
        n = registry.reload(ldp_admin, container)
        print(f"registry loaded: {n} model(s)", flush=True)
    except Exception as e:  # the LDP may not be up yet: /ai/models will reload
        print(f"registry unavailable at boot ({e}) — reloaded via GET /ai/models", flush=True)
    app = App(ldp_base, container, state, registry)
    port = int(os.environ.get("AI_PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), AIHandler)
    srv.app = app  # type: ignore[attr-defined]
    print(f"AI stack (J15) — POST /ai/run on :{port}, LDP={ldp_base}, registry={container} "
          f"({len(registry.entries)} models)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
