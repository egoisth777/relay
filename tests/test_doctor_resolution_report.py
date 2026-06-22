"""Slice s-store-root-resolution / ticket t-doctor-resolution-report.

Written test-first; expected to FAIL until `doctor` reports the resolved root AND the
resolution layer. The ticket converges when these pass.

Contract: `doctor` JSON gains a `resolution` object:
    {"layer": "flag" | "env" | "marker" | "none", "marker": "<path>" | null}
- `conv_root` reflects the resolved root for flag/env/marker layers.
- When nothing resolves, `doctor` reports it clearly and never crashes with a traceback
  (either layer == "none" on exit 0, or a clean ConvError on exit 2).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import clean_env, load_json, run_cli  # noqa: E402


class DoctorResolutionReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def test_reports_flag_layer(self) -> None:
        root = self.tmp / "conv"
        proc = run_cli(["doctor", "--conv-root", root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), root)
        self.assertEqual(out["resolution"]["layer"], "flag")

    def test_reports_env_layer(self) -> None:
        root = self.tmp / "conv"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(BRAIN_CONV=root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), root)
        self.assertEqual(out["resolution"]["layer"], "env")

    def test_reports_marker_layer_and_matched_path(self) -> None:
        (self.tmp / "conv").mkdir()
        work = self.tmp / "work"
        work.mkdir()
        proc = run_cli(["doctor"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), self.tmp / "conv")
        self.assertEqual(out["resolution"]["layer"], "marker")
        self.assertTrue(out["resolution"]["marker"], "marker layer must report what matched")
        self.assertIn(
            Path(out["resolution"]["marker"]).resolve(),
            {self.tmp, self.tmp / "conv"},
        )

    def test_unresolvable_reports_clearly_without_crashing(self) -> None:
        proc = run_cli(["doctor"], cwd=self.tmp)
        self.assertNotIn("Traceback", proc.stderr)
        if proc.returncode == 0:
            self.assertEqual(load_json(proc)["resolution"]["layer"], "none")
        else:
            self.assertEqual(proc.returncode, 2)
            self.assertIn("conv:", proc.stderr)


if __name__ == "__main__":
    unittest.main()
