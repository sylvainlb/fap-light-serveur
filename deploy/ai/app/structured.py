"""Structured outputs (F4.1–F4.3, J16).

Generation constrained by the output table's shape: if the model has
`native_structured_output`, we send `response_format` (JSON schema derived
from the contract); otherwise prompt engineering (schema in the prompt,
"JSON only") + parsing. The output is converted to JSON-LD (IRIs from the
contract's sh:path) then validated by SHACL via the ext/shacl endpoint
(shacl.py). Non-conformant -> maxRetries attempts with counter-argumentation
(the report is sent back to the model), then clean stop WITHOUT writing (F4.3, C3).
"""
from __future__ import annotations

import json
import re

from . import shacl
from .errors import RunError

SH = "http://www.w3.org/ns/shacl#"


def _local(iri: str) -> str:
    return iri.split("#")[-1].split("/")[-1]


def shape_of(contract_doc: dict) -> dict:
    """Extracts the shape (properties) from the JSON-LD contract: {local: {iri, datatype, ...}}.

    The contract served by ext/shacl embeds sh:property inline in the
    NodeShape; we walk @graph + sh:property recursively.
    """
    props: dict[str, dict] = {}

    def walk(node):
        if not isinstance(node, dict):
            return
        path = node.get("sh:path") or node.get(SH + "path")
        if isinstance(path, dict):
            path = path.get("@id")
        if isinstance(path, str):
            props[_local(path)] = {
                "iri": path,
                "datatype": (node.get("sh:datatype") or node.get(SH + "datatype") or "").split("#")[-1] or None,
                "minCount": node.get("sh:minCount") or node.get(SH + "minCount"),
                "in": node.get("sh:in") or node.get(SH + "in"),
                "nodeKind": (node.get("sh:nodeKind") or node.get(SH + "nodeKind") or ""),
            }
        for sub in node.get("sh:property", node.get(SH + "property", [])) or []:
            walk(sub)

    for node in contract_doc.get("@graph", [contract_doc]):
        walk(node)
    return props


def json_schema_for(props: dict) -> dict:
    """JSON Schema derived from the shape (for native response_format, F4.1)."""
    properties, required = {}, []
    for local, p in props.items():
        t = {"string": "string", "integer": "integer", "decimal": "number",
             "boolean": "boolean", "dateTime": "string"}.get(p["datatype"], "string")
        sch = {"type": t}
        if p.get("in"):
            sch["enum"] = p["in"] if isinstance(p["in"], list) else [p["in"]]
        properties[local] = sch
        if p.get("minCount"):
            required.append(local)
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def extract_json(text: str) -> dict:
    """Extracts the first JSON object from the model's raw output."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the output")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("truncated JSON in the output")


def to_jsonld(parsed: dict, props: dict, class_iri: str | None = None) -> dict:
    """Converts the model's JSON into a JSON-LD document (contract IRIs)."""
    doc: dict = {"@context": {"sh": SH}}
    if class_iri:
        doc["@type"] = class_iri
    for local, value in parsed.items():
        p = props.get(local)
        if p is None:
            # tolerance: unknown key -> ignored (SHACL will judge if relevant)
            continue
        doc[p["iri"]] = value
    return doc


def class_of(contract_doc: dict) -> str | None:
    graph = contract_doc.get("@graph", [contract_doc])
    for node in graph:
        if isinstance(node, dict) and (node.get("@type") == "sh:NodeShape" or node.get(SH + "targetClass") or "sh:targetClass" in node):
            tc = node.get("sh:targetClass") or node.get(SH + "targetClass")
            if isinstance(tc, dict):
                return tc.get("@id")
            return tc
    return None


def generate_structured(entry, component, contract_doc, base_prompt, params, locale,
                        model_caller, ldp, validate_url) -> tuple[dict, dict]:
    """Loop generation -> parsing -> conversion -> SHACL, with maxRetries (F4.3).

    Returns (conforming jsonld_doc, report). Raises RunError(422 output_validation_failed)
    with the W3C report in extra after retries are exhausted — no write.
    """
    props = shape_of(contract_doc)
    schema = json_schema_for(props)
    max_retries = int(params.get("maxRetries", 2))
    prompt = base_prompt
    report = None
    for attempt in range(max_retries + 1):
        text = model_caller(entry, prompt, params, locale,
                            response_format=({"type": "json_schema", "json_schema": schema}
                                             if entry.get("native_structured_output") else None),
                            system_extra=("Output JSON only, conforming to this JSON schema: "
                                          + json.dumps(schema)) if not entry.get("native_structured_output") else None)
        try:
            parsed = extract_json(text)
            doc = to_jsonld(parsed, props, class_of(contract_doc))
        except (ValueError, json.JSONDecodeError) as e:
            prompt = (f"{base_prompt}\n\nYour previous output was not valid JSON ({e}). "
                      f"Output JSON only, conforming to: {json.dumps(schema)}")
            continue
        report = shacl.validate_graph(ldp, doc, validate_url)
        if shacl.conforms(report):
            return doc, report
        # counter-argumentation with the error report (F4.3)
        prompt = (f"{base_prompt}\n\nYour previous output did not conform to the contract. "
                  f"Validation report: {json.dumps(report, ensure_ascii=False)[:4000]}\n"
                  f"Fix every violation and output JSON only, conforming to: {json.dumps(schema)}")
    raise RunError(422, "output_validation_failed",
                   "non-conformant output after retries — no write (F4.3)",
                   extra={"validationReport": report})
