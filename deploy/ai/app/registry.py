"""Model registry (F1.1–F1.4, annex B).

Storage: LDP container /ai_models/ (ai_models table of the deployed
model.json), one resource per model, read via LDP according to ACLs. No
credentials here (F1.4): remote endpoint secrets live in the service
environment variables (AI_MODEL_SECRET_<NAME>).
"""
from __future__ import annotations

from .template import ComponentInvalid


def _prop(doc: dict, local: str):
    """Reads a property from a served JSON-LD document (full or prefixed IRI)."""
    for k, v in doc.items():
        if k.startswith("@"):
            continue
        if k == local or k.endswith("#" + local) or k.endswith("/" + local):
            return v
    return None


class ModelRegistry:
    def __init__(self, entries: dict[str, dict]):
        # name -> entry; name = exact value of cmp:model (e.g. ollama://mistral-small)
        self.entries = entries

    def reload(self, ldp, container: str) -> int:
        """Reloads the registry from the LDP (in place). Returns the number of models."""
        fresh = ModelRegistry.from_ldp(ldp, container)
        self.entries = fresh.entries
        return len(self.entries)

    @classmethod
    def from_ldp(cls, ldp, container: str) -> "ModelRegistry":
        entries: dict[str, dict] = {}
        for iri in ldp.get_container_members(container):
            doc = ldp.get_json(iri)
            name = _prop(doc, "name")
            if not name:
                continue
            entries[str(name)] = {
                "name": str(name),
                "protocol": str(_prop(doc, "protocol") or "openai_chat"),
                "endpoint": str(_prop(doc, "endpoint") or ""),
                "model_id": str(_prop(doc, "model_id") or ""),
                "location": str(_prop(doc, "location") or "local"),
                "context_window": _prop(doc, "context_window"),
                "native_structured_output": bool(_prop(doc, "native_structured_output")),
                "jurisdiction": _prop(doc, "jurisdiction"),
                "iri": iri,
            }
        return cls(entries)

    def resolve(self, cmp_model: str) -> dict:
        """F1.2: resolution by strict equality on `name`. Unknown = rejection."""
        entry = self.entries.get(cmp_model)
        if entry is None:
            raise ComponentInvalid(
                f"cmp:model '{cmp_model}' missing from the model registry (F1.2)"
            )
        return entry

    def list_public(self) -> list[dict]:
        """Public view (editor): never any credential (F1.4)."""
        return [
            {k: v for k, v in e.items() if k != "iri"} | {"iri": e.get("iri", "")}
            for e in self.entries.values()
        ]
