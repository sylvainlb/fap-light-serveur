"""Unit tests: template (R1.1), registry (F1.2), text engine (F2/F3), jobs/budget."""
import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import template  # noqa: E402
from app.engine import Engine, RunError, contract_properties, provenance_values  # noqa: E402
from app.jobs import StateStore  # noqa: E402
from app.registry import ModelRegistry  # noqa: E402
from app.template import ComponentInvalid  # noqa: E402

CTX = {"cmp": "https://example.workshop/ns/component#", "app": "https://pod/ns/app/interventions#"}
CONTRACT = {"@graph": [{"sh:path": {"@id": "https://pod/ns/app/interventions#report"}},
                      {"sh:path": {"@id": "https://pod/ns/app/interventions#status"}}]}
PROPS = contract_properties(CONTRACT)

COMPONENT = {
    "@context": CTX, "@type": "cmp:AiComponent", "cmp:label": {"en": "Sum"},
    "cmp:core": True, "cmp:contract": "https://pod/shapes", "cmp:source": "https://pod/interventions/",
    "cmp:model": "ollama://mistral-small", "cmp:promptTemplate": "Sum: {{app:report}}",
    "cmp:cardinality": "cmp:perResource", "cmp:outputMode": "cmp:text",
    "cmp:outputField": "app:summary",
    "cmp:provenanceFields": {"cmp:modelField": "app:prov_model", "cmp:componentField": "app:prov_comp",
                             "cmp:promptVersionField": "app:prov_pv", "cmp:runDateField": "app:prov_date",
                             "cmp:localeField": "app:prov_locale"},
    "cmp:parameters": {"temperature": 0.2}, "cmp:promptVersion": "1.0",
}


class FakeLDP:
    def __init__(self, docs=None, members=None):
        self.docs = docs or {}
        self.members = members or {}
        self.patches = []
        self.posts = []
        self.validate_reports = []   # queue of W3C reports for POST */validate
        self.validated = []          # graphs submitted for validation

    def get_json(self, iri):
        if iri not in self.docs:
            from app.ldp import LDPError
            raise LDPError(404, "not found: " + iri)
        return self.docs[iri]

    def get_container_members(self, iri):
        return self.members.get(iri, [])

    def patch(self, iri, doc):
        self.patches.append((iri, doc))

    def post(self, container, doc):
        self.posts.append((container, doc))
        return container + "generated-1"

    def _req(self, method, iri, body=None, extra=None):
        """Intercepts POST <...>/validate (J3 ext/shacl endpoint)."""
        if method == "POST" and "/validate" in iri:
            self.validated.append((iri, body))
            report = self.validate_reports.pop(0) if self.validate_reports else {
                "@context": {"sh": "http://www.w3.org/ns/shacl#"},
                "@type": "sh:ValidationReport",
                "sh:conforms": True, "sh:result": []}
            return 200, {}, report
        raise AssertionError("_req not supported: " + method + " " + iri)


class TestTemplate(unittest.TestCase):
    def test_render(self):
        out = template.render("Sum: {{app:report}}", CTX, {"https://pod/ns/app/interventions#report": "abc"})
        self.assertEqual(out, "Sum: abc")

    def test_placeholder_hors_contrat_rejet(self):
        with self.assertRaises(ComponentInvalid):
            template.validate_placeholders("{{app:unknown}}", CTX, PROPS)
        template.validate_placeholders("{{app:report}} {{app:status}}", CTX, PROPS)  # OK

    def test_prefix_inconnu_rejet(self):
        with self.assertRaises(ComponentInvalid):
            template.render("{{xx:report}}", CTX, {})


class TestRegistry(unittest.TestCase):
    def test_resolve_par_name(self):
        reg = ModelRegistry({"ollama://mistral-small": {"name": "ollama://mistral-small"}})
        self.assertEqual(reg.resolve("ollama://mistral-small")["name"], "ollama://mistral-small")
        with self.assertRaises(ComponentInvalid):
            reg.resolve("ollama://other")

    def test_from_ldp(self):
        ldp = FakeLDP(
            members={"/ai_models/": ["https://pod/ai_models/1", "https://pod/ai_models/2"]},
            docs={
                "https://pod/ai_models/1": {"@id": "https://pod/ai_models/1",
                                             "https://pod/ns/app/ai_models#name": "ollama://mistral-small",
                                             "https://pod/ns/app/ai_models#endpoint": "http://ollama:11434/v1",
                                             "https://pod/ns/app/ai_models#model_id": "mistral-small",
                                             "https://pod/ns/app/ai_models#location": "local"},
                "https://pod/ai_models/2": {"https://pod/ns/app/ai_models#name": "remote://api.mistral/mistral-large",
                                             "https://pod/ns/app/ai_models#endpoint": "https://api.mistral.ai/v1",
                                             "https://pod/ns/app/ai_models#model_id": "mistral-large-latest",
                                             "https://pod/ns/app/ai_models#location": "remote"},
            })
        reg = ModelRegistry.from_ldp(ldp, "/ai_models/")
        self.assertEqual(set(reg.entries), {"ollama://mistral-small", "remote://api.mistral/mistral-large"})
        pub = reg.list_public()
        self.assertFalse(any("secret" in k.lower() or "key" in k.lower() for m in pub for k in m))  # F1.4


