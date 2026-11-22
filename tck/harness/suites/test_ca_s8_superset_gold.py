"""CA-S8 — Superset displays the gold dashboards on Postgres without modifying the
queries. Spec §10 CA-S8, §C1.8.

Automated verification: the EXACT dashboard queries
(tck/expected/superset_queries.sql, {from}/{to} placeholders) run as role
`reader` against the gold matviews and return the expected columns.

If superset.base.url + superset.api.token + superset.chart.ids are set,
a live check via the Superset API (chart data) is added; otherwise a documented SKIP.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from suites.base import TckTestCase


class CaS8SupersetGold(TckTestCase):
    """Gold dashboards served by Postgres, unchanged queries, reader role."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.queries_file = Path(__file__).resolve().parents[2] / "expected" / "superset_queries.sql"
        cls.superset_url = cls.t.opt("superset.base.url")
        cls.superset_token = cls.t.opt("superset.api.token")
        cls.chart_ids = [c for c in (cls.t.opt("superset.chart.ids") or "").split(",") if c]

    def _window(self) -> dict[str, str]:
        to = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        frm = to - dt.timedelta(days=7)
        return {"from": frm.isoformat().replace("+00:00", "Z"),
                "to": to.isoformat().replace("+00:00", "Z")}

    def _statements(self) -> list[str]:
        text = self.queries_file.read_text(encoding="utf-8")
        stmts = []
        for chunk in text.split(";"):
            chunk = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--"))
            chunk = chunk.strip()
            if chunk:
                stmts.append(chunk)
        return stmts

    def test_01_dashboard_queries_runnable_unmodified(self):
        """Each dashboard query ({from}/{to} window substituted) runs as role
        reader and returns a consistent result (columns present)."""
        win = self._window()
        stmts = self._statements()
        self.assertGreaterEqual(len(stmts), 5, "the superset_queries.sql file must contain the 5 F1-F3 queries")
        for stmt in stmts:
            q = stmt.format(**win)
            with self.subTest(query=q[:80]):
                rows = self.sql(q, role="reader")
                self.assertIsNotNone(rows, "the dashboard query must succeed as role reader")

    def test_02_columns_expected_by_dashboards(self):
        """The key columns used by the F1/F2/F3 screens are present in gold."""
        expectations = {
            "gold.city_dashboard_summary": ["updated_at"],
            "gold.net_grid_hourly": ["hour"],
            "gold.energy_cost_daily": ["day"],
            "gold.pv_performance_hourly": ["hour"],
            "gold.anomaly_candidates": ["event_timestamp"],
        }
        for table, cols in expectations.items():
            with self.subTest(table=table):
                for col in cols:
                    ok = self.t.pg_ok(
                        f"SELECT {col} FROM {table} LIMIT 1", "reader")
                    self.assertTrue(ok, f"column {col} missing or unreadable on {table} (reader role)")

    def test_03_superset_api_live_if_configured(self):
        """Optional live check via the Superset API (chart data). SKIP if not configured."""
        if not (self.superset_url and self.superset_token and self.chart_ids):
            self.skipTest("superset.base.url / superset.api.token / superset.chart.ids not "
                          "set: SQL-level check only (test_01/test_02)")
        for cid in self.chart_ids:
            with self.subTest(chart=cid):
                resp = self.t.http(
                    "POST", f"/api/v1/chart/data",
                    body={"datasource": {"id": int(cid), "type": "table"},
                          "queries": [{"filters": [], "row_limit": 10}]},
                    headers={"Authorization": f"Bearer {self.superset_token}"},
                    base=self.superset_url)
                self.assertEqual(resp.status, 200,
                                 f"chart {cid}: status {resp.status}: {resp.text()[:200]}")
