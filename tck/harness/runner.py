#!/usr/bin/env python3
"""Runner of the FAP-light server TCK.

Discovers the suites `harness/suites/test_ca_s*.py`, runs them (unittest) and produces:
  - a full console log + `evidence/tck-run-<stamp>.log` ;
  - a JUnit XML report `evidence/junit-<stamp>.xml`.

Usage:
  python3 runner.py [--properties tck.properties] [--suites CA-S1,CA-S4] [--list] [--evidence-dir DIR]

Exit code: 0 if all tests pass (skips allowed), 1 otherwise, 2 if the config is invalid.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import unittest
import xml.sax.saxutils as su
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
SUITES_DIR = HARNESS_DIR / "suites"
sys.path.insert(0, str(HARNESS_DIR.parent))  # pour `import harness...`

from harness.target import ConfigError, TCK_DIR, load_properties  # noqa: E402


def normalize_ca(token: str) -> str:
    """'CA-S1' / 'ca-s1' / 'S1' / 's1' -> 'CA-S1' (normalized acceptance criterion id)."""
    t = token.strip().upper()
    if not t.startswith("CA-"):
        t = "CA-" + t
    return t


def discover_suite_modules(suite_filter: list[str] | None):
    """Map CA-Sx -> module test_ca_sx_*.py ; returns [(ca_id, module), ...]."""
    wanted = {normalize_ca(s) for s in suite_filter} if suite_filter else None
    modules = []
    for f in sorted(SUITES_DIR.glob("test_ca_s*.py")):
        mod_name = f.stem
        ca = mod_name.split("_")[2].upper()  # test_ca_s1_... -> S1
        ca_id = f"CA-{ca}"
        if wanted and ca_id not in wanted:
            continue
        modules.append((ca_id, mod_name))
    return modules


def load_tests(mod_names):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for _, mod in mod_names:
        suite.addTests(loader.loadTestsFromName(f"suites.{mod}"))
    return suite


class JUnitWriter:
    """Writes a minimal JUnit XML from a TimingResult (which keeps every executed case)."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, result: "TimingResult", suite_name: str):
        durations = result.durations
        lines = ['<?xml version="1.0" encoding="utf-8"?>']
        lines.append(
            f'<testsuite name="{su.escape(suite_name)}" tests="{result.testsRun}" '
            f'failures="{len(result.failures)}" errors="{len(result.errors)}" '
            f'skipped="{len(result.skipped)}" time="{sum(durations.values()):.3f}">'
        )
        failed = {id(c): ("failure", tb) for c, tb in result.failures}
        failed.update({id(c): ("error", tb) for c, tb in result.errors})
        skipped = {id(c): reason for c, reason in result.skipped}
        for case in result._all:
            cls = su.escape(case.__class__.__module__)
            name = su.escape(case._testMethodName)
            t = f"{durations.get(id(case), 0):.3f}"
            if id(case) in failed:
                tag, tb = failed[id(case)]
                msg = su.escape(tb.strip().splitlines()[-1][:300] if tb.strip() else "")
                lines.append(f'  <testcase classname="{cls}" name="{name}" time="{t}">')
                lines.append(f'    <{tag} message="{msg}">{su.escape(tb)}</{tag}>')
                lines.append("  </testcase>")
            elif id(case) in skipped:
                lines.append(f'  <testcase classname="{cls}" name="{name}" time="{t}">')
                lines.append(f'    <skipped message="{su.escape(skipped[id(case)])}"/>')
                lines.append("  </testcase>")
            else:
                lines.append(f'  <testcase classname="{cls}" name="{name}" time="{t}"/>')
        lines.append("</testsuite>")
        self.path.write_text("\n".join(lines), encoding="utf-8")


class TimingResult(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.durations = {}
        self._all = []
        self._start = {}

    def startTest(self, test):
        self._start[id(test)] = time.time()
        self._all.append(test)
        super().startTest(test)

    def stopTest(self, test):
        self.durations[id(test)] = time.time() - self._start.get(id(test), time.time())
        super().stopTest(test)


def main() -> int:
    ap = argparse.ArgumentParser(description="FAP-light server TCK")
    ap.add_argument("--properties", default=str(TCK_DIR / "tck.properties"))
    ap.add_argument("--suites", default=None,
                    help="filter, e.g. CA-S1,CA-S4 (or S1,S4)")
    ap.add_argument("--list", action="store_true", help="list discovered tests without running them")
    ap.add_argument("--evidence-dir", default=str(TCK_DIR / "evidence"))
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%dT%H%M%SZ")
    ev_dir = Path(args.evidence_dir)
    ev_dir.mkdir(parents=True, exist_ok=True)
    log_path = ev_dir / f"tck-run-{stamp}.log"
    junit_path = ev_dir / f"junit-{stamp}.xml"

    log_fh = open(log_path, "w", encoding="utf-8")

    def emit(msg=""):
        print(msg)
        print(msg, file=log_fh)

    try:
        props = load_properties(args.properties)
    except ConfigError as e:
        emit(f"CONFIG ERROR: {e}")
        return 2

    os.environ["TCK_RUN_ID"] = stamp
    os.environ["TCK_PROPERTIES"] = str(Path(args.properties).resolve())
    sys.path.insert(0, str(SUITES_DIR.parent))

    suite_filter = [s.strip() for s in args.suites.split(",")] if args.suites else None
    mods = discover_suite_modules(suite_filter)
    if not mods:
        emit("No suite matches the filter.")
        return 2

    emit(f"TCK FAP-light — run {stamp}")
    emit(f"Properties: {args.properties}")
    emit(f"Suites    : {', '.join(m for _, m in mods)}")
    emit(f"Evidence  : {log_path}")
    emit("")

    if args.list:
        loader = unittest.TestLoader()
        count = 0
        for ca_id, mod in mods:
            ts = loader.loadTestsFromName(f"suites.{mod}")
            for group in ts:
                for case in group:
                    emit(f"{ca_id}  {case.id()}")
                    count += 1
        emit("")
        emit(f"Total: {count} test(s) discovered")
        return 0

    suite = load_tests(mods)

    class Tee(io.StringIO):
        def write(self, s):
            if s.strip():
                emit(s.rstrip("\n"))
            return super().write(s)

    runner = unittest.TextTestRunner(stream=Tee(), resultclass=TimingResult, verbosity=2)
    result = runner.run(suite)

    emit("")
    emit(f"Tests run: {result.testsRun} | OK: {result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)}"
         f" | FAILURES: {len(result.failures)} | ERRORS: {len(result.errors)} | SKIPPED: {len(result.skipped)}")
    for case, tb in result.failures + result.errors:
        emit(f"  [FAIL] {case.id()}: {tb.strip().splitlines()[-1][:200]}")
    for case, reason in result.skipped:
        emit(f"  [SKIP] {case.id()}: {reason}")

    JUnitWriter(junit_path).write(result, "fap-light-serveur-tck")
    emit(f"JUnit report: {junit_path}")
    log_fh.close()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
