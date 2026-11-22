"""Common base of the TCK suites: config loading and assertion helpers."""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from harness.target import Target, load_properties, wait_until, PgError


class TckTestCase(unittest.TestCase):
    """Access to the target via self.t ; unique marker per run for idempotence."""

    t: Target

    @classmethod
    def setUpClass(cls):
        props_path = os.environ.get("TCK_PROPERTIES",
                                    str(Path(__file__).resolve().parents[2] / "tck.properties"))
        cls.t = Target(load_properties(props_path))

    # ------------------------------------------------------------- helpers
    @property
    def marker(self) -> str:
        return self.t.marker

    @property
    def site(self) -> str:
        return f"tck-site-{self.t.run_id}".lower()

    @staticmethod
    def now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def ts(offset_s: int = 0) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_s))

    def inject_sim(self, fixture: str = "sim_meter_payload.json", **extra) -> dict:
        """Posts a simulation feed payload to the ORCE ingestion endpoint."""
        repl = {"MARKER": self.marker, "SITE": self.site, "TS": self.now_iso(), **extra}
        payload = json.loads(self.t.fixture(fixture, **repl))
        resp = self.t.http("POST", self.t.get("dsp.ingest.sim.path", "/ingest/sim"), body=payload)
        return {"status": resp.status, "body": resp.text(), "payload": payload}

    def sql(self, query: str, role: str = "admin"):
        return self.t.pg_sql(query, role)

    def scalar(self, query: str, role: str = "admin"):
        return self.t.pg_scalar(query, role)

    def quote(self, s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    def wait_for(self, fn, timeout_s: int, desc: str, interval_s: float = 5.0):
        return wait_until(fn, timeout_s, interval_s, desc)
