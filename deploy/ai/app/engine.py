"""Moteur d'exécution (F2.1–F2.4) — J15 : sorties texte.

Flux par ressource (synchrone, F2.2/F3.1) : lecture LDP de la ressource,
instanciation du prompt, appel modèle, PATCH de `cmp:outputField` avec les
colonnes de provenance (R1.8/R1.9). Flux par conteneur (asynchrone, job) :
une exécution sur le lot (R1.5), placeholders instanciés avec la liste des
valeurs du lot, sortie = document autonome dans `cmp:outputTarget` portant
`source_container` (F3.2 rev 5).

J16 (sorties structurées + SHACL) n'est pas livré ici : `cmp:structured`
retourne 400 invalid_request en attendant.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from . import adapters, shacl, structured, template
from .schema import validate_component
from .template import ComponentInvalid


from .errors import RunError  # réexporté pour server.py


def contract_properties(contract_doc: dict) -> set[str]:
    """Extrait les sh:path du contrat publié (JSON-LD PropertyShape)."""
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

    # ------------------------------------------------------------- exécution
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
        """Exécution synchrone (perResource). Retourne le corps 200."""
        self._validate(doc, token)
        if doc.get("cmp:cardinality") != "cmp:perResource":
            raise RunError(400, "invalid_request", "l'exécution synchrone exige cmp:perResource (F2.2)")
        if self.state.budget_exceeded(component_ref):
            raise RunError(429, "budget_exceeded", "seuil de budget dépassé pour ce composant (F6.3)")
        entry = self.registry.resolve(doc["cmp:model"])
        ldp = self.ldp_factory(token)
        prompt = self._prompt_for(doc, ldp, source)
        prov = provenance_values(component_ref, doc, entry["name"], locale)
        if doc.get("cmp:outputMode") == "cmp:text":
            try:
                text = self.model_caller(entry, prompt, doc.get("cmp:parameters", {}), locale)
            except adapters.ModelCallError as e:
                raise RunError(502, "model_call_failed", str(e)) from e   # F2.3 : aucune écriture
            ldp.patch(source, {doc["cmp:outputField"]: text, **prov})
            self.state.incr_counter(component_ref, entry["name"], is_test=False)
            return {"patched": {"resource": source, "field": doc["cmp:outputField"]}}
        # J16 : sortie structurée (F4.1-F4.4)
        contract = ldp.get_json(doc["cmp:outputContract"])
        vurl = shacl.validate_url_for(doc["cmp:outputContract"], doc["cmp:outputTarget"])
        try:
            out_doc, _report = structured.generate_structured(
                entry, doc, contract, prompt, doc.get("cmp:parameters", {}), locale,
                self.model_caller, ldp, vurl)
        except adapters.ModelCallError as e:
            raise RunError(502, "model_call_failed", str(e)) from e
        out_doc.update(prov)
        loc = ldp.post(doc["cmp:outputTarget"], out_doc)   # F4.4 : voie standard (SQL juge)
        self.state.incr_counter(component_ref, entry["name"], is_test=False)
        return {"written": {"created": loc}}

    def run_async(self, doc: dict, component_ref: str, source: str, locale: str, token: str,
                  requester: str, is_test: bool) -> dict:
        """Exécution asynchrone (texte par conteneur) : 202 + job, traitement en thread."""
        self._validate(doc, token)
        if doc.get("cmp:cardinality") != "cmp:perContainer":
            raise RunError(400, "invalid_request", "l'exécution asynchrone exige cmp:perContainer (F2.2)")
        if self.state.budget_exceeded(component_ref):
            raise RunError(429, "budget_exceeded", "seuil de budget dépassé pour ce composant (F6.3)")
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
            prompt = self._prompt_for(doc, ldp, source, lot=True)  # une exécution sur le lot (R1.5)
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
        except RunError as e:  # non-conformité après tentatives (F4.3) : rapport, aucune écriture
            self.state.update_job(job_id, state="failed",
                                  error={"code": e.code, "message": e.message, **e.extra})
        except adapters.ModelCallError as e:
            self.state.update_job(job_id, state="failed",
                                  error={"code": "model_call_failed", "message": str(e)})
        except Exception as e:  # noqa: BLE001 — job isolé, jamais d'écriture partielle (F4.3)
            self.state.update_job(job_id, state="failed",
                                  error={"code": "internal", "message": str(e)})


def component_ref_for(component: str | None, draft: dict | None) -> str:
    if component:
        return component
    return "draft:" + hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()[:16]
