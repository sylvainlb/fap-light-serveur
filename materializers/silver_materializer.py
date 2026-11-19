#!/usr/bin/env python3
"""Materializer silver du profil FAP-light (spec §C1.5, CA-S6).

En production, le refresh est assuré par pg_cron dans Postgres (toutes les
10 min, job 'fap-light-silver-watermark' -> SELECT public.silver_tick()).
Ce CLI sert :
  - en CI / test : exécution manuelle (mode cli du TCK, silver.refresh.mode=cli) ;
  - en exploitation : rattrapage manuel ou --full-refresh.

Usage :
  python3 silver_materializer.py                 # tick watermark (incrémental)
  python3 silver_materializer.py --full-refresh  # rejoue tout le silver depuis
                                                 # bronze (réapplique les vues
                                                 # silver + watermark)

Connexion via psql (variables PG* usuelles : PGHOST, PGPORT, PGUSER,
PGPASSWORD, PGDATABASE) — aucune dépendance Python externe.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_DDL = Path(__file__).resolve().parents[1] / "deploy/postgres/init/03_silver_views.sql"


def psql(sql: str | None = None, file: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-X", "-q"]
    if file:
        cmd += ["-f", file]
    if sql:
        cmd += ["-c", sql]
    env = dict(os.environ)
    env.setdefault("PGHOST", "localhost")
    env.setdefault("PGPORT", "5432")
    env.setdefault("PGDATABASE", "fap")
    env.setdefault("PGUSER", "admin")
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)


def main() -> int:
    ap = argparse.ArgumentParser(description="Materializer silver FAP-light")
    ap.add_argument("--full-refresh", action="store_true",
                    help="rejoue tout le silver depuis bronze (réapplique les vues + watermark)")
    ap.add_argument("--ddl", default=str(DEFAULT_DDL),
                    help="fichier DDL des vues silver (utilisé avec --full-refresh)")
    args = ap.parse_args()

    if args.full_refresh:
        if not Path(args.ddl).exists():
            print(f"ERREUR: DDL introuvable: {args.ddl}", file=sys.stderr)
            return 2
        r = psql(file=args.ddl)
        if r.returncode != 0:
            print(f"ERREUR full-refresh (DDL): {r.stderr[:500]}", file=sys.stderr)
            return 1
        print("[full-refresh] vues silver réappliquées depuis bronze")

    r = psql(sql="SELECT public.silver_tick()")
    if r.returncode != 0:
        print(f"ERREUR silver_tick: {r.stderr[:500]}", file=sys.stderr)
        return 1
    print("[tick] watermark silver avancé (silver._watermarks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
