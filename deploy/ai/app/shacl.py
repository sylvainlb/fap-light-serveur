"""SHACL validation of structured outputs (F4.2) — reuse of the ext/shacl engine.

No second validator: the stack calls the external validation endpoint of
the data server (J3, ldp-shacl-api v1.1: POST /apps/<slug>/validate[/<table>],
RDF/JSON-LD body, response = W3C ValidationReport JSON-LD sh:conforms + sh:result[]).
For the core model, the URL is derived from the root (env
SHACL_VALIDATE_URL_TEMPLATE allows overriding, e.g. "https://{base}/validate/{table}").
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
        super().__init__(f"non-conformant ({len(results)} violation(s)): {msg}")


def validate_url_for(contract_url: str, output_target: str) -> str:
    """Derives the J3 validation URL from the pinned contract and the target.

    Package: https://pod/apps/<slug>/shapes + target /apps/<slug>/<table>/
      -> https://pod/apps/<slug>/validate[/<table>]
    Core: https://pod/shapes + target /<table>/ -> env template or {base}/validate/<table>
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
        # if the target is under the same package, restrict to the table shape
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
    """POSTs the RDF/JSON-LD graph to the J3 endpoint. Returns the W3C report."""
    status, _, report = ldp._req("POST", validate_url, data_doc)
    if status != 200 or not isinstance(report, dict):
        raise ValidationFailed(report if isinstance(report, dict) else {"raw": report})
    return report
