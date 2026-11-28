"""Identity propagation to the LDP (F6.2) — J15 arbitration.

Default mechanism: RFC 8693 token exchange — the service exchanges the
requester's token for a token of its own identity (Keycloak 26.x, grant
urn:ietf:params:oauth:grant-type:token-exchange, requested_subject =
webid). No contact with the core or the PEP.
Dev fallback (AI_TOKEN_EXCHANGE=0): the requester's Bearer is forwarded as-is.
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
    """RFC 8693 token exchange: returns the access_token for the webid identity."""
    token_url = token_url or os.environ.get("IDP_TOKEN_URL", "")
    client_id = client_id or os.environ.get("AI_CLIENT_ID", "")
    client_secret = client_secret or os.environ.get("AI_CLIENT_SECRET", "")
    if not (token_url and client_id):
        raise IdentityError("token exchange not configured (IDP_TOKEN_URL / AI_CLIENT_ID)")
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
        raise IdentityError("token exchange response without access_token")
    return token


def ldp_token_for(incoming_bearer: str | None, webid: str) -> str | None:
    """Returns the token to present to the LDP to act as `webid`."""
    if os.environ.get("AI_TOKEN_EXCHANGE", "0") == "1":
        if not incoming_bearer:
            return None
        return exchange_token(incoming_bearer, webid)
    return incoming_bearer  # dev fallback: forward the Bearer
