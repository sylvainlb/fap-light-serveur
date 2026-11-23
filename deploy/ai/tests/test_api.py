"""Tests d'API de bout en bout : POST /ai/run, GET /ai/jobs/<id>, /ai/models, compteurs admin.

Serveur réel sur port éphémère, LDP et modèle remplacés par des fakes.
Auth dev : AI_DEV_INSECURE=1 + headers X-Webid/X-Role.
"""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine import contract_properties  # noqa: E402
from app.jobs import StateStore  # noqa: E402
from app.registry import ModelRegistry  # noqa: E402
from app.server import AIHandler, App  # noqa: E402

os.environ["AI_DEV_INSECURE"] = "1"
os.environ["AI_JWT_VERIFY"] = "0"

CTX = {"cmp": "https://exemple.atelier/ns/component#", "app": "https://pod/ns/app/interventions#"}
CONTRACT = {"@graph": [{"sh:path": {"@id": "https://pod/ns/app/interventions#report"}}]}
DRAFT = {
    "@context": CTX, "@type": "cmp:AiComponent", "cmp:label": {"en": "Sum"},
    "cmp:core": True, "cmp:contract": "https://pod/shapes", "cmp:source": "https://pod/interventions/",
    "cmp:model": "ollama://mistral-small", "cmp:promptTemplate": "Sum: {{app:report}}",
    "cmp:cardinality": "cmp:perResource", "cmp:outputMode": "cmp:text",
    "cmp:outputField": "app:summary",
    "cmp:provenanceFields": {k: "app:p_" + k for k in
                             ["cmp:modelField", "cmp:componentField", "cmp:promptVersionField",
                              "cmp:runDateField", "cmp:localeField"]},
    "cmp:promptVersion": "1.0",
}


class FakeLDP:
    def __init__(self):
        self.docs = {
            "https://pod/shapes": CONTRACT,
            "https://pod/interventions/42": {"@id": "https://pod/interventions/42",
                                              "https://pod/ns/app/interventions#report": "Pompe remplacée"},
        }
        self.members = {"https://pod/interventions/": ["https://pod/interventions/1"]}
        self.docs["https://pod/interventions/1"] = {"https://pod/ns/app/interventions#report": "r1"}
        self.patches, self.posts = [], []

    def get_json(self, iri):
        if iri not in self.docs:
            from app.ldp import LDPError
            raise LDPError(404, iri)
        return self.docs[iri]

    def get_container_members(self, iri):
        return self.members.get(iri, [])

    def patch(self, iri, doc):
        self.patches.append((iri, doc))

    def post(self, container, doc):
        self.posts.append((container, doc))
        return container + "gen-1"


