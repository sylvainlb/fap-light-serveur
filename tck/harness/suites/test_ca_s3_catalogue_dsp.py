"""CA-S3 — Catalogue DSP : liste exactement les assets gold déclarés, avec
policySummary ; un asset non déclaré n'apparaît pas. Filtre gold-only conservé à l'ISO,
dérivation générique (schémas déclarés publiables). Spec §10 CA-S3, §C1.6.

Réponse attendue (contrat FACIS natif, identique à la FAP-full) :
  POST {dsp.catalogue.path} -> {"datasets": [ {id, metadata, offers}, ... ], ...}
"""

from __future__ import annotations

import json

from suites.base import TckTestCase


class CaS3CatalogueDsp(TckTestCase):
    """Le catalogue DSP expose exactement les assets gold déclarés (format postgres)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.t.get("dsp.catalogue.path", "/dsp/catalogue/request")
        cls.expected = cls.t.expected_datasets()

    def _catalogue(self) -> list[dict]:
        resp = self.t.http("POST", self.path, body={"@type": "CatalogRequestMessage"})
        self.assertEqual(resp.status, 200,
                         f"catalogue: statut attendu 200, reçu {resp.status}: {resp.text()[:300]}")
        doc = resp.json()
        datasets = doc.get("datasets")
        self.assertIsInstance(datasets, list,
                              f"catalogue: la réponse doit contenir une liste 'datasets': {doc}")
        return datasets

    def test_01_ensemble_exact_des_assets_declares(self):
        """L'ensemble des ids du catalogue == ensemble des ids de expected/datasets.json."""
        got = {d.get("id") for d in self._catalogue()}
        want = {d["id"] for d in self.expected}
        self.assertEqual(got, want,
                         f"écart catalogue: manquants={sorted(want - got)}, "
                         f"en trop={sorted(got - want)}")

    def test_02_metadonnees_conformes_format_postgres(self):
        """Chaque asset : format 'postgres', schema 'gold', assetType, timeColumn, policySummary."""
        for d in self._catalogue():
            with self.subTest(asset=d.get("id")):
                md = d.get("metadata", {})
                self.assertEqual(md.get("format"), "postgres",
                                 "format doit être 'postgres' en profil light (spec §C1.6)")
                self.assertEqual(md.get("schema"), "gold",
                                 "filtre gold-only à l'ISO (spec §C1.6)")
                self.assertTrue(md.get("assetType"), "assetType requis")
                self.assertTrue(md.get("table"), "table requis")
                self.assertTrue(md.get("timeColumn"), "timeColumn requis")
                offers = d.get("offers", [])
                self.assertTrue(offers, "au moins une offre requise")
                ps = offers[0].get("policySummary", {})
                self.assertEqual(ps.get("rate_limit"), "10 req/min", "policySummary.rate_limit")
                self.assertEqual(ps.get("ttl"), "PT1H", "policySummary.ttl")
                self.assertTrue(ps.get("purpose"), "policySummary.purpose requis")

    def test_03_gold_non_declare_n_apparait_pas(self):
        """Une matview gold créée mais non déclarée dans datasets.json ne doit pas
        apparaître dans le catalogue (dérivation filtrée sur les schémas/table déclarés)."""
        probe = f"gold.tck_undeclared_{self.t.run_id}".lower()
        self.sql(f"CREATE MATERIALIZED VIEW IF NOT EXISTS {probe} AS "
                 f"SELECT 1 AS x, now() AS hour")
        try:
            ids = {d.get("id") for d in self._catalogue()}
            table_names = {d.get("metadata", {}).get("table") for d in self._catalogue()}
            self.assertNotIn(probe.split(".")[1], table_names,
                             f"la matview non déclarée {probe} ne doit pas être publiée")
            self.assertFalse(any("tck_undeclared" in (i or "") for i in ids),
                             "aucun asset tck_undeclared ne doit exister dans le catalogue")
        finally:
            self.sql(f"DROP MATERIALIZED VIEW IF EXISTS {probe}")

    def test_04_bronze_et_silver_ne_sont_pas_exposes(self):
        """Le filtre gold-only exclut bronze et silver du catalogue (ISO FACIS)."""
        tables = {d.get("metadata", {}).get("table") for d in self._catalogue()}
        schemas = {d.get("metadata", {}).get("schema") for d in self._catalogue()}
        self.assertEqual(schemas, {"gold"}, f"seul le schéma gold est publié: {schemas}")
        for forbidden in ("energy_meter",):
            self.assertNotIn(forbidden, tables)
