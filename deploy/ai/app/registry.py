"""Registre de modèles (F1.1–F1.4, annexe B).

Support : conteneur LDP /ai_models/ (table ai_models du model.json déployé),
une ressource par modèle, lu via LDP selon les ACL. Aucun credential ici
(F1.4) : les secrets des endpoints distants vivent dans les variables
d'environnement du service (AI_MODEL_SECRET_<NAME>).
"""
from __future__ import annotations

from .template import ComponentInvalid


def _prop(doc: dict, local: str):
    """Lit une propriété dans un document JSON-LD servi (IRI complète ou préfixée)."""
    for k, v in doc.items():
        if k.startswith("@"):
            continue
        if k == local or k.endswith("#" + local) or k.endswith("/" + local):
            return v
    return None


class ModelRegistry:
    def __init__(self, entries: dict[str, dict]):
        # name -> entry ; name = valeur exacte de cmp:model (ex. ollama://mistral-small)
        self.entries = entries

    def reload(self, ldp, container: str) -> int:
        """Recharge le registre depuis le LDP (in place). Retourne le nb de modèles."""
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
        """F1.2 : résolution par égalité stricte sur `name`. Inconnu = rejet."""
        entry = self.entries.get(cmp_model)
        if entry is None:
            raise ComponentInvalid(
                f"cmp:model '{cmp_model}' absent du registre de modèles (F1.2)"
            )
        return entry

    def list_public(self) -> list[dict]:
        """Vue publique (éditeur) : jamais de credential (F1.4)."""
        return [
            {k: v for k, v in e.items() if k != "iri"} | {"iri": e.get("iri", "")}
            for e in self.entries.values()
        ]
