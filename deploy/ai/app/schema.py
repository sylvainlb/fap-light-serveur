"""Validation structurelle du document composant IA (annexe A, J14).

Le schéma est extrait tel quel de la spec (spec/app-ai-component-editor.md,
annexe A) vers schema/ai-component.schema.json — une seule source de vérité.
Contrôles sémantiques (placeholders, contrats, provenance) : hors schéma,
faits par le moteur à la publication/exécution (F7.1).
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
        # rev 5 : format: uri est annotatif par défaut -> on active la validation de format
        _validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return _validator


def validate_component(doc) -> list[str]:
    """Retourne la liste des erreurs de schéma (vide = valide)."""
    if not isinstance(doc, dict):
        return ["document non objet"]
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator().iter_errors(doc)]
