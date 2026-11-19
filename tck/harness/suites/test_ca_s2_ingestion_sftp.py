"""CA-S2 — Ingestion SFTP : cycle 60 s, garde extension/taille, parsers JSON/CSV,
archive /archive, curseur ; fichier invalide -> DLQ (error_type/message/ts + payload brut).
Spec §10 CA-S2, §C1.3.

Chaque test utilise son propre marqueur de fichier (dérivé du run) pour ne pas
se confondre avec l'injection simulation de CA-S1.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from suites.base import TckTestCase


class CaS2IngestionSftp(TckTestCase):
    """Dépôt SFTP ingéré, archivé, curseur enregistré ; fichier invalide en DLQ."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bronze = cls.t.get("bronze.table.meter", "bronze.energy_meter")
        cls.dlq = cls.t.get("dlq.table", "dlq")
        cls.cursors = cls.t.get("cursors.table", "_cursors")
        cls.ingest_dir = cls.t.get("sftp.ingest.dir", "/ingest")
        cls.archive_dir = cls.t.get("sftp.archive.dir", "/archive")
        cls.cycle = cls.t.get_int("sftp.cycle.s", 60)
        cls.wait_s = cls.cycle + cls.t.get_int("sftp.margin.s", 45)

    def _deposit_text(self, content: str, remote_name: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=Path(remote_name).suffix, delete=False) as f:
            f.write(content)
            local = f.name
        self.t.sftp_put(local, f"{self.ingest_dir}/{remote_name}")
        return remote_name

    def _deposit_fixture(self, fixture: str, remote_name: str, file_marker: str) -> str:
        content = self.t.fixture(fixture, MARKER=file_marker, SITE=self.site, TS=self.now_iso())
        return self._deposit_text(content, remote_name)

    # ------------------------------------------------------- fichier valide
    def test_01_fichier_json_valide_ingere_archive_curseur(self):
        fm = f"{self.marker}-sftpjson"
        name = f"tck-{fm}.json"
        self._deposit_fixture("sftp_meter_valid.json", name, fm)

        self.wait_for(
            lambda: self.sql(f"SELECT 1 FROM {self.bronze} "
                             f"WHERE raw_payload LIKE '%{fm}%'"),
            timeout_s=self.wait_s, desc=f"ingestion bronze du fichier {name}")

        # archivé dans /archive
        listing = self.t.sftp_ls(self.archive_dir)
        self.assertIn(name, listing, f"fichier non archivé dans {self.archive_dir}: {listing}")

        # curseur enregistré (fichier traité)
        cur = self.sql(f"SELECT count(*) FROM {self.cursors} "
                       f"WHERE file_name = {self.quote(name)}")
        self.assertGreater(int(cur[0][0]), 0, f"curseur non enregistré pour {name}")

    def test_02_fichier_csv_valide_parse(self):
        fm = f"{self.marker}-sftpcsv"
        name = f"tck-{fm}.csv"
        self._deposit_fixture("sftp_meter_valid.csv", name, fm)

        self.wait_for(
            lambda: self.sql(f"SELECT 1 FROM {self.bronze} "
                             f"WHERE raw_payload LIKE '%{fm}%'"),
            timeout_s=self.wait_s, desc=f"ingestion bronze du CSV {name}")

    # ------------------------------------------------------ fichier invalide
    def test_03_fichier_json_invalide_part_en_dlq(self):
        fm = f"{self.marker}-sftpinv"
        name = f"tck-{fm}.json"
        self._deposit_fixture("sftp_invalid.json", name, fm)

        self.wait_for(
            lambda: self.sql(
                f"SELECT error_type, message, ts FROM {self.dlq} "
                f"WHERE payload LIKE '%{fm}%' OR file_name = {self.quote(name)}"),
            timeout_s=self.wait_s, desc=f"DLQ pour le fichier invalide {name}")
        rows = self.sql(
            f"SELECT error_type, message, ts, payload FROM {self.dlq} "
            f"WHERE payload LIKE '%{fm}%' OR file_name = {self.quote(name)} LIMIT 1")
        self.assertTrue(rows, "ligne DLQ introuvable")
        error_type, message, ts, payload = rows[0]
        self.assertTrue(error_type, "DLQ: error_type requis et non vide")
        self.assertTrue(message, "DLQ: message requis et non vide")
        self.assertTrue(ts, "DLQ: ts requis et non vide")
        self.assertTrue(payload and fm in payload, "DLQ: payload brut requis (fidélité)")
        # aucune ligne bronze pour ce fichier
        bronze = self.sql(f"SELECT count(*) FROM {self.bronze} WHERE raw_payload LIKE '%{fm}%'")
        self.assertEqual(int(bronze[0][0]), 0, "un fichier invalide ne doit pas atterrir en bronze")

    def test_04_fichier_bad_extension_rejete(self):
        fm = f"{self.marker}-badext"
        name = f"tck-{fm}.txt"
        self._deposit_fixture("sftp_bad_extension.txt", name, fm)

        self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.dlq} WHERE file_name = {self.quote(name)} "
                f"UNION ALL SELECT 1 FROM {self.cursors} WHERE file_name = {self.quote(name)}"),
            timeout_s=self.wait_s, desc=f"rejet structuré (DLQ ou curseur) pour {name}")
        bronze = self.sql(f"SELECT count(*) FROM {self.bronze} "
                          f"WHERE raw_payload LIKE '%{fm}%' OR raw_payload LIKE '%{name}%'")
        self.assertEqual(int(bronze[0][0]), 0, "un fichier .txt ne doit pas atterrir en bronze")
