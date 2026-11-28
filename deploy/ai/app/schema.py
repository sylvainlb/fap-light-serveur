"""Structural validation of the AI component document (annex A, J14).

The schema is extracted verbatim from the spec (spec/app-ai-component-editor.md,
annex A) into schema/ai-component.schema.json — a single source of truth.
Semantic checks (placeholders, contracts, provenance): outside the schema,
performed by the engine at publish/run time (F7.1).
"""
from __future__ import annotations

import json
import os

import jsonschema

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema", "ai-component.schema.json")

_validator: jsonschema.Draft202012Validator | None = None


def validator() -> jsonschema.Draft202012Validator:
    global _validator
    if _validator is None:
        with open(_SCHEMA_PATH) as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)
        # rev 5: format: uri is annotative by default -> enable format validation
        _validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return _validator


def validate_component(doc) -> list[str]:
    """Returns the list of schema errors (empty = valid)."""
    if not isinstance(doc, dict):
        return ["document is not an object"]
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator().iter_errors(doc)]
