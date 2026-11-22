"""CA-S3 — DSP catalog: lists exactly the declared gold assets, with
policySummary; an undeclared asset does not appear. Gold-only filter kept per ISO,
generic derivation (declared schemas publishable). Spec §10 CA-S3, §C1.6.

Expected response (native FACIS contract, identical to the full FAP):
  POST {dsp.catalogue.path} -> {"datasets": [ {id, metadata, offers}, ... ], ...}
"""

from __future__ import annotations

import json

from suites.base import TckTestCase


class CaS3CatalogueDsp(TckTestCase):
    """The DSP catalog exposes exactly the declared gold assets (postgres format)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.t.get("dsp.catalogue.path", "/dsp/catalogue/request")
        cls.expected = cls.t.expected_datasets()

    def _catalogue(self) -> list[dict]:
        resp = self.t.http("POST", self.path, body={"@type": "CatalogRequestMessage"})
        self.assertEqual(resp.status, 200,
                         f"catalog: expected status 200, got {resp.status}: {resp.text()[:300]}")
        doc = resp.json()
        datasets = doc.get("datasets")
        self.assertIsInstance(datasets, list,
                              f"catalog: the response must contain a 'datasets' list: {doc}")
        return datasets

    def test_01_exact_set_of_declared_assets(self):
        """The set of catalog ids == the set of ids of expected/datasets.json."""
        got = {d.get("id") for d in self._catalogue()}
        want = {d["id"] for d in self.expected}
        self.assertEqual(got, want,
                         f"catalog mismatch: missing={sorted(want - got)}, "
                         f"extra={sorted(got - want)}")

    def test_02_metadata_conformant_postgres_format(self):
        """Each asset: format 'postgres', schema 'gold', assetType, timeColumn, policySummary."""
        for d in self._catalogue():
            with self.subTest(asset=d.get("id")):
                md = d.get("metadata", {})
                self.assertEqual(md.get("format"), "postgres",
                                 "format must be 'postgres' in the light profile (spec §C1.6)")
                self.assertEqual(md.get("schema"), "gold",
                                 "gold-only filter per ISO (spec §C1.6)")
                self.assertTrue(md.get("assetType"), "assetType required")
                self.assertTrue(md.get("table"), "table required")
                self.assertTrue(md.get("timeColumn"), "timeColumn required")
                offers = d.get("offers", [])
                self.assertTrue(offers, "at least one offer required")
                ps = offers[0].get("policySummary", {})
                self.assertEqual(ps.get("rate_limit"), "10 req/min", "policySummary.rate_limit")
                self.assertEqual(ps.get("ttl"), "PT1H", "policySummary.ttl")
                self.assertTrue(ps.get("purpose"), "policySummary.purpose required")

    def test_03_undeclared_gold_not_exposed(self):
        """A gold matview created but not declared in datasets.json must not
        appear in the catalog (derivation filtered on declared schemas/tables)."""
        probe = f"gold.tck_undeclared_{self.t.run_id}".lower()
        self.sql(f"CREATE MATERIALIZED VIEW IF NOT EXISTS {probe} AS "
                 f"SELECT 1 AS x, now() AS hour")
        try:
            ids = {d.get("id") for d in self._catalogue()}
            table_names = {d.get("metadata", {}).get("table") for d in self._catalogue()}
            self.assertNotIn(probe.split(".")[1], table_names,
                             f"the undeclared matview {probe} must not be published")
            self.assertFalse(any("tck_undeclared" in (i or "") for i in ids),
                             "no tck_undeclared asset must exist in the catalog")
        finally:
            self.sql(f"DROP MATERIALIZED VIEW IF EXISTS {probe}")

    def test_04_bronze_and_silver_not_exposed(self):
        """The gold-only filter excludes bronze and silver from the catalog (ISO FACIS)."""
        tables = {d.get("metadata", {}).get("table") for d in self._catalogue()}
        schemas = {d.get("metadata", {}).get("schema") for d in self._catalogue()}
        self.assertEqual(schemas, {"gold"}, f"only the gold schema is published: {schemas}")
        for forbidden in ("energy_meter",):
            self.assertNotIn(forbidden, tables)
