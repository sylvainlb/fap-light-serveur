"""Client d'accès à la cible FAP-light sous test.

Charge la configuration depuis un fichier .properties (style Java) avec
surcharge par variables d'environnement préfixées TCK_ :
  tck.properties:  dsp.base.url=http://localhost:1880
  env:             TCK_DSP_BASE_URL=http://vm:1880

Trois canaux d'accès :
  - PostgreSQL via le binaire `psql` (ou un wrapper, ex. `docker compose ... exec -T postgres psql`)
    configurable par `pg.cmd`, exécuté une fois par rôle (admin/gate/reader/app) ;
  - HTTP (urllib, stdlib) vers ORCE : catalogue DSP, data plane DSP, endpoints d'ingestion ;
  - SFTP (client OpenSSH `sftp` en subprocess, auth par clé) pour les dépôts de fichiers CA-S2.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
TCK_DIR = HARNESS_DIR.parent
FIXTURES_DIR = HARNESS_DIR / "fixtures"
EXPECTED_DIR = TCK_DIR / "expected"


class ConfigError(RuntimeError):
    pass


class PgError(RuntimeError):
    """Échec d'une requête psql (ex. permission denied) — porte le stderr PG."""

    def __init__(self, sql: str, returncode: int, stderr: str):
        self.sql = sql
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"psql a échoué (rc={returncode}): {stderr.strip()[:400]}")


def load_properties(path: str | Path) -> dict[str, str]:
    props: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"fichier de propriétés introuvable: {p}")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
        elif ":" in line:
            k, v = line.split(":", 1)
        else:
            continue
        props[k.strip()] = v.strip()
    # Surcharge par environnement : TCK_DSP_BASE_URL -> dsp.base.url
    for k in list(props):
        env = "TCK_" + k.upper().replace(".", "_").replace("-", "_")
        if env in os.environ:
            props[k] = os.environ[env]
    for env, val in os.environ.items():
        if env.startswith("TCK_"):
            key = env[4:].lower().replace("_", ".")
            props.setdefault(key, val)
    return props