class TestEngineText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = StateStore(self.tmp, budget_max_runs=0)
        self.registry = ModelRegistry({"ollama://mistral-small": {
            "name": "ollama://mistral-small", "protocol": "openai_chat",
            "endpoint": "http://ollama:11434/v1", "model_id": "mistral-small", "location": "local"}})
        self.ldp = FakeLDP(docs={
            "https://pod/shapes": CONTRACT,
            "https://pod/interventions/42": {
                "@context": CTX,
                "@id": "https://pod/interventions/42",
                "https://pod/ns/app/interventions#report": "Pump replaced on floor 2",
            },
            "https://pod/interventions/": None,  # container (not fetched in perResource)
        }, members={"https://pod/interventions/": ["https://pod/interventions/1", "https://pod/interventions/2"]})
        self.ldp.docs["https://pod/interventions/1"] = {"https://pod/ns/app/interventions#report": "r1"}
        self.ldp.docs["https://pod/interventions/2"] = {"https://pod/ns/app/interventions#report": "r2"}
        self.calls = []
        def fake_model(entry, prompt, params, locale):
            self.calls.append((entry["name"], prompt, locale))
            return "OUTPUT-TEXT"
        self.engine = Engine(lambda token: self.ldp, self.registry, self.state, model_caller=fake_model)

    def test_run_sync_per_resource_ecrit_patch_avec_provenance(self):
        res = self.engine.run_sync(copy.deepcopy(COMPONENT), "https://pod/components/summary/1.0",
                                   "https://pod/interventions/42", "fr", token="tok")
        self.assertEqual(res, {"patched": {"resource": "https://pod/interventions/42", "field": "app:summary"}})
        iri, patch = self.ldp.patches[0]
        self.assertEqual(iri, "https://pod/interventions/42")
        self.assertEqual(patch["app:summary"], "OUTPUT-TEXT")
        self.assertEqual(patch["app:prov_model"], "ollama://mistral-small")
        self.assertEqual(patch["app:prov_comp"], "https://pod/components/summary/1.0")
        self.assertEqual(patch["app:prov_pv"], "1.0")
        self.assertEqual(patch["app:prov_locale"], "fr")
        self.assertIn("app:prov_date", patch)
        self.assertEqual(self.calls[0][1], "Sum: Pump replaced on floor 2")  # instantiated prompt
        self.assertEqual(self.calls[0][2], "fr")
        # counter F5.3
        self.assertEqual(self.state.counters()["https://pod/components/summary/1.0"]["byModel"]["ollama://mistral-small"], 1)

    def test_modele_inconnu_rejet_422_sans_ecriture(self):
        d = copy.deepcopy(COMPONENT); d["cmp:model"] = "ollama://unknown"
        with self.assertRaises(RunError) as ctx:
            self.engine.run_sync(d, "ref", "https://pod/interventions/42", "en", "tok")
        self.assertEqual(ctx.exception.http, 422)
        self.assertEqual(ctx.exception.code, "component_invalid")
        self.assertEqual(self.ldp.patches, [])

    def test_placeholder_hors_contrat_rejet_422(self):
        d = copy.deepcopy(COMPONENT); d["cmp:promptTemplate"] = "{{app:secret}}"
        with self.assertRaises(RunError) as ctx:
            self.engine.run_sync(d, "ref", "https://pod/interventions/42", "en", "tok")
        self.assertEqual(ctx.exception.code, "component_invalid")
        self.assertEqual(self.ldp.patches, [])

    def test_echec_modele_502_aucune_ecriture(self):
        from app import adapters
        def boom(entry, prompt, params, locale):
            raise adapters.ModelCallError("endpoint down")
        self.engine.model_caller = boom
        with self.assertRaises(RunError) as ctx:
            self.engine.run_sync(copy.deepcopy(COMPONENT), "ref", "https://pod/interventions/42", "en", "tok")
        self.assertEqual((ctx.exception.http, ctx.exception.code), (502, "model_call_failed"))
        self.assertEqual(self.ldp.patches, [])  # F2.3

    def _structured_doc(self):
        d = copy.deepcopy(COMPONENT)
        d["cmp:outputMode"] = "cmp:structured"
        d["cmp:outputTarget"] = "https://pod/summaries/"
        d["cmp:outputContract"] = "https://pod/apps/atelier/shapes"
        d["cmp:outputPackage"] = "https://pod/apps/atelier/meta"
        del d["cmp:outputField"]
        d["cmp:parameters"] = {"maxRetries": 1}
        return d

    def test_j16_structure_conforme_ecrit_apres_validation_shacl(self):
        out_contract = {"@graph": [
            {"@type": "sh:NodeShape", "sh:targetClass": {"@id": "https://pod/ns/app/store_atelier_summaries#"},
             "sh:property": [{"sh:path": {"@id": "https://pod/ns/app/store_atelier_summaries#summary_text"},
                              "sh:datatype": "http://www.w3.org/2001/XMLSchema#string", "sh:minCount": 1}]}]}
        self.ldp.docs["https://pod/apps/atelier/shapes"] = out_contract
        self.ldp.validate_reports = [{"sh:conforms": True, "sh:result": []}]
        self.engine.model_caller = lambda e, p, pa, l, **kw: '{"summary_text": "Summary OK"}'
        res = self.engine.run_sync(self._structured_doc(), "ref", "https://pod/interventions/42", "en", "tok")
        self.assertEqual(res, {"written": {"created": "https://pod/summaries/generated-1"}})
        # the validated graph contains the contract IRI + the provenance written afterwards
        vurl, vdoc = self.ldp.validated[0]
        self.assertIn("/apps/atelier/validate", vurl)
        self.assertEqual(vdoc["https://pod/ns/app/store_atelier_summaries#summary_text"], "Summary OK")
        container, posted = self.ldp.posts[0]
        self.assertEqual(posted["app:prov_model"], "ollama://mistral-small")

    def test_j16_non_conforme_apres_tentatives_422_rapport_sans_ecriture(self):
        out_contract = {"@graph": [{"sh:property": [
            {"sh:path": {"@id": "https://pod/ns/app/store_atelier_summaries#summary_text"}, "sh:minCount": 1}]}]}
        self.ldp.docs["https://pod/apps/atelier/shapes"] = out_contract
        bad = {"sh:conforms": False, "sh:result": [{"sh:resultPath": "summary_text",
                                                     "sh:resultMessage": "required"}]}
        self.ldp.validate_reports = [bad, bad]  # 1 attempt + 1 retry (maxRetries=1)
        prompts = []
        def model(e, p, pa, l, **kw):
            prompts.append(p)
            return '{"summary_text": "x"}'
        self.engine.model_caller = model
        with self.assertRaises(RunError) as ctx:
            self.engine.run_sync(self._structured_doc(), "ref", "https://pod/interventions/42", "en", "tok")
        self.assertEqual((ctx.exception.http, ctx.exception.code), (422, "output_validation_failed"))
        self.assertIn("validationReport", ctx.exception.extra)          # W3C report (F4.2/F4.3)
        self.assertEqual(self.ldp.posts, [])                            # no write (C3)
        self.assertEqual(len(prompts), 2)                               # counter-argumentation (F4.3)
        self.assertIn("Validation report", prompts[1])

    def test_run_async_per_container_document_autonome(self):
        d = copy.deepcopy(COMPONENT)
        d["cmp:cardinality"] = "cmp:perContainer"
        d["cmp:outputTarget"] = "https://pod/summaries/"
        d["cmp:outputContract"] = "https://pod/shapes"
        d["cmp:outputCore"] = True
        job = self.engine.run_async(d, "ref", "https://pod/interventions/", "en", "tok", "webid:1", is_test=True)
        import time
        for _ in range(100):
            j = self.state.get_job(job["job"])
            if j["state"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        j = self.state.get_job(job["job"])
        self.assertEqual(j["state"], "succeeded")
        self.assertEqual(j["written"], {"created": "https://pod/summaries/generated-1"})
        container, doc = self.ldp.posts[0]
        self.assertEqual(container, "https://pod/summaries/")
        self.assertEqual(doc["app:summary"], "OUTPUT-TEXT")
        self.assertEqual(doc["source_container"], "https://pod/interventions/")  # F3.2 rev 5
        # batch prompt: placeholder instantiated with the list of values (R1.5)
        self.assertIn('["r1", "r2"]', self.calls[0][1])


class TestBudget(unittest.TestCase):
    def test_seuil_budget_429(self):
        tmp = tempfile.mkdtemp()
        state = StateStore(tmp, budget_max_runs=2, budget_window_days=30)
        state.incr_counter("c", "m", is_test=False)
        state.incr_counter("c", "m", is_test=False)
        self.assertTrue(state.budget_exceeded("c"))
        self.assertFalse(state.budget_exceeded("other"))
        # tests do not consume the budget
        state2 = StateStore(tempfile.mkdtemp(), budget_max_runs=1)
        state2.incr_counter("c", "m", is_test=True)
        self.assertFalse(state2.budget_exceeded("c"))


class TestProvenance(unittest.TestCase):
    def test_champs(self):
        pv = provenance_values("ref", COMPONENT, "ollama://x", "fr")
        self.assertEqual(pv["app:prov_model"], "ollama://x")
        self.assertEqual(pv["app:prov_locale"], "fr")
        self.assertEqual(pv["app:prov_pv"], "1.0")


if __name__ == "__main__":
    unittest.main()
