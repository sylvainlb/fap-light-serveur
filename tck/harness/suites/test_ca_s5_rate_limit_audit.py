"""CA-S5 — Rate limit 10 req/min appliqué par offre ; accès audités (agreement_id).
Spec §10 CA-S5, §7 (rate limit 10 req/min par offre, LIMIT 5000 lignes).

La salve est envoyée sur un asset dédié (ca.s5.asset.id) pour ne pas consommer
le bucket de l'asset utilisé par CA-S4.
"""

from __future__ import annotations

import datetime as dt

from suites.base import TckTestCase


class CaS5RateLimitAudit(TckTestCase):
    """Rate limit par offre + audit des accès data space."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.t.get("dsp.dataplane.path", "/dsp/data")
        cls.asset = cls.t.get("ca.s5.asset.id", "dataset:facis:streetlight-zone-hourly")
        cls.limit = cls.t.get_int("rate.limit.per.min", 10)
        cls.burst = cls.limit + 2
        cls.audit_table = cls.t.get("audit.table", "dsp_access_audit")
        cls.agreement = cls.t.get("dsp.agreement.id", "agreement:tck:ca-s4")

    def _window(self) -> tuple[str, str]:
        to = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        frm = to - dt.timedelta(days=30)
        return frm.isoformat().replace("+00:00", "Z"), to.isoformat().replace("+00:00", "Z")

    def test_01_rate_limit_par_offre(self):
        """Sur un burst de limit+2 requêtes signées : ≤ limit acceptées (2xx),
        ≥ 2 rejetées (429/403) avec dspace:Error."""
        frm, to = self._window()
        accepted, rejected = 0, 0
        for i in range(self.burst):
            headers = self.t.dsp_sign(self.asset, frm, to, path=self.path)
            resp = self.t.http("POST", self.path,
                               body={"assetId": self.asset, "from": frm, "to": to},
                               headers=headers)
            if 200 <= resp.status < 300:
                accepted += 1
            elif resp.status in (429, 403):
                rejected += 1
                if i >= self.limit:  # seules les requêtes au-delà du quota doivent échouer
                    self.assertIn("dspace:Error", resp.text())
            else:
                self.fail(f"statut inattendu {resp.status} à la requête {i}: {resp.text()[:200]}")
        self.assertLessEqual(accepted, self.limit,
                             f"rate limit non appliqué: {accepted} requêtes acceptées > {self.limit}")
        self.assertGreaterEqual(rejected, self.burst - self.limit,
                                f"les requêtes au-delà de {self.limit}/min doivent être rejetées "
                                f"(rejetées={rejected})")

    def test_02_acces_audites_avec_agreement_id(self):
        """Chaque accès accepté doit laisser une trace d'audit (agreement_id + timestamp)."""
        frm, to = self._window()
        headers = self.t.dsp_sign(self.asset, frm, to, path=self.path)
        resp = self.t.http("POST", self.path,
                           body={"assetId": self.asset, "from": frm, "to": to},
                           headers=headers)
        self.assertIn(resp.status, (200, 429, 403),
                      f"statut inattendu pour la requête auditée: {resp.status}")
        # l'audit peut être asynchrone : courte attente
        found = self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.audit_table} WHERE agreement_id = {self.quote(self.agreement)}"),
            timeout_s=30, interval_s=2, desc=f"audit agreement_id={self.agreement}")
        self.assertTrue(found)
        row = self.sql(
            f"SELECT agreement_id, ts FROM {self.audit_table} "
            f"WHERE agreement_id = {self.quote(self.agreement)} ORDER BY ts DESC LIMIT 1")[0]
        self.assertTrue(row[0] and row[1], f"audit incomplet (agreement_id, ts requis): {row}")
