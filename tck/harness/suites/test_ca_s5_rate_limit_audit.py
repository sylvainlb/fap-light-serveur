"""CA-S5 — Rate limit 10 req/min applied per offer; accesses audited (agreement_id).
Spec §10 CA-S5, §7 (rate limit 10 req/min per offer, LIMIT 5000 rows).

The burst is sent on a dedicated asset (ca.s5.asset.id) so as not to consume
the bucket of the asset used by CA-S4.
"""

from __future__ import annotations

import datetime as dt

from suites.base import TckTestCase


class CaS5RateLimitAudit(TckTestCase):
    """Rate limit per offer + data space access audit."""

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

    def test_01_rate_limit_per_offer(self):
        """On a burst of limit+2 signed requests: ≤ limit accepted (2xx),
        ≥ 2 rejected (429/403) with dspace:Error."""
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
                if i >= self.limit:  # only requests beyond the quota must fail
                    self.assertIn("dspace:Error", resp.text())
            else:
                self.fail(f"unexpected status {resp.status} on request {i}: {resp.text()[:200]}")
        self.assertLessEqual(accepted, self.limit,
                             f"rate limit not applied: {accepted} requests accepted > {self.limit}")
        self.assertGreaterEqual(rejected, self.burst - self.limit,
                                f"requests beyond {self.limit}/min must be rejected "
                                f"(rejected={rejected})")

    def test_02_accesses_audited_with_agreement_id(self):
        """Each accepted access must leave an audit trace (agreement_id + timestamp)."""
        frm, to = self._window()
        headers = self.t.dsp_sign(self.asset, frm, to, path=self.path)
        resp = self.t.http("POST", self.path,
                           body={"assetId": self.asset, "from": frm, "to": to},
                           headers=headers)
        self.assertIn(resp.status, (200, 429, 403),
                      f"unexpected status for the audited request: {resp.status}")
        # the audit may be asynchronous: short wait
        found = self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.audit_table} WHERE agreement_id = {self.quote(self.agreement)}"),
            timeout_s=30, interval_s=2, desc=f"audit agreement_id={self.agreement}")
        self.assertTrue(found)
        row = self.sql(
            f"SELECT agreement_id, ts FROM {self.audit_table} "
            f"WHERE agreement_id = {self.quote(self.agreement)} ORDER BY ts DESC LIMIT 1")[0]
        self.assertTrue(row[0] and row[1], f"incomplete audit (agreement_id, ts required): {row}")
