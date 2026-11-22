#!/usr/bin/env python3
"""Silver materializer of the FAP-light profile (spec §C1.5, CA-S6).

In production, the refresh is handled by pg_cron in Postgres (every
10 min, job 'fap-light-silver-watermark' -> SELECT public.silver_tick()).
This CLI is used:
  - in CI / tests: manual execution (TCK cli mode, silver.refresh.mode=cli) ;
  - in operations: manual catch-up or --full-refresh.

Usage:
  python3 silver_materializer.py                 # watermark tick (incremental)
  python3 silver_materializer.py --full-refresh  # replays all silver from
                                                 # bronze (reapplies the
                                                 # silver views + watermark)

Connection via psql (usual PG* variables: PGHOST, PGPORT, PGUSER,
PGPASSWORD, PGDATABASE) — no external Python dependency.
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
    ap = argparse.ArgumentParser(description="FAP-light silver materializer")
    ap.add_argument("--full-refresh", action="store_true",
                    help="replays all silver from bronze (reapplies the views + watermark)")
    ap.add_argument("--ddl", default=str(DEFAULT_DDL),
                    help="DDL file of the silver views (used with --full-refresh)")
    args = ap.parse_args()

    if args.full_refresh:
        if not Path(args.ddl).exists():
            print(f"ERROR: DDL not found: {args.ddl}", file=sys.stderr)
            return 2
        r = psql(file=args.ddl)
        if r.returncode != 0:
            print(f"ERROR full-refresh (DDL): {r.stderr[:500]}", file=sys.stderr)
            return 1
        print("[full-refresh] silver views reapplied from bronze")

    r = psql(sql="SELECT public.silver_tick()")
    if r.returncode != 0:
        print(f"ERROR silver_tick: {r.stderr[:500]}", file=sys.stderr)
        return 1
    print("[tick] silver watermark advanced (silver._watermarks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
