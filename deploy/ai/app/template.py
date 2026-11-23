"""Instanciation du prompt paramétré (R1.1, R1.2).

Un placeholder est `{{prefix:propriete}}` ; il est résolu en IRI via le
@context du document, et doit référencer une propriété de la table source
déclarée dans le contrat de référence (R1.1). Tout placeholder non
résolvable est un rejet (ComponentInvalid).
"""
from __future__ import annotations

import json
import re

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+)\s*\}\}")


class ComponentInvalid(Exception):
    """Document composant invalide (schéma ou contrôles sémantiques)."""


def expand_iri(context: dict, prefixed: str) -> str:
    prefix, _, local = prefixed.partition(":")
    if prefix not in context:
        raise ComponentInvalid(f"placeholder {prefixed}: prefix '{prefix}' absent du @context")
    return context[prefix] + local


def extract_placeholders(template: str) -> list[str]:
    return PLACEHOLDER_RE.findall(template)


def validate_placeholders(template: str, context: dict, contract_props: set[str]) -> list[str]:
    """R1.1 : chaque placeholder doit référencer une propriété du contrat.

    Retourne la liste des IRIs de propriétés référencées.
    Lève ComponentInvalid si un placeholder est hors contrat.
    """
    iris = []
    for ph in extract_placeholders(template):
        iri = expand_iri(context, ph)
        if iri not in contract_props:
            raise ComponentInvalid(f"placeholder {ph} ({iri}) hors du contrat de référence")
        iris.append(iri)
    return iris


def render(template: str, context: dict, values: dict[str, object]) -> str:
    """Remplace chaque placeholder par la valeur de la propriété (IRI complète)."""

    def repl(m: re.Match) -> str:
        iri = expand_iri(context, m.group(1))
        if iri not in values:
            raise ComponentInvalid(f"valeur absente pour la propriété {iri}")
        v = values[iri]
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    return PLACEHOLDER_RE.sub(repl, template)


def resource_values(doc: dict) -> dict[str, object]:
    """Aplatit un document JSON-LD en {IRI propriété: valeur}.

    Les clés préfixées (`app:report`) sont résolues en IRIs via le
    @context du document ; les clés déjà absolues sont gardées telles quelles.
    """
    ctx = doc.get("@context", {}) if isinstance(doc.get("@context"), dict) else {}
    out: dict[str, object] = {}
    for k, v in doc.items():
        if k.startswith("@"):
            continue
        if ":" in k and not k.startswith("http"):
            prefix, _, local = k.partition(":")
            if prefix in ctx:
                out[ctx[prefix] + local] = v
                continue
        out[k] = v
    return out
