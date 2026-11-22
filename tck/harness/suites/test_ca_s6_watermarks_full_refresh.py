"""CA-S6 — Silver watermark advances correctly; --full-refresh replays all
silver from bronze. Spec §10 CA-S6, §C1.5 (pg_cron, same watermarks, same 10 min).

Two modes (property silver.refresh.mode):
  - pg_cron : the refresh is handled by pg_cron in Postgres; the TCK polls the watermark ;
  - cli     : the TCK runs `silver.materializer.cmd` (useful in CI to avoid
              waiting 10 min).
"""

from __future__ import annotations

import json
import subprocess

from suites.base import TckTestCase


class CaS6WatermarksFullRefresh(TckTestCase):
    """Silver watermark + full-refresh replayable from bronze."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bronze = cls.t.get("bronze.table.meter", "bronze.energy_meter")
        cls.silver = cls.t.get("silver.view.meter", "silver.energy_meter")
        cls.wm_table = cls.t.get("watermarks.table", "silver._watermarks")
        cls.wm_key = cls.t.get("watermarks.key", "silver.energy_meter")
        cls.mode = cls.t.get("silver.refresh.mode", "pg_cron")
        cls.cmd = cls.t.get("silver.materializer.cmd", "")
        cls.timeout = cls.t.get_int("silver.timeout.s", 600)
        cls.marker_col = cls.t.get("silver.marker.column", "meter_id")

    # ------------------------------------------------------------- helpers
    def _watermark(self):
        rows = self.sql(
            f"SELECT watermark FROM {self.wm_table} WHERE key = {self.quote(self.wm_key)}")
        return rows[0][0] if rows else None

    def _refresh(self, full: bool = False):
        if self.mode == "cli":
            self.assertTrue(self.cmd, "silver.refresh.mode=cli requires silver.materializer.cmd")
            args = self.cmd.split() + (["--full-refresh"] if full else [])
            proc = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout + 120)
            self.assertEqual(proc.returncode, 0,
                             f"materializer failed: {proc.stderr[:400]}")
        # mode pg_cron: nothing to trigger, the cron runs inside Postgres

    def _inject_bronze_direct(self, meter: str, power_kw: float) -> str:
        """Inserts directly into bronze (simulates a line that went through the gate) with a
        minimal JSON payload; returns the timestamp used."""
        ts = self.now_iso()
        payload = json.dumps({
            "type": "smart_energy.meter", "schema_version": "1.0", "timestamp": ts,
            "site_id": self.site, "meter_id": meter, "asset_id": meter,
            "active_power_kw": power_kw,
        }).replace("'", "''")
        self.sql(
            f"INSERT INTO {self.bronze} (raw_payload, source_topic, source_system, ingest_timestamp) "
            f"VALUES ('{payload}', 'sim.smart_energy.meter', 'tck', now())")
        return ts

    # --------------------------------------------------------------- tests
    def test_01_watermark_advances_after_new_line(self):
        """After ingesting a new bronze line + refresh, the silver watermark
        advances (≥ timestamp of the line)."""
        before = self._watermark()
        marker = f"{self.marker}-wm"
        ts = self._inject_bronze_direct(marker, 5.0)
        self._refresh(full=False)
        self.wait_for(
            lambda: (self._watermark() or "") >= ts,
            timeout_s=self.timeout, desc=f"watermark >= {ts} (before: {before})")
        after = self._watermark()
        self.assertGreaterEqual(after, ts, f"watermark not advanced: before={before}, after={after}")

    def test_02_full_refresh_replays_silver_from_bronze(self):
        """--full-refresh: all replayed bronze lines are present in silver
        (watermark = max bronze ts), and the quality filters apply."""
        ok_marker = f"{self.marker}-ok"
        bad_marker = f"{self.marker}-bad"
        ts_ok = self._inject_bronze_direct(ok_marker, 8.0)
        # semantically invalid line (negative power): passes the gate (structure OK)
        # but must be filtered by the silver quality layer (spec synthesis §1: power >= 0)
        self._inject_bronze_direct(bad_marker, -1.0)

        self._refresh(full=True)
        self.wait_for(
            lambda: self.sql(f"SELECT 1 FROM {self.silver} "
                             f"WHERE {self.marker_col} = {self.quote(ok_marker)}"),
            timeout_s=self.timeout, desc=f"full-refresh: {ok_marker} present in silver")

        # the invalid line stays in bronze (fidelity) but is absent from silver (validity)
        in_bronze = self.scalar(f"SELECT count(*) FROM {self.bronze} "
                                f"WHERE raw_payload LIKE '%{bad_marker}%'")
        in_silver = self.scalar(f"SELECT count(*) FROM {self.silver} "
                                f"WHERE {self.marker_col} = {self.quote(bad_marker)}")
        self.assertEqual(int(in_bronze or 0), 1, "the invalid line must stay in bronze")
        self.assertEqual(int(in_silver or 0), 0,
                         "the silver quality filter (power >= 0) must exclude the invalid line")

        # watermark = max of ingested bronze ts
        wm = self._watermark()
        self.assertIsNotNone(wm, "silver watermark required after full-refresh")
        self.assertGreaterEqual(wm, ts_ok)
