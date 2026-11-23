"""Propagation d'identité au LDP (F6.2) — arbitrage J15.

Mécanisme retenu par défaut : échange de jeton RFC 8693 (token exchange) —
le service échange le jeton du demandeur contre un jeton propre à son
identité (Keycloak 26.x, grant urn:ietf:params:oauth:grant-type:token-exchange,
requested_subject = webid). Aucun contact avec le cœur ni le PEP.
Repli dev (AI_TOKEN_EXCHANGE=0) : le Bearer du demandeur est forwardé tel quel.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error


class IdentityError(Exception):
    pass


def exchange_token(subject_token: str, webid: str,
                   token_url: str | None = None, client_id: str | None = None,
                   client_secret: str | None = None, timeout: float = 15.0) -> str:
    """RFC 8693 token exchange : retourne le access_token pour l'identité webid."""
    token_url = token_url or os.environ.get("IDP_TOKEN_URL", "")
    client_id = client_id or os.environ.get("AI_CLIENT_ID", "")
    client_secret = client_secret or os.environ.get("AI_CLIENT_SECRET", "")
    if not (token_url and client_id):
        raise IdentityError("token exchange non configuré (IDP_TOKEN_URL / AI_CLIENT_ID)")
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": subject_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "requested_subject": webid,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(token_url, data=form, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise IdentityError(f"token exchange -> HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from e
    token = data.get("access_token")
    if not token:
        raise IdentityError("token exchange sans access_token dans la réponse")
    return token


def ldp_token_for(incoming_bearer: str | None, webid: str) -> str | None:
    """Retourne le jeton à présenter au LDP pour agir comme `webid`."""
    if os.environ.get("AI_TOKEN_EXCHANGE", "0") == "1":
        if not incoming_bearer:
            return None
        return exchange_token(incoming_bearer, webid)
    return incoming_bearer  # repli dev : forward du Bearer
