"""CA-S8 — Superset affiche les dashboards gold sur Postgres sans modification des
requêtes. Spec §10 CA-S8, §C1.8.

Vérification automatisée : les requêtes EXACTES des dashboards
(tck/expected/superset_queries.sql, placeholders {from}/{to}) s'exécutent en rôle
`reader` contre les matviews gold et retournent les colonnes attendues.

Si superset.base.url + superset.api.token + superset.chart.ids sont renseignés,
un contrôle live via l'API Superset (chart data) est ajouté ; sinon SKIP documenté.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from suites.base import TckTestCase


class CaS8SupersetGold(TckTestCase):
    """Dashboards gold servis par Postgres, requêtes inchangées, rôle reader."""

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

    def test_01_requetes_dashboards_exécutables_sans_modification(self):
        """Chaque requête dashboard (fenêtre {from}/{to} substituée) tourne en rôle
        reader et retourne un résultat cohérent (colonnes présentes)."""
        win = self._window()
        stmts = self._statements()
        self.assertGreaterEqual(len(stmts), 5, "le fichier superset_queries.sql doit contenir les 5 requêtes F1-F3")
        for stmt in stmts:
            q = stmt.format(**win)
            with self.subTest(query=q[:80]):
                rows = self.sql(q, role="reader")
                self.assertIsNotNone(rows, "la requête dashboard doit réussir en rôle reader")

    def test_02_colonnes_attendues_par_les_dashboards(self):
        """Les colonnes clés utilisées par les écrans F1/F2/F3 sont présentes dans le gold."""
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
                    self.assertTrue(ok, f"colonne {col} absente ou illisible sur {table} (rôle reader)")

    def test_03_api_superset_live_si_configuree(self):
        """Contrôle live optionnel via l'API Superset (chart data). SKIP si non configuré."""
        if not (self.superset_url and self.superset_token and self.chart_ids):
            self.skipTest("superset.base.url / superset.api.token / superset.chart.ids non "
                          "renseignés : vérification SQL seule (test_01/test_02)")
        for cid in self.chart_ids:
            with self.subTest(chart=cid):
                resp = self.t.http(
                    "POST", f"/api/v1/chart/data",
                    body={"datasource": {"id": int(cid), "type": "table"},
                          "queries": [{"filters": [], "row_limit": 10}]},
                    headers={"Authorization": f"Bearer {self.superset_token}"},
                    base=self.superset_url)
                self.assertEqual(resp.status, 200,
                                 f"chart {cid}: statut {resp.status}: {resp.text()[:200]}")
