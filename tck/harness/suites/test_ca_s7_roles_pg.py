"""CA-S7 — PG roles: the `gate` role has no read, the `reader` role no
write; `app` writes app.*; the 4 roles exist. Spec §10 CA-S7, §C1.4 / §5.
"""

from __future__ import annotations

from harness.target import PgError
from suites.base import TckTestCase


class CaS7RolesPg(TckTestCase):
    """Strict separation of the PostgreSQL roles (gate / app / reader / admin)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bronze = cls.t.get("bronze.table.meter", "bronze.energy_meter")
        cls.silver = cls.t.get("silver.view.meter", "silver.energy_meter")
        cls.gold = cls.t.get("gold.table.hourly", "gold.net_grid_hourly")
        cls.dlq = cls.t.get("dlq.table", "dlq")
        cls.probe = "app.tck_role_probe"
        # probe table in app.* (created by admin; the app role must be able to write to it)
        cls.t.pg_sql(f"CREATE SCHEMA IF NOT EXISTS app; "
                     f"CREATE TABLE IF NOT EXISTS {cls.probe} (id int, note text)", "admin")

    def test_00_roles_existent(self):
        rows = self.sql("SELECT rolname FROM pg_roles WHERE rolname IN "
                        "('gate','app','reader','admin')")
        found = {r[0] for r in rows}
        self.assertEqual(found, {"gate", "app", "reader", "admin"},
                         f"the 4 roles must be provisioned: found={found}")

    def test_01_gate_cannot_read(self):
        """gate: any read is forbidden (spec §C1.4: INSERT bronze only)."""
        for q in (f"SELECT count(*) FROM {self.bronze}",
                  f"SELECT count(*) FROM {self.silver}",
                  f"SELECT count(*) FROM {self.gold}",
                  f"SELECT count(*) FROM {self.dlq}",
                  "SELECT 1 FROM pg_roles"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "gate"),
                                 f"the gate role must not be able to run: {q}")

    def test_02_gate_insert_bronze_allowed_other_denied(self):
        marker = f"gate-{self.marker}"
        payload = ('{"type":"smart_energy.meter","meter_id":"' + marker + '"}').replace("'", "''")
        # INSERT bronze allowed (the only right of the gate)
        self.assertTrue(self.t.pg_ok(
            f"INSERT INTO {self.bronze} (raw_payload, source_topic, source_system, ingest_timestamp) "
            f"VALUES ('{payload}', 'sim.smart_energy.meter', 'tck', now())", "gate"),
            "the gate role must be able to insert into bronze")
        # cleanup by admin
        self.sql(f"DELETE FROM {self.bronze} WHERE raw_payload LIKE '%{marker}%'", "admin")
        # any write outside bronze is denied
        for q in (f"INSERT INTO {self.silver} SELECT * FROM {self.silver} LIMIT 0",
                  f"DELETE FROM {self.gold}",
                  f"INSERT INTO {self.dlq} DEFAULT VALUES",
                  f"CREATE TABLE public.tck_gate_forbidden (id int)"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "gate"),
                                 f"the gate role must not be able to run: {q}")

    def test_03_reader_reads_gold_but_cannot_write(self):
        """reader: SELECT gold OK (data plane), any write denied."""
        self.assertTrue(self.t.pg_ok(f"SELECT count(*) FROM {self.gold}", "reader"),
                        "the reader role must read gold")
        for q in (f"INSERT INTO {self.gold} DEFAULT VALUES",
                  f"UPDATE {self.gold} SET 1 WHERE false",
                  f"DELETE FROM {self.gold}",
                  f"INSERT INTO {self.bronze} (raw_payload) VALUES ('{{}}')",
                  f"DELETE FROM {self.dlq}",
                  f"INSERT INTO {self.probe} VALUES (1, 'x')"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "reader"),
                                 f"the reader role must not perform any write: {q}")

    def test_04_app_writes_app_but_not_fap_domains(self):
        """app: write on app.* OK (reserved for step 2), write on bronze/silver/gold denied."""
        self.assertTrue(self.t.pg_ok(
            f"INSERT INTO {self.probe} VALUES (1, '{self.marker}')", "app"),
            "the app role must write into app.*")
        self.sql(f"DELETE FROM {self.probe} WHERE note = '{self.marker}'", "admin")
        for q in (f"INSERT INTO {self.bronze} (raw_payload) VALUES ('{{}}')",
                  f"DELETE FROM {self.gold}",
                  f"INSERT INTO {self.dlq} DEFAULT VALUES"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "app"),
                                 f"the app role must not touch the FAP domains: {q}")

    def test_05_admin_sanity(self):
        """Sanity: admin can read and write (otherwise the harness cannot run)."""
        self.assertTrue(self.t.pg_ok(f"SELECT count(*) FROM {self.bronze}", "admin"))
