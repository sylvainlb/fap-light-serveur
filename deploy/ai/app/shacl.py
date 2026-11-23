"""Validation SHACL des sorties structurées (F4.2) — réutilisation du moteur ext/shacl.

Aucun second validateur : la pile appelle l'endpoint de validation externe du
serveur de données (J3, ldp-shacl-api v1.1 : POST /apps/<slug>/validate[/<table>],
corps RDF/JSON-LD, réponse = W3C ValidationReport JSON-LD sh:conforms + sh:result[]).
Pour le modèle cœur, l'URL est dérivée de la racine (env SHACL_VALIDATE_URL_TEMPLATE
permet de surcharger, ex. "https://{base}/validate/{table}").
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

SH = "http://www.w3.org/ns/shacl#"


class ValidationFailed(Exception):
    def __init__(self, report: dict):
        self.report = report
        results = report.get(SH + "result", report.get("sh:result", []))
        msg = "; ".join(
            f"{r.get(SH + 'resultPath', r.get('sh:resultPath', '?'))}: "
            f"{r.get(SH + 'resultMessage', r.get('sh:resultMessage', ''))}"
            for r in results[:5]
        )
        super().__init__(f"non conforme ({len(results)} violation(s)): {msg}")


def validate_url_for(contract_url: str, output_target: str) -> str:
    """Dérive l'URL de validation J3 depuis le contrat épinglé et la cible.

    Package : https://pod/apps/<slug>/shapes + cible /apps/<slug>/<table>/
      -> https://pod/apps/<slug>/validate[/<table>]
    Cœur : https://pod/shapes + cible /<table>/ -> template env ou {base}/validate/<table>
    """
    template = os.environ.get("SHACL_VALIDATE_URL_TEMPLATE")
    if template:
        base = contract_url.split("/shapes")[0].rstrip("/")
        table = output_target.rstrip("/").rsplit("/", 1)[-1]
        return template.replace("{base}", base).replace("{slug}",
                          contract_url.split("/apps/")[-1].split("/")[0] if "/apps/" in contract_url else "").replace("{table}", table)
    if "/apps/" in contract_url:
        base, slug = contract_url.split("/apps/")[0], contract_url.split("/apps/")[1].split("/")[0]
        table = output_target.rstrip("/").rsplit("/", 1)[-1]
        # si la cible est sous le même package, restreindre à la shape de la table
        if f"/apps/{slug}/" in output_target:
            return f"{base}/apps/{slug}/{table}/validate"
        return f"{base}/apps/{slug}/validate"
    base = contract_url.split("/shapes")[0].rstrip("/")
    table = output_target.rstrip("/").rsplit("/", 1)[-1]
    return f"{base}/validate/{table}"


def conforms(report: dict) -> bool:
    c = report.get(SH + "conforms", report.get("sh:conforms"))
    return c is True or c == "true"


def validate_graph(ldp, data_doc: dict, validate_url: str) -> dict:
    """POST le graphe RDF/JSON-LD sur l'endpoint J3. Retourne le rapport W3C."""
    status, _, report = ldp._req("POST", validate_url, data_doc)
    if status != 200 or not isinstance(report, dict):
        raise ValidationFailed(report if isinstance(report, dict) else {"raw": report})
    return report
