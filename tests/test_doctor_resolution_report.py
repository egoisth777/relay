"""Slice s-store-root-resolution / ticket t-doctor-resolution-report.

Contract: `doctor` JSON carries a `resolution` object:
    {"layer": "flag"|"env-conversate"|"env-brain"|"marker"|"none", "marker": "<path>"|null}
- `conv_root` reflects the resolved root for flag/env/marker layers.
- When nothing resolves, `doctor` reports it clearly and never crashes with a traceback
  (either layer == "none" on exit 0, or a clean ConvError on exit 2).
- `doctor` WARNs about records that predate the resumption sections.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import clean_env, load_json, run_cli  # noqa: E402


LEGACY_RECORD = """+++
id = "conv_260101_legacy"
topic = "legacy record"
status = "active"
tags = []
refs = []
created = "2026-01-01T00:00:00Z"
updated = "2026-01-01T00:00:00Z"
+++
## summary
an old record with no resumption sections

## dict
- **x** - y

## qa
- **Q:** q? **A:** a.
"""


class DoctorResolutionReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def test_reports_flag_layer(self) -> None:
        root = self.tmp / ".conversate"
        proc = run_cli(["doctor", "--conv-root", root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), root)
        self.assertEqual(out["resolution"]["layer"], "flag")

    def test_reports_env_conversate_layer(self) -> None:
        root = self.tmp / ".conversate"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(CONVERSATE_ROOT=root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), root)
        self.assertEqual(out["resolution"]["layer"], "env-conversate")

    def test_reports_env_brain_layer(self) -> None:
        root = self.tmp / ".conversate"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(BRAIN_CONV=root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), root)
        self.assertEqual(out["resolution"]["layer"], "env-brain")

    def test_reports_marker_layer_and_matched_path(self) -> None:
        (self.tmp / ".conversate").mkdir()
        work = self.tmp / "work"
        work.mkdir()
        proc = run_cli(["doctor"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), self.tmp / ".conversate")
        self.assertEqual(out["resolution"]["layer"], "marker")
        self.assertTrue(out["resolution"]["marker"], "marker layer must report what matched")
        self.assertIn(
            Path(out["resolution"]["marker"]).resolve(),
            {self.tmp, self.tmp / ".conversate"},
        )

    def test_unresolvable_reports_clearly_without_crashing(self) -> None:
        proc = run_cli(["doctor"], cwd=self.tmp)
        self.assertNotIn("Traceback", proc.stderr)
        if proc.returncode == 0:
            self.assertEqual(load_json(proc)["resolution"]["layer"], "none")
        else:
            self.assertEqual(proc.returncode, 2)
            self.assertIn("conv:", proc.stderr)

    def test_warns_on_records_missing_resumption_sections(self) -> None:
        root = self.tmp / ".conversate"
        self.assertEqual(run_cli(["init", "--conv-root", root], cwd=self.tmp).returncode, 0)
        (root / "convs" / "2026-01-01_legacy.md").write_text(LEGACY_RECORD, encoding="utf-8")
        proc = run_cli(["doctor", "--conv-root", root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(out["parse_errors"], [], "a legacy record must still parse")
        warned = [w for w in out["warnings"] if "legacy" in w["file"]]
        self.assertEqual(len(warned), 1, out["warnings"])
        self.assertEqual(
            set(warned[0]["missing_sections"]),
            {"resume", "user-instructions", "condensed-transcript"},
        )


if __name__ == "__main__":
    unittest.main()