class Target:
    """Accès configuré à la FAP-light sous test."""

    def __init__(self, props: dict[str, str]):
        self.props = props
        self.run_id = os.environ.get("TCK_RUN_ID", time.strftime("%Y%m%dT%H%M%SZ"))
        self.marker = f"tck-{self.run_id}".lower()

    # ---------------------------------------------------------------- config
    def get(self, key: str, default: str | None = None) -> str:
        if key in self.props:
            return self.props[key]
        if default is not None:
            return default
        raise ConfigError(f"propriété requise manquante: {key}")

    def get_int(self, key: str, default: int) -> int:
        return int(self.get(key, str(default)))

    def opt(self, key: str) -> str | None:
        v = self.props.get(key)
        return v if v not in (None, "", "CHANGE_ME") else None

    def require(self, key: str) -> str:
        v = self.opt(key)
        if v is None:
            raise ConfigError(f"propriété requise manquante ou non renseignée: {key}")
        return v

    # ------------------------------------------------------------- postgres
    def _pg_cmd(self, role: str) -> list[str]:
        base = shlex.split(self.get("pg.cmd", "psql"))
        return base + [
            "-h", self.get("pg.host", "localhost"),
            "-p", self.get("pg.port", "5432"),
            "-U", self.get(f"pg.{role}.user", role),
            "-d", self.get("pg.db", "fap"),
            "-X", "-q", "-v", "ON_ERROR_STOP=1",
            "-A", "-t", "-F", "\t",
        ]

    def pg_sql(self, sql: str, role: str = "admin") -> list[list[str]]:
        """Exécute du SQL via psql en tant que `role`, retourne les lignes (liste de champs)."""
        password = self.require(f"pg.{role}.password")
        env = dict(os.environ, PGPASSWORD=password)
        proc = subprocess.run(
            self._pg_cmd(role) + ["-c", sql],
            capture_output=True, text=True, env=env, timeout=120,
        )
        if proc.returncode != 0:
            raise PgError(sql, proc.returncode, proc.stderr)
        rows = []
        for line in proc.stdout.splitlines():
            if line == "":
                continue
            rows.append(line.split("\t"))
        return rows

    def pg_scalar(self, sql: str, role: str = "admin") -> str | None:
        rows = self.pg_sql(sql, role)
        if not rows or not rows[0]:
            return None
        return rows[0][0]

    def pg_ok(self, sql: str, role: str) -> bool:
        """True si la requête réussit, False si PG refuse (permission denied, etc.)."""
        try:
            self.pg_sql(sql, role)
            return True
        except PgError:
            return False

    # ----------------------------------------------------------------- http
    @dataclass
    class Resp:
        status: int
        headers: dict
        body: bytes

        def json(self):
            return json.loads(self.body.decode("utf-8"))

        def text(self) -> str:
            return self.body.decode("utf-8", errors="replace")

    def http(self, method: str, path: str, body=None, headers: dict | None = None,
             base: str | None = None, timeout: int = 30) -> "Target.Resp":
        url = (base or self.get("dsp.base.url", "http://localhost:1880")).rstrip("/") + path
        data = None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Target.Resp(r.status, dict(r.headers), r.read())
        except urllib.error.HTTPError as e:
            return Target.Resp(e.code, dict(e.headers or {}), e.read())

    # ------------------------------------------------------------- HMAC DSP
    def dsp_sign(self, asset_id: str, from_ts: str, to_ts: str, path: str | None = None,
                 method: str = "POST") -> dict[str, str]:
        """Signature HMAC-SHA256 du data plane (forme canonique de référence du TCK) :
        sha256=HMAC(secret, "METHOD\\nPATH\\nASSET\\nFROM\\nTO\\nTS") envoyée en headers."""
        secret = self.require("dsp.hmac.secret")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = path or self.get("dsp.dataplane.path", "/dsp/data")
        canonical = f"{method}\n{path}\n{asset_id}\n{from_ts}\n{to_ts}\n{ts}"
        sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        return {
            "X-DSP-Agreement": self.get("dsp.agreement.id", "agreement:tck:001"),
            "X-DSP-Timestamp": ts,
            "X-DSP-Signature": f"sha256={sig}",
        }

    # ----------------------------------------------------------------- sftp
    def sftp_batch(self, commands: list[str]) -> subprocess.CompletedProcess:
        """Exécute un batch sftp (auth par clé). commands: lignes 'put local remote', 'ls dir'…"""
        host = self.require("sftp.host")
        port = self.get("sftp.port", "22")
        user = self.require("sftp.user")
        key = self.require("sftp.key")
        batch = "\n".join(commands) + "\n"
        return subprocess.run(
            ["sftp", "-b", "-", "-i", key, "-P", port,
             "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
             f"{user}@{host}"],
            input=batch, capture_output=True, text=True, timeout=120,
        )

    def sftp_put(self, local: str | Path, remote: str) -> None:
        proc = self.sftp_batch([f"put {local} {remote}"])
        if proc.returncode != 0:
            raise RuntimeError(f"dépôt SFTP échoué: {proc.stderr.strip()[:400]}")

    def sftp_ls(self, remote_dir: str) -> str:
        proc = self.sftp_batch([f"ls -l {remote_dir}"])
        return proc.stdout + proc.stderr

    # -------------------------------------------------------------- helpers
    def fixture(self, name: str, **repl) -> str:
        """Charge un fixture et substitue les placeholders __CLÉ__."""
        text = (FIXTURES_DIR / name).read_text(encoding="utf-8")
        for k, v in repl.items():
            text = text.replace(f"__{k.upper()}__", str(v))
        return text

    def expected_datasets(self) -> list[dict]:
        return json.loads((EXPECTED_DIR / "datasets.json").read_text(encoding="utf-8"))


def wait_until(fn, timeout_s: int, interval_s: float, desc: str):
    """Polling : lève TimeoutError si `fn()` ne devient pas truthy dans le délai."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval_s)
    raise TimeoutError(f"délai dépassé ({timeout_s}s) en attendant: {desc} (dernier état: {last!r})")
