"""CA-S4 — Data plane DSP : réponse {assetId, schema, table, columns, rows, rowCount}
dans la fenêtre from/to, LIMIT 5000 ; sans contrat valide -> 401/403 en JSON-LD dspace:Error.
Spec §10 CA-S4, §C1.7 (mêmes contrat et format de réponse que la FAP-full, sur Postgres).
"""

from __future__ import annotations

import datetime as dt

from suites.base import TckTestCase


class CaS4DataPlane(TckTestCase):
    """Contrat de réponse du data plane + fenêtre from/to + LIMIT 5000 + erreurs dspace:Error."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.t.get("dsp.dataplane.path", "/dsp/data")
        cls.asset = cls.t.get("ca.s4.asset.id", "dataset:facis:net-grid-hourly")
        cls.limit = cls.t.get_int("data.limit", 5000)
        cls.time_column = "hour"
        for d in cls.t.expected_datasets():
            if d["id"] == cls.asset:
                cls.time_column = d["metadata"].get("timeColumn", "hour")
                cls.schema = d["metadata"]["schema"]
                cls.table = d["metadata"]["table"]

    def _window(self) -> tuple[str, str]:
        to = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        frm = to - dt.timedelta(days=30)
        return frm.isoformat().replace("+00:00", "Z"), to.isoformat().replace("+00:00", "Z")

    def _query(self, asset: str | None = None, frm: str | None = None, to: str | None = None,
               sign: bool = True, raw_headers: dict | None = None):
        asset = asset or self.asset
        frm, to = frm or self._window()[0], to or self._window()[1]
        body = {"assetId": asset, "from": frm, "to": to}
        headers = self.t.dsp_sign(asset, frm, to, path=self.path) if sign else {}
        if raw_headers:
            headers.update(raw_headers)
        return self.t.http("POST", self.path, body=body, headers=headers), body

    # ------------------------------------------------------- contrat de réponse
    def test_01_forme_de_reponse_iso_facis(self):
        resp, body = self._query()
        self.assertEqual(resp.status, 200, f"statut attendu 200: {resp.status} {resp.text()[:300]}")
        doc = resp.json()
        for key in ("assetId", "schema", "table", "columns", "rows", "rowCount"):
            self.assertIn(key, doc, f"clé manquante dans la réponse data plane: {key}")
        self.assertEqual(doc["assetId"], self.asset)
        self.assertEqual(doc["schema"], self.schema)
        self.assertEqual(doc["table"], self.table)
        self.assertIsInstance(doc["columns"], list)
        self.assertIsInstance(doc["rows"], list)
        self.assertEqual(doc["rowCount"], len(doc["rows"]),
                         "rowCount doit égaler len(rows) (JSON plat, spec §C1.7)")
        self.assertLessEqual(doc["rowCount"], self.limit)
        # lignes = tableaux plats (pas d'objets imbriqués)
        for row in doc["rows"][:5]:
            self.assertIsInstance(row, list, f"les lignes doivent être des tableaux JSON plats: {row}")

    def test_02_fenetre_from_to_respectee(self):
        frm, to = self._window()
        resp, _ = self._query(frm=frm, to=to)
        self.assertEqual(resp.status, 200)
        doc = resp.json()
        self.assertIn(self.time_column, doc["columns"], f"colonne temporelle {self.time_column} absente")
        idx = doc["columns"].index(self.time_column)
        for row in doc["rows"]:
            ts = row[idx]
            self.assertGreaterEqual(str(ts), frm, f"ligne hors fenêtre (avant from): {ts}")
            self.assertLessEqual(str(ts)[:19] + "Z" if "T" in str(ts) else str(ts), to,
                                 f"ligne hors fenêtre (après to): {ts}")

    def test_03_limite_5000_appliquee(self):
        """rowCount ≤ 5000 ; si la fenêtre contient plus de 5000 lignes, rowCount == 5000."""
        frm, to = self._window()
        resp, _ = self._query(frm=frm, to=to)
        doc = resp.json()
        total = self.scalar(
            f"SELECT count(*) FROM {self.schema}.{self.table} "
            f"WHERE {self.time_column} >= '{frm}'::timestamptz "
            f"AND {self.time_column} <= '{to}'::timestamptz")
        total = int(total or 0)
        if total > self.limit:
            self.assertEqual(doc["rowCount"], self.limit,
                             f"LIMIT {self.limit} non appliqué: {total} lignes dans la fenêtre")
        else:
            self.assertEqual(doc["rowCount"], total,
                             f"toutes les lignes de la fenêtre doivent être servies ({total})")

    # ----------------------------------------------------------- erreurs contrat
    def test_04_sans_contrat_401_ou_403_dspace_error(self):
        resp, _ = self._query(sign=False)
        self.assertIn(resp.status, (401, 403), f"sans contrat, attendu 401/403, reçu {resp.status}")
        self.assertIn("dspace:Error", resp.text(),
                      f"l'erreur doit être un JSON-LD dspace:Error: {resp.text()[:300]}")

    def test_05_signature_invalide_403_dspace_error(self):
        resp, _ = self._query(raw_headers={"X-DSP-Signature": "sha256=0" * 32})
        self.assertIn(resp.status, (401, 403))
        self.assertIn("dspace:Error", resp.text())

    def test_06_asset_inconnu_rejete(self):
        resp, _ = self._query(asset="dataset:facis:inexistant")
        self.assertIn(resp.status, (403, 404), f"asset non déclaré: attendu 403/404, reçu {resp.status}")
        self.assertIn("dspace:Error", resp.text())

    def test_07_fenetre_invalide_400_dspace_error(self):
        resp, _ = self._query(frm="pas-une-date", to="non-plus")
        self.assertEqual(resp.status, 400, f"from/to invalide: attendu 400, reçu {resp.status}")
        self.assertIn("dspace:Error", resp.text())
