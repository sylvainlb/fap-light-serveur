"""CA-S6 — Watermark silver avance correctement ; --full-refresh rejoue tout le
silver depuis bronze. Spec §10 CA-S6, §C1.5 (pg_cron, mêmes watermarks, mêmes 10 min).

Deux modes (propriété silver.refresh.mode) :
  - pg_cron : le refresh est assuré par pg_cron dans Postgres ; le TCK poll le watermark ;
  - cli     : le TCK exécute `silver.materializer.cmd` (utile en CI pour ne pas
              attendre 10 min).
"""

from __future__ import annotations

import json
import subprocess

from suites.base import TckTestCase


class CaS6WatermarksFullRefresh(TckTestCase):
    """Watermark silver + full-refresh rejouable depuis bronze."""

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
            self.assertTrue(self.cmd, "silver.refresh.mode=cli exige silver.materializer.cmd")
            args = self.cmd.split() + (["--full-refresh"] if full else [])
            proc = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout + 120)
            self.assertEqual(proc.returncode, 0,
                             f"materializer a échoué: {proc.stderr[:400]}")
        # mode pg_cron : rien à déclencher, le cron tourne dans Postgres

    def _inject_bronze_direct(self, meter: str, power_kw: float) -> str:
        """Insère directement en bronze (simule une ligne passée par la gate) avec un
        payload JSON minimal ; retourne le timestamp utilisé."""
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
    def test_01_watermark_avance_apres_nouvelle_ligne(self):
        """Après ingestion d'une nouvelle ligne bronze + refresh, le watermark silver
        avance (≥ timestamp de la ligne)."""
        before = self._watermark()
        marker = f"{self.marker}-wm"
        ts = self._inject_bronze_direct(marker, 5.0)
        self._refresh(full=False)
        self.wait_for(
            lambda: (self._watermark() or "") >= ts,
            timeout_s=self.timeout, desc=f"watermark >= {ts} (avant: {before})")
        after = self._watermark()
        self.assertGreaterEqual(after, ts, f"watermark non avancé: avant={before}, après={after}")

    def test_02_full_refresh_rejoue_silver_depuis_bronze(self):
        """--full-refresh : toutes les lignes bronze rejouées sont présentes en silver
        (watermark = max ts bronze), et les filtres qualité s'appliquent."""
        ok_marker = f"{self.marker}-ok"
        bad_marker = f"{self.marker}-bad"
        ts_ok = self._inject_bronze_direct(ok_marker, 8.0)
        # ligne sémantiquement invalide (puissance négative) : passe la gate (structure OK)
        # mais doit être filtrée par la qualité silver (spec synthèse §1 : puissance >= 0)
        self._inject_bronze_direct(bad_marker, -1.0)

        self._refresh(full=True)
        self.wait_for(
            lambda: self.sql(f"SELECT 1 FROM {self.silver} "
                             f"WHERE {self.marker_col} = {self.quote(ok_marker)}"),
            timeout_s=self.timeout, desc=f"full-refresh: {ok_marker} présent en silver")

        # la ligne invalide reste en bronze (fidélité) mais est absente du silver (validité)
        in_bronze = self.scalar(f"SELECT count(*) FROM {self.bronze} "
                                f"WHERE raw_payload LIKE '%{bad_marker}%'")
        in_silver = self.scalar(f"SELECT count(*) FROM {self.silver} "
                                f"WHERE {self.marker_col} = {self.quote(bad_marker)}")
        self.assertEqual(int(in_bronze or 0), 1, "la ligne invalide doit rester en bronze")
        self.assertEqual(int(in_silver or 0), 0,
                         "le filtre qualité silver (puissance >= 0) doit exclure la ligne invalide")

        # watermark = max des ts bronze ingérés
        wm = self._watermark()
        self.assertIsNotNone(wm, "watermark silver requis après full-refresh")
        self.assertGreaterEqual(wm, ts_ok)