def call(method, url, body=None, webid="https://pod/users/1", role="user"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if webid:
        req.add_header("X-Webid", webid)
        req.add_header("X-Role", role)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ldp = FakeLDP()
        cls.ldp.members["/ai_models/"] = ["https://pod/ai_models/1"]
        cls.ldp.docs["https://pod/ai_models/1"] = {
            "https://pod/ns/app/ai_models#name": "ollama://mistral-small",
            "https://pod/ns/app/ai_models#protocol": "openai_chat",
            "https://pod/ns/app/ai_models#endpoint": "http://ollama:11434/v1",
            "https://pod/ns/app/ai_models#model_id": "mistral-small",
            "https://pod/ns/app/ai_models#location": "local"}
        cls.state = StateStore(tempfile.mkdtemp())
        cls.registry = ModelRegistry({"ollama://mistral-small": {
            "name": "ollama://mistral-small", "protocol": "openai_chat",
            "endpoint": "http://ollama:11434/v1", "model_id": "mistral-small", "location": "local"}})
        app = App("http://ldp.invalid", "/ai_models/", cls.state, cls.registry,
                  model_caller=lambda e, p, pa, l: "TEXTE")
        app.engine.ldp_factory = lambda token: cls.ldp
        app._ldp_factory = lambda token: cls.ldp  # reload du registre via /ai/models
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), AIHandler)
        cls.srv.app = app
        import threading
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_health_sans_auth(self):
        s, b = call("GET", self.base + "/ai/health", webid=None)
        self.assertEqual(s, 200)

    def test_run_sans_identite_401(self):
        s, b = call("POST", self.base + "/ai/run",
                    {"componentDraft": DRAFT, "source": "https://pod/interventions/42"}, webid=None)
        self.assertEqual((s, b["error"]["code"]), (401, "unauthorized"))

    def test_run_texte_per_resource_200_patch(self):
        s, b = call("POST", self.base + "/ai/run",
                    {"componentDraft": DRAFT, "source": "https://pod/interventions/42", "locale": "fr"})
        self.assertEqual(s, 200, b)
        self.assertEqual(b["patched"]["resource"], "https://pod/interventions/42")
        self.assertEqual(self.ldp.patches[-1][1]["app:summary"], "TEXTE")
        self.assertEqual(self.ldp.patches[-1][1]["app:p_cmp:localeField"], "fr")

    def test_component_et_draft_ensemble_400(self):
        s, b = call("POST", self.base + "/ai/run",
                    {"component": "https://pod/c/1", "componentDraft": DRAFT, "source": "https://pod/x"})
        self.assertEqual((s, b["error"]["code"]), (400, "invalid_request"))

    def test_ni_component_ni_draft_400(self):
        s, b = call("POST", self.base + "/ai/run", {"source": "https://pod/x"})
        self.assertEqual(s, 400)

    def test_schema_invalide_422(self):
        bad = copy.deepcopy(DRAFT); bad.pop("cmp:core")  # ni core ni package -> rejet (rev 5)
        s, b = call("POST", self.base + "/ai/run",
                    {"componentDraft": bad, "source": "https://pod/interventions/42"})
        self.assertEqual((s, b["error"]["code"]), (422, "component_invalid"))

    def test_modele_absent_registre_422(self):
        d = copy.deepcopy(DRAFT); d["cmp:model"] = "ollama://inconnu"
        s, b = call("POST", self.base + "/ai/run",
                    {"componentDraft": d, "source": "https://pod/interventions/42"})
        self.assertEqual((s, b["error"]["code"]), (422, "component_invalid"))

    def test_source_inconnue_404(self):
        s, b = call("POST", self.base + "/ai/run",
                    {"componentDraft": DRAFT, "source": "https://pod/interventions/999"})
        self.assertEqual((s, b["error"]["code"]), (404, "not_found"))

    def test_per_container_202_puis_job_demandeur_seul(self):
        d = copy.deepcopy(DRAFT)
        d["cmp:cardinality"] = "cmp:perContainer"
        d.update({"cmp:outputTarget": "https://pod/summaries/", "cmp:outputContract": "https://pod/shapes",
                  "cmp:outputCore": True})
        s, b = call("POST", self.base + "/ai/run",
                    {"componentDraft": d, "source": "https://pod/interventions/"})
        self.assertEqual(s, 202, b)
        job_uri = b["job"]
        job_id = job_uri.rsplit("/", 1)[-1]
        for _ in range(100):
            s, j = call("GET", self.base + job_uri)
            if j["state"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(j["state"], "succeeded")
        self.assertEqual(j["written"]["created"], "https://pod/summaries/gen-1")
        self.assertEqual(j["requester"], "https://pod/users/1")
        # un autre demandeur ne voit pas le job
        s, b2 = call("GET", self.base + job_uri, webid="https://pod/users/2")
        self.assertEqual((s, b2["error"]["code"]), (403, "forbidden"))
        # job inconnu
        s, _ = call("GET", self.base + "/ai/jobs/" + "0" * 32)
        self.assertEqual(s, 404)

    def test_models_et_counters_admin(self):
        s, b = call("GET", self.base + "/ai/models")
        self.assertEqual(s, 200)
        self.assertEqual(b["models"][0]["name"], "ollama://mistral-small")
        s, _ = call("GET", self.base + "/ai/admin/counters", role="user")
        self.assertEqual(s, 403)
        s, b = call("GET", self.base + "/ai/admin/counters", role="admin")
        self.assertEqual(s, 200)
        self.assertIn("counters", b)


if __name__ == "__main__":
    unittest.main()
