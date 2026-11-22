"""CA-S1 — Simulation ingestion (sim.* topic): bronze -> silver (<= 10 min) -> gold,
with full lineage. Spec §10 CA-S1, §C1.3 (simulation flow -> gate -> INSERT bronze).
"""

from __future__ import annotations

from suites.base import TckTestCase


class CaS1IngestionSimulation(TckTestCase):
    """A record published by the simulation appears in bronze then silver (≤ 10 min)
    then gold, with full lineage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bronze = cls.t.get("bronze.table.meter", "bronze.energy_meter")
        cls.silver = cls.t.get("silver.view.meter", "silver.energy_meter")
        cls.gold = cls.t.get("gold.table.hourly", "gold.net_grid_hourly")
        cls.lineage_cols = cls.t.get("bronze.lineage.columns",
                                     "source_topic,ingest_timestamp,source_system").split(",")
        cls.silver_timeout = cls.t.get_int("silver.timeout.s", 600)
        cls.gold_timeout = cls.t.get_int("gold.timeout.s", 1200)
        # Injects a meter feed payload with a unique marker, once for the whole suite.
        cls.injected = None

    def setUp(self):
        if CaS1IngestionSimulation.injected is None:
            CaS1IngestionSimulation.injected = self.inject_sim()
        self.assertIn(CaS1IngestionSimulation.injected["status"], (200, 201, 202),
                      f"the simulation ingestion endpoint must accept the payload: "
                      f"{CaS1IngestionSimulation.injected}")

    # ------------------------------------------------------------- bronze
    def test_01_bronze_holds_raw_with_lineage(self):
        """The bronze line holds the raw payload (fidelity) + the lineage columns."""
        rows = self.sql(
            f"SELECT raw_payload FROM {self.bronze} "
            f"WHERE raw_payload LIKE '%{self.marker}%' ORDER BY ingest_timestamp DESC LIMIT 1")
        self.assertTrue(rows, f"no bronze line found for marker {self.marker}")
        raw = rows[0][0]
        self.assertIn(self.marker, raw)
        self.assertIn("smart_energy.meter", raw)
        # full lineage: each lineage column exists and is set
        cols = ", ".join(f"{c} IS NOT NULL AS {c}_ok" for c in self.lineage_cols)
        rows = self.sql(
            f"SELECT {cols} FROM {self.bronze} WHERE raw_payload LIKE '%{self.marker}%' LIMIT 1")
        self.assertTrue(rows, "bronze line not found for the lineage check")
        for i, c in enumerate(self.lineage_cols):
            self.assertEqual(rows[0][i], "t", f"lineage column not set: {c}")
        # the declared source is indeed the sim.* topic family
        src = self.scalar(
            f"SELECT source_topic FROM {self.bronze} "
            f"WHERE raw_payload LIKE '%{self.marker}%' LIMIT 1")
        self.assertIsNotNone(src)
        self.assertTrue(src.startswith("sim."), f"unexpected source_topic: {src} (expected sim.*)")

    # ------------------------------------------------------------- silver
    def test_02_silver_within_10_min_window(self):
        """The silver (typed, filtered) holds the line within ≤ silver.timeout.s (default 600 s)."""
        marker_col = self.t.get("silver.marker.column", "meter_id")
        found = self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.silver} WHERE {marker_col} = {self.quote(self.marker)}"),
            timeout_s=self.silver_timeout, desc=f"silver.{marker_col}={self.marker}")
        self.assertTrue(found)

    def test_03_silver_is_typed_via_cast(self):
        """The silver is typed: the CAST (typing act, spec §C1.2) works on the fields."""
        marker_col = self.t.get("silver.marker.column", "meter_id")
        # If the view exposes active_power_kw, the numeric CAST must succeed and equal the injected value.
        val = self.scalar(
            f"SELECT active_power_kw::float8 FROM {self.silver} "
            f"WHERE {marker_col} = {self.quote(self.marker)} LIMIT 1")
        self.assertIsNotNone(val, "silver line missing for the typing test")
        self.assertAlmostEqual(float(val), 12.45, places=6,
                               msg="the silver CAST must return the injected value (12.45)")

    # --------------------------------------------------------------- gold
    def test_04_gold_within_refresh_window(self):
        """The record reaches gold (matview) within ≤ gold.timeout.s (default 1200 s)."""
        marker_col = self.t.get("gold.marker.column", "site_id")
        found = self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.gold} WHERE {marker_col} = {self.quote(self.site)}"),
            timeout_s=self.gold_timeout, desc=f"gold.{marker_col}={self.site}")
        self.assertTrue(found)

    def test_05_full_chain_bronze_silver_gold(self):
        """End-to-end lineage: bronze AND silver AND gold all contain the marker."""
        marker_col_s = self.t.get("silver.marker.column", "meter_id")
        marker_col_g = self.t.get("gold.marker.column", "site_id")
        b = self.scalar(f"SELECT count(*) FROM {self.bronze} "
                        f"WHERE raw_payload LIKE '%{self.marker}%'")
        s = self.scalar(f"SELECT count(*) FROM {self.silver} "
                        f"WHERE {marker_col_s} = {self.quote(self.marker)}")
        g = self.scalar(f"SELECT count(*) FROM {self.gold} "
                        f"WHERE {marker_col_g} = {self.quote(self.site)}")
        self.assertGreater(int(b or 0), 0, "bronze: marker missing")
        self.assertGreater(int(s or 0), 0, "silver: marker missing")
        self.assertGreater(int(g or 0), 0, "gold: marker missing")
