"""Model abstraction (F1.1, F1.2, C4).

The URI scheme of `name` (ollama://, remote://…) selects the protocol
adapter; in v1 all adapters speak the OpenAI-compatible API
(`POST {endpoint}/chat/completions`). Interchangeability lives at this
abstraction level: switching models changes neither the code nor the host (C4).

Credentials (F1.4): never in the registry; read from the service
secrets, env `AI_MODEL_SECRET_<NORMALIZED_NAME>` (name without the scheme
and special characters), sent only to the declared endpoint.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error


class ModelCallError(Exception):
    """Model call failure (F2.3: reported, never a compensating write)."""


def _secret_env_name(name: str) -> str:
    base = name.split("://", 1)[-1]
    return "AI_MODEL_SECRET_" + re.sub(r"[^A-Z0-9]+", "_", base.upper()).strip("_")


def _secret_for(name: str) -> str | None:
    return os.environ.get(_secret_env_name(name))


def adapter_for(name: str) -> str:
    scheme = name.split("://", 1)[0]
    if scheme in ("ollama", "remote", "openai"):
        return "openai_chat"
    raise ModelCallError(f"unknown URI scheme for model '{name}'")


def call_model(entry: dict, prompt: str, params: dict, locale: str, timeout: float = 120.0,
               response_format: dict | None = None, system_extra: str | None = None) -> str:
    """Synchronous call to the declared model. Returns the raw output text."""
    protocol = adapter_for(entry["name"])
    if protocol != "openai_chat":
        raise ModelCallError(f"protocol not supported in v1: {protocol}")
    if entry.get("protocol") not in (None, "openai_chat"):
        raise ModelCallError(f"registry protocol not supported in v1: {entry['protocol']}")

    endpoint = entry["endpoint"].rstrip("/")
    url = f"{endpoint}/chat/completions"
    system = f"You are a precise assistant. Output locale: {locale}."
    if system_extra:
        system += " " + system_extra
    body = {
        "model": entry["model_id"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": params.get("temperature", 0.2),
    }
    if "maxOutputLength" in params:
        body["max_tokens"] = params["maxOutputLength"]
    if response_format:
        body["response_format"] = response_format  # native structured output (F4.1)

    headers = {"Content-Type": "application/json"}
    secret = _secret_for(entry["name"])
    if entry.get("location") == "remote" and secret:
        headers["Authorization"] = f"Bearer {secret}"

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ModelCallError(f"endpoint {url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ModelCallError(f"endpoint {url} unreachable: {e}") from e

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ModelCallError(f"malformed model response: {e}") from e
