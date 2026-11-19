"""CA-S1 — Ingestion simulation (topic sim.*) : bronze -> silver (<= 10 min) -> gold,
avec lineage complète. Spec §10 CA-S1, §C1.3 (flow simulation -> gate -> INSERT bronze).
"""

from __future__ import annotations

from suites.base import TckTestCase


class CaS1IngestionSimulation(TckTestCase):
    """Une donnée publiée par la simulation apparaît en bronze puis silver (≤ 10 min)
    puis gold, avec lineage complète."""

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
        # Injection d'un payload de feed meter avec marqueur unique, une fois pour toute la suite.
        cls.injected = None

    def setUp(self):
        if CaS1IngestionSimulation.injected is None:
            CaS1IngestionSimulation.injected = self.inject_sim()
        self.assertIn(CaS1IngestionSimulation.injected["status"], (200, 201, 202),
                      f"l'endpoint d'ingestion simulation doit accepter le payload: "
                      f"{CaS1IngestionSimulation.injected}")

    # ------------------------------------------------------------- bronze
    def test_01_bronze_contient_le_brut_avec_lineage(self):
        """La ligne bronze contient le payload brut (fidélité) + les colonnes de lineage."""
        rows = self.sql(
            f"SELECT raw_payload FROM {self.bronze} "
            f"WHERE raw_payload LIKE '%{self.marker}%' ORDER BY ingest_timestamp DESC LIMIT 1")
        self.assertTrue(rows, f"aucune ligne bronze trouvée pour le marqueur {self.marker}")
        raw = rows[0][0]
        self.assertIn(self.marker, raw)
        self.assertIn("smart_energy.meter", raw)
        # lineage complète : chaque colonne de lineage existe et est renseignée
        cols = ", ".join(f"{c} IS NOT NULL AS {c}_ok" for c in self.lineage_cols)
        rows = self.sql(
            f"SELECT {cols} FROM {self.bronze} WHERE raw_payload LIKE '%{self.marker}%' LIMIT 1")
        self.assertTrue(rows, "ligne bronze introuvable pour le contrôle de lineage")
        for i, c in enumerate(self.lineage_cols):
            self.assertEqual(rows[0][i], "t", f"colonne de lineage non renseignée: {c}")
        # la source déclarée est bien la famille de topics sim.*
        src = self.scalar(
            f"SELECT source_topic FROM {self.bronze} "
            f"WHERE raw_payload LIKE '%{self.marker}%' LIMIT 1")
        self.assertIsNotNone(src)
        self.assertTrue(src.startswith("sim."), f"source_topic inattendu: {src} (attendu sim.*)")

    # ------------------------------------------------------------- silver
    def test_02_silver_dans_la_fenetre_10_min(self):
        """Le silver (typé, filtré) contient la ligne en ≤ silver.timeout.s (défaut 600 s)."""
        marker_col = self.t.get("silver.marker.column", "meter_id")
        found = self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.silver} WHERE {marker_col} = {self.quote(self.marker)}"),
            timeout_s=self.silver_timeout, desc=f"silver.{marker_col}={self.marker}")
        self.assertTrue(found)

    def test_03_silver_est_type_via_cast(self):
        """Le silver est typé : le CAST (acte de typage, spec §C1.2) fonctionne sur les champs."""
        marker_col = self.t.get("silver.marker.column", "meter_id")
        # Si la vue expose active_power_kw, le CAST en numérique doit réussir et valoir la valeur injectée.
        val = self.scalar(
            f"SELECT active_power_kw::float8 FROM {self.silver} "
            f"WHERE {marker_col} = {self.quote(self.marker)} LIMIT 1")
        self.assertIsNotNone(val, "ligne silver absente pour le test de typage")
        self.assertAlmostEqual(float(val), 12.45, places=6,
                               msg="le CAST silver doit restituer la valeur injectée (12.45)")

    # --------------------------------------------------------------- gold
    def test_04_gold_dans_la_fenetre_refresh(self):
        """La donnée atteint le gold (matview) en ≤ gold.timeout.s (défaut 1200 s)."""
        marker_col = self.t.get("gold.marker.column", "site_id")
        found = self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.gold} WHERE {marker_col} = {self.quote(self.site)}"),
            timeout_s=self.gold_timeout, desc=f"gold.{marker_col}={self.site}")
        self.assertTrue(found)

    def test_05_chaine_complete_bronze_silver_gold(self):
        """Lineage de bout en bout : bronze ET silver ET gold contiennent le marqueur."""
        marker_col_s = self.t.get("silver.marker.column", "meter_id")
        marker_col_g = self.t.get("gold.marker.column", "site_id")
        b = self.scalar(f"SELECT count(*) FROM {self.bronze} "
                        f"WHERE raw_payload LIKE '%{self.marker}%'")
        s = self.scalar(f"SELECT count(*) FROM {self.silver} "
                        f"WHERE {marker_col_s} = {self.quote(self.marker)}")
        g = self.scalar(f"SELECT count(*) FROM {self.gold} "
                        f"WHERE {marker_col_g} = {self.quote(self.site)}")
        self.assertGreater(int(b or 0), 0, "bronze: marqueur absent")
        self.assertGreater(int(s or 0), 0, "silver: marqueur absent")
        self.assertGreater(int(g or 0), 0, "gold: marqueur absent")
