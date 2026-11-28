"""Instantiation of the parameterized prompt (R1.1, R1.2).

A placeholder is `{{prefix:property}}`; it is resolved to an IRI via the
document's @context, and must reference a property of the source table
declared in the reference contract (R1.1). Any unresolvable placeholder
is a rejection (ComponentInvalid).
"""
from __future__ import annotations

import json
import re

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+)\s*\}\}")


class ComponentInvalid(Exception):
    """Invalid component document (schema or semantic checks)."""


def expand_iri(context: dict, prefixed: str) -> str:
    prefix, _, local = prefixed.partition(":")
    if prefix not in context:
        raise ComponentInvalid(f"placeholder {prefixed}: prefix '{prefix}' missing from @context")
    return context[prefix] + local


def extract_placeholders(template: str) -> list[str]:
    return PLACEHOLDER_RE.findall(template)


def validate_placeholders(template: str, context: dict, contract_props: set[str]) -> list[str]:
    """R1.1: each placeholder must reference a property of the contract.

    Returns the list of referenced property IRIs.
    Raises ComponentInvalid if a placeholder is outside the contract.
    """
    iris = []
    for ph in extract_placeholders(template):
        iri = expand_iri(context, ph)
        if iri not in contract_props:
            raise ComponentInvalid(f"placeholder {ph} ({iri}) outside the reference contract")
        iris.append(iri)
    return iris


def render(template: str, context: dict, values: dict[str, object]) -> str:
    """Replaces each placeholder with the property value (full IRI)."""

    def repl(m: re.Match) -> str:
        iri = expand_iri(context, m.group(1))
        if iri not in values:
            raise ComponentInvalid(f"missing value for property {iri}")
        v = values[iri]
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    return PLACEHOLDER_RE.sub(repl, template)


def resource_values(doc: dict) -> dict[str, object]:
    """Flattens a JSON-LD document into {property IRI: value}.

    Prefixed keys (`app:report`) are resolved to IRIs via the document's
    @context; already-absolute keys are kept as-is.
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
