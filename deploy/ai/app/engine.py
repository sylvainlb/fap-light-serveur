"""Execution engine (F2.1–F2.4) — J15: text outputs.

Per-resource flow (synchronous, F2.2/F3.1): LDP read of the resource,
prompt instantiation, model call, PATCH of `cmp:outputField` with the
provenance columns (R1.8/R1.9). Per-container flow (asynchronous, job):
one run over the batch (R1.5), placeholders instantiated with the list of
batch values, output = autonomous document POSTed into `cmp:outputTarget`
carrying `source_container` (F3.2 rev 5).

J16 (structured outputs + SHACL) is not delivered here: `cmp:structured`
returns 400 invalid_request until then.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from . import adapters, shacl, structured, template
from .schema import validate_component
from .template import ComponentInvalid


from .errors import RunError  # re-exported for server.py


def contract_properties(contract_doc: dict) -> set[str]:
    """Extracts the sh:path values from the published contract (JSON-LD PropertyShape)."""
    props: set[str] = set()
    graph = contract_doc.get("@graph", [contract_doc])
    for node in graph:
        if not isinstance(node, dict):
            continue
        path = node.get("sh:path") or node.get("http://www.w3.org/ns/shacl#path")
        if isinstance(path, dict):
            path = path.get("@id")
        if isinstance(path, str):
            props.add(path)
    return props


def provenance_values(component_ref: str, doc: dict, model_name: str, locale: str) -> dict:
    pf = doc.get("cmp:provenanceFields", {})
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        pf.get("cmp:modelField", "app:provenance_model"): model_name,
        pf.get("cmp:componentField", "app:provenance_component"): component_ref,
        pf.get("cmp:promptVersionField", "app:provenance_prompt_version"): doc.get("cmp:promptVersion", ""),
        pf.get("cmp:runDateField", "app:provenance_run_date"): now,
        pf.get("cmp:localeField", "app:provenance_locale"): locale,
    }


class Engine:
    def __init__(self, ldp_factory, registry, state, model_caller=None):
        self.ldp_factory = ldp_factory          # token -> LDPClient
        self.registry = registry
        self.state = state
        self.model_caller = model_caller or adapters.call_model

    # ------------------------------------------------------------ validation
    def _validate(self, doc: dict, token: str | None = None) -> None:
        errs = validate_component(doc)
        if errs:
            raise RunError(422, "component_invalid", "; ".join(errs[:5]))
        try:
            self.registry.resolve(doc["cmp:model"])
        except ComponentInvalid as e:
            raise RunError(422, "component_invalid", str(e)) from e
        ldp = self.ldp_factory(token)
        contract = ldp.get_json(doc["cmp:contract"])
        props = contract_properties(contract)
        try:
            template.validate_placeholders(doc["cmp:promptTemplate"], doc.get("@context", {}), props)
        except ComponentInvalid as e:
            raise RunError(422, "component_invalid", str(e)) from e

    # ------------------------------------------------------------- execution
    def _prompt_for(self, doc, ldp, source: str, lot: bool = False) -> str:
        if lot:
            members = ldp.get_container_members(source)
            lot_values: dict[str, list] = {}
            for m in members:
                for k, v in template.resource_values(ldp.get_json(m)).items():
                    lot_values.setdefault(k, []).append(v)
            return template.render(doc["cmp:promptTemplate"], doc.get("@context", {}), lot_values)
        src_doc = ldp.get_json(source)
        return template.render(doc["cmp:promptTemplate"], doc.get("@context", {}),
                               template.resource_values(src_doc))

    def run_sync(self, doc: dict, component_ref: str, source: str, locale: str, token: str) -> dict:
        """Synchronous execution (perResource). Returns the 200 body."""
        self._validate(doc, token)
        if doc.get("cmp:cardinality") != "cmp:perResource":
            raise RunError(400, "invalid_request", "synchronous execution requires cmp:perResource (F2.2)")
        if self.state.budget_exceeded(component_ref):
            raise RunError(429, "budget_exceeded", "budget threshold exceeded for this component (F6.3)")
        entry = self.registry.resolve(doc["cmp:model"])
        ldp = self.ldp_factory(token)
        prompt = self._prompt_for(doc, ldp, source)
        prov = provenance_values(component_ref, doc, entry["name"], locale)
        if doc.get("cmp:outputMode") == "cmp:text":
            try:
                text = self.model_caller(entry, prompt, doc.get("cmp:parameters", {}), locale)
            except adapters.ModelCallError as e:
                raise RunError(502, "model_call_failed", str(e)) from e   # F2.3: no write
            ldp.patch(source, {doc["cmp:outputField"]: text, **prov})
            self.state.incr_counter(component_ref, entry["name"], is_test=False)
            return {"patched": {"resource": source, "field": doc["cmp:outputField"]}}
        # J16: structured output (F4.1-F4.4)
        contract = ldp.get_json(doc["cmp:outputContract"])
        vurl = shacl.validate_url_for(doc["cmp:outputContract"], doc["cmp:outputTarget"])
        try:
            out_doc, _report = structured.generate_structured(
                entry, doc, contract, prompt, doc.get("cmp:parameters", {}), locale,
                self.model_caller, ldp, vurl)
        except adapters.ModelCallError as e:
            raise RunError(502, "model_call_failed", str(e)) from e
        out_doc.update(prov)
        loc = ldp.post(doc["cmp:outputTarget"], out_doc)   # F4.4: standard path (SQL judges)
        self.state.incr_counter(component_ref, entry["name"], is_test=False)
        return {"written": {"created": loc}}

    def run_async(self, doc: dict, component_ref: str, source: str, locale: str, token: str,
                  requester: str, is_test: bool) -> dict:
        """Asynchronous execution (text per container): 202 + job, processed in a thread."""
        self._validate(doc, token)
        if doc.get("cmp:cardinality") != "cmp:perContainer":
            raise RunError(400, "invalid_request", "asynchronous execution requires cmp:perContainer (F2.2)")
        if self.state.budget_exceeded(component_ref):
            raise RunError(429, "budget_exceeded", "budget threshold exceeded for this component (F6.3)")
        job = self.state.create_job(component_ref, source, requester, is_test)
        t = threading.Thread(target=self._run_container_job,
                             args=(job["job"], doc, component_ref, source, locale, token), daemon=True)
        t.start()
        return job

    def _run_container_job(self, job_id: str, doc: dict, component_ref: str, source: str,
                           locale: str, token: str) -> None:
        self.state.update_job(job_id, state="running")
        try:
            entry = self.registry.resolve(doc["cmp:model"])
            ldp = self.ldp_factory(token)
            prompt = self._prompt_for(doc, ldp, source, lot=True)  # one run over the batch (R1.5)
            prov = provenance_values(component_ref, doc, entry["name"], locale)
            if doc.get("cmp:outputMode") == "cmp:text":
                text = self.model_caller(entry, prompt, doc.get("cmp:parameters", {}), locale)
                out_doc = {doc["cmp:outputField"]: text, "source_container": source, **prov}
            else:
                contract = ldp.get_json(doc["cmp:outputContract"])
                vurl = shacl.validate_url_for(doc["cmp:outputContract"], doc["cmp:outputTarget"])
                out_doc, _ = structured.generate_structured(
                    entry, doc, contract, prompt, doc.get("cmp:parameters", {}), locale,
                    self.model_caller, ldp, vurl)
                out_doc.update(prov)
            loc = ldp.post(doc["cmp:outputTarget"], out_doc)
            self.state.incr_counter(component_ref, entry["name"], is_test=False)
            self.state.update_job(job_id, state="succeeded", written={"created": loc})
        except RunError as e:  # non-conformant after retries (F4.3): report, no write
            self.state.update_job(job_id, state="failed",
                                  error={"code": e.code, "message": e.message, **e.extra})
        except adapters.ModelCallError as e:
            self.state.update_job(job_id, state="failed",
                                  error={"code": "model_call_failed", "message": str(e)})
        except Exception as e:  # noqa: BLE001 — isolated job, never a partial write (F4.3)
            self.state.update_job(job_id, state="failed",
                                  error={"code": "internal", "message": str(e)})


def component_ref_for(component: str | None, draft: dict | None) -> str:
    if component:
        return component
    return "draft:" + hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()[:16]
