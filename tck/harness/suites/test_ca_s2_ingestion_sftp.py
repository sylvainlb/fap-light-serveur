"""CA-S2 — SFTP ingestion: 60 s cycle, extension/size guard, JSON/CSV parsers,
archive /archive, cursor; invalid file -> DLQ (error_type/message/ts + raw payload).
Spec §10 CA-S2, §C1.3.

Each test uses its own file marker (derived from the run) so as not to be
confused with the CA-S1 simulation injection.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from suites.base import TckTestCase


class CaS2IngestionSftp(TckTestCase):
    """SFTP drop ingested, archived, cursor recorded; invalid file goes to the DLQ."""

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

    # ------------------------------------------------------- valid file
    def test_01_valid_json_file_ingested_archived_cursor(self):
        fm = f"{self.marker}-sftpjson"
        name = f"tck-{fm}.json"
        self._deposit_fixture("sftp_meter_valid.json", name, fm)

        self.wait_for(
            lambda: self.sql(f"SELECT 1 FROM {self.bronze} "
                             f"WHERE raw_payload LIKE '%{fm}%'"),
            timeout_s=self.wait_s, desc=f"bronze ingestion of file {name}")

        # archived in /archive
        listing = self.t.sftp_ls(self.archive_dir)
        self.assertIn(name, listing, f"file not archived in {self.archive_dir}: {listing}")

        # cursor recorded (file processed)
        cur = self.sql(f"SELECT count(*) FROM {self.cursors} "
                       f"WHERE file_name = {self.quote(name)}")
        self.assertGreater(int(cur[0][0]), 0, f"cursor not recorded for {name}")

    def test_02_valid_csv_file_parsed(self):
        fm = f"{self.marker}-sftpcsv"
        name = f"tck-{fm}.csv"
        self._deposit_fixture("sftp_meter_valid.csv", name, fm)

        self.wait_for(
            lambda: self.sql(f"SELECT 1 FROM {self.bronze} "
                             f"WHERE raw_payload LIKE '%{fm}%'"),
            timeout_s=self.wait_s, desc=f"bronze ingestion of CSV {name}")

    # ------------------------------------------------------ invalid file
    def test_03_invalid_json_file_goes_to_dlq(self):
        fm = f"{self.marker}-sftpinv"
        name = f"tck-{fm}.json"
        self._deposit_fixture("sftp_invalid.json", name, fm)

        self.wait_for(
            lambda: self.sql(
                f"SELECT error_type, message, ts FROM {self.dlq} "
                f"WHERE payload LIKE '%{fm}%' OR file_name = {self.quote(name)}"),
            timeout_s=self.wait_s, desc=f"DLQ for invalid file {name}")
        rows = self.sql(
            f"SELECT error_type, message, ts, payload FROM {self.dlq} "
            f"WHERE payload LIKE '%{fm}%' OR file_name = {self.quote(name)} LIMIT 1")
        self.assertTrue(rows, "DLQ line not found")
        error_type, message, ts, payload = rows[0]
        self.assertTrue(error_type, "DLQ: error_type required and non-empty")
        self.assertTrue(message, "DLQ: message required and non-empty")
        self.assertTrue(ts, "DLQ: ts required and non-empty")
        self.assertTrue(payload and fm in payload, "DLQ: raw payload required (fidelity)")
        # no bronze line for this file
        bronze = self.sql(f"SELECT count(*) FROM {self.bronze} WHERE raw_payload LIKE '%{fm}%'")
        self.assertEqual(int(bronze[0][0]), 0, "an invalid file must not land in bronze")

    def test_04_bad_extension_rejected(self):
        fm = f"{self.marker}-badext"
        name = f"tck-{fm}.txt"
        self._deposit_fixture("sftp_bad_extension.txt", name, fm)

        self.wait_for(
            lambda: self.sql(
                f"SELECT 1 FROM {self.dlq} WHERE file_name = {self.quote(name)} "
                f"UNION ALL SELECT 1 FROM {self.cursors} WHERE file_name = {self.quote(name)}"),
            timeout_s=self.wait_s, desc=f"structured rejection (DLQ or cursor) for {name}")
        bronze = self.sql(f"SELECT count(*) FROM {self.bronze} "
                          f"WHERE raw_payload LIKE '%{fm}%' OR raw_payload LIKE '%{name}%'")
        self.assertEqual(int(bronze[0][0]), 0, "a .txt file must not land in bronze")
