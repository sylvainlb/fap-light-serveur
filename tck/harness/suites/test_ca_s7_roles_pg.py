"""CA-S7 — Rôles PG : le rôle `gate` n'a aucune lecture, le rôle `reader` aucune
écriture ; `app` écrit app.* ; les 4 rôles existent. Spec §10 CA-S7, §C1.4 / §5.
"""

from __future__ import annotations

from harness.target import PgError
from suites.base import TckTestCase


class CaS7RolesPg(TckTestCase):
    """Séparation stricte des rôles PostgreSQL (gate / app / reader / admin)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bronze = cls.t.get("bronze.table.meter", "bronze.energy_meter")
        cls.silver = cls.t.get("silver.view.meter", "silver.energy_meter")
        cls.gold = cls.t.get("gold.table.hourly", "gold.net_grid_hourly")
        cls.dlq = cls.t.get("dlq.table", "dlq")
        cls.probe = "app.tck_role_probe"
        # table sonde dans app.* (créée par admin ; le rôle app doit pouvoir y écrire)
        cls.t.pg_sql(f"CREATE SCHEMA IF NOT EXISTS app; "
                     f"CREATE TABLE IF NOT EXISTS {cls.probe} (id int, note text)", "admin")

    def test_00_roles_existent(self):
        rows = self.sql("SELECT rolname FROM pg_roles WHERE rolname IN "
                        "('gate','app','reader','admin')")
        found = {r[0] for r in rows}
        self.assertEqual(found, {"gate", "app", "reader", "admin"},
                         f"les 4 rôles doivent être provisionnés: trouvés={found}")

    def test_01_gate_ne_peut_pas_lire(self):
        """gate : toute lecture est interdite (spec §C1.4 : INSERT bronze only)."""
        for q in (f"SELECT count(*) FROM {self.bronze}",
                  f"SELECT count(*) FROM {self.silver}",
                  f"SELECT count(*) FROM {self.gold}",
                  f"SELECT count(*) FROM {self.dlq}",
                  "SELECT 1 FROM pg_roles"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "gate"),
                                 f"le rôle gate ne doit pas pouvoir exécuter: {q}")

    def test_02_gate_insert_bronze_autorise_autre_refuse(self):
        marker = f"gate-{self.marker}"
        payload = ('{"type":"smart_energy.meter","meter_id":"' + marker + '"}').replace("'", "''")
        # INSERT bronze autorisé (c'est le seul droit du gate)
        self.assertTrue(self.t.pg_ok(
            f"INSERT INTO {self.bronze} (raw_payload, source_topic, source_system, ingest_timestamp) "
            f"VALUES ('{payload}', 'sim.smart_energy.meter', 'tck', now())", "gate"),
            "le rôle gate doit pouvoir insérer en bronze")
        # nettoyage par admin
        self.sql(f"DELETE FROM {self.bronze} WHERE raw_payload LIKE '%{marker}%'", "admin")
        # toute écriture hors bronze est refusée
        for q in (f"INSERT INTO {self.silver} SELECT * FROM {self.silver} LIMIT 0",
                  f"DELETE FROM {self.gold}",
                  f"INSERT INTO {self.dlq} DEFAULT VALUES",
                  f"CREATE TABLE public.tck_gate_forbidden (id int)"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "gate"),
                                 f"le rôle gate ne doit pas pouvoir exécuter: {q}")

    def test_03_reader_lit_gold_mais_n_ecrit_pas(self):
        """reader : SELECT gold OK (data plane), toute écriture refusée."""
        self.assertTrue(self.t.pg_ok(f"SELECT count(*) FROM {self.gold}", "reader"),
                        "le rôle reader doit lire le gold")
        for q in (f"INSERT INTO {self.gold} DEFAULT VALUES",
                  f"UPDATE {self.gold} SET 1 WHERE false",
                  f"DELETE FROM {self.gold}",
                  f"INSERT INTO {self.bronze} (raw_payload) VALUES ('{{}}')",
                  f"DELETE FROM {self.dlq}",
                  f"INSERT INTO {self.probe} VALUES (1, 'x')"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "reader"),
                                 f"le rôle reader ne doit aucune écriture: {q}")

    def test_04_app_ecrit_app_mais_pas_les_domaines_fap(self):
        """app : écriture app.* OK (réservé étape 2), écriture bronze/silver/gold refusée."""
        self.assertTrue(self.t.pg_ok(
            f"INSERT INTO {self.probe} VALUES (1, '{self.marker}')", "app"),
            "le rôle app doit écrire dans app.*")
        self.sql(f"DELETE FROM {self.probe} WHERE note = '{self.marker}'", "admin")
        for q in (f"INSERT INTO {self.bronze} (raw_payload) VALUES ('{{}}')",
                  f"DELETE FROM {self.gold}",
                  f"INSERT INTO {self.dlq} DEFAULT VALUES"):
            with self.subTest(q=q):
                self.assertFalse(self.t.pg_ok(q, "app"),
                                 f"le rôle app ne doit pas toucher aux domaines FAP: {q}")

    def test_05_admin_sanity(self):
        """Sanity : admin peut lire et écrire (sinon le harness ne peut pas tourner)."""
        self.assertTrue(self.t.pg_ok(f"SELECT count(*) FROM {self.bronze}", "admin"))
