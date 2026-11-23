"""Abstraction des modèles (F1.1, F1.2, C4).

Le schéma d'URI du `name` (ollama://, remote://…) sélectionne l'adaptateur
de protocole ; en v1 tous les adaptateurs parlent l'API compatible OpenAI
(`POST {endpoint}/chat/completions`). L'interchangeabilité porte sur cette
abstraction : changer de modèle ne change ni le code ni l'hôte (C4).

Credentials (F1.4) : jamais dans le registre ; lus dans les secrets du
service, env `AI_MODEL_SECRET_<NAME_NORMALISE>` (name sans le schéma et
les caractères spéciaux), envoyés uniquement vers l'endpoint déclaré.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error


class ModelCallError(Exception):
    """Échec d'appel modèle (F2.3 : rapporté, jamais d'écriture compensatoire)."""


def _secret_env_name(name: str) -> str:
    base = name.split("://", 1)[-1]
    return "AI_MODEL_SECRET_" + re.sub(r"[^A-Z0-9]+", "_", base.upper()).strip("_")


def _secret_for(name: str) -> str | None:
    return os.environ.get(_secret_env_name(name))


def adapter_for(name: str) -> str:
    scheme = name.split("://", 1)[0]
    if scheme in ("ollama", "remote", "openai"):
        return "openai_chat"
    raise ModelCallError(f"schéma d'URI inconnu pour le modèle '{name}'")


def call_model(entry: dict, prompt: str, params: dict, locale: str, timeout: float = 120.0,
               response_format: dict | None = None, system_extra: str | None = None) -> str:
    """Appel synchrone au modèle déclaré. Retourne le texte brut de sortie."""
    protocol = adapter_for(entry["name"])
    if protocol != "openai_chat":
        raise ModelCallError(f"protocole non supporté en v1: {protocol}")
    if entry.get("protocol") not in (None, "openai_chat"):
        raise ModelCallError(f"protocole du registre non supporté en v1: {entry['protocol']}")

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
        body["response_format"] = response_format  # sortie structurée native (F4.1)

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
        raise ModelCallError(f"endpoint {url} injoignable: {e}") from e

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ModelCallError(f"réponse du modèle mal formée: {e}") from e
