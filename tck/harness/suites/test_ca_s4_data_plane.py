"""CA-S4 — DSP data plane: response {assetId, schema, table, columns, rows, rowCount}
within the from/to window, LIMIT 5000; without a valid contract -> 401/403 JSON-LD dspace:Error.
Spec §10 CA-S4, §C1.7 (same contract and response format as the full FAP, on Postgres).
"""

from __future__ import annotations

import datetime as dt

from suites.base import TckTestCase


class CaS4DataPlane(TckTestCase):
    """Data plane response contract + from/to window + LIMIT 5000 + dspace:Error errors."""

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

    # ------------------------------------------------------- response contract
    def test_01_iso_facis_response_shape(self):
        resp, body = self._query()
        self.assertEqual(resp.status, 200, f"expected status 200: {resp.status} {resp.text()[:300]}")
        doc = resp.json()
        for key in ("assetId", "schema", "table", "columns", "rows", "rowCount"):
            self.assertIn(key, doc, f"missing key in the data plane response: {key}")
        self.assertEqual(doc["assetId"], self.asset)
        self.assertEqual(doc["schema"], self.schema)
        self.assertEqual(doc["table"], self.table)
        self.assertIsInstance(doc["columns"], list)
        self.assertIsInstance(doc["rows"], list)
        self.assertEqual(doc["rowCount"], len(doc["rows"]),
                         "rowCount must equal len(rows) (flat JSON, spec §C1.7)")
        self.assertLessEqual(doc["rowCount"], self.limit)
        # rows = flat arrays (no nested objects)
        for row in doc["rows"][:5]:
            self.assertIsInstance(row, list, f"rows must be flat JSON arrays: {row}")

    def test_02_from_to_window_respected(self):
        frm, to = self._window()
        resp, _ = self._query(frm=frm, to=to)
        self.assertEqual(resp.status, 200)
        doc = resp.json()
        self.assertIn(self.time_column, doc["columns"], f"time column {self.time_column} missing")
        idx = doc["columns"].index(self.time_column)
        for row in doc["rows"]:
            ts = row[idx]
            self.assertGreaterEqual(str(ts), frm, f"row out of window (before from): {ts}")
            self.assertLessEqual(str(ts)[:19] + "Z" if "T" in str(ts) else str(ts), to,
                                 f"row out of window (after to): {ts}")

    def test_03_limit_5000_applied(self):
        """rowCount ≤ 5000 ; if the window holds more than 5000 rows, rowCount == 5000."""
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
                             f"LIMIT {self.limit} not applied: {total} rows in the window")
        else:
            self.assertEqual(doc["rowCount"], total,
                             f"all rows of the window must be served ({total})")

    # ----------------------------------------------------------- contract errors
    def test_04_no_contract_401_or_403_dspace_error(self):
        resp, _ = self._query(sign=False)
        self.assertIn(resp.status, (401, 403), f"without contract, expected 401/403, got {resp.status}")
        self.assertIn("dspace:Error", resp.text(),
                      f"the error must be a JSON-LD dspace:Error: {resp.text()[:300]}")

    def test_05_invalid_signature_403_dspace_error(self):
        resp, _ = self._query(raw_headers={"X-DSP-Signature": "sha256=0" * 32})
        self.assertIn(resp.status, (401, 403))
        self.assertIn("dspace:Error", resp.text())

    def test_06_unknown_asset_rejected(self):
        resp, _ = self._query(asset="dataset:facis:inexistant")
        self.assertIn(resp.status, (403, 404), f"undeclared asset: expected 403/404, got {resp.status}")
        self.assertIn("dspace:Error", resp.text())

    def test_07_invalid_window_400_dspace_error(self):
        resp, _ = self._query(frm="pas-une-date", to="non-plus")
        self.assertEqual(resp.status, 400, f"invalid from/to: expected 400, got {resp.status}")
        self.assertIn("dspace:Error", resp.text())
