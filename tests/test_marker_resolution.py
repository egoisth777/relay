"""Slice s-store-root-resolution / ticket t-marker-resolution.

These tests DEFINE the ticket. They are written test-first and are expected to FAIL
against the current `conv_cli.py` (which uses the brittle `parents[4]` fallback, writes
no sentinel, and has no marker search). The ticket converges when they pass.

Contract under test (conv_root resolution order):
  1. --conv-root flag
  2. $BRAIN_CONV env
  3. marker search from cwd, then the script dir, nearest ancestor first:
       - a dir containing a `.conv-root` sentinel file -> that dir IS the root
       - a dir containing a `conv/` subdirectory        -> <dir>/conv
  4. else: exit 2 with a clear ConvError (never parent-count arithmetic)

`init` writes a `.conv-root` sentinel inside the resolved root.

Assumption: the test working dirs live under the system temp tree and the repo
checkout has no `conv/` dir or `.conv-root` above `scripts/`, so a "clean" run with no
marker genuinely resolves to nothing.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import clean_env, load_json, run_cli  # noqa: E402


class MarkerResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    # --- layer 4: fail loud, no guessing -------------------------------------

    def test_no_marker_no_override_errors(self) -> None:
        proc = run_cli(["init"], cwd=self.tmp)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(
            ("BRAIN_CONV" in proc.stderr) or ("conv-root" in proc.stderr),
            f"error should name the override options, got: {proc.stderr!r}",
        )

    def test_no_marker_does_not_create_arithmetic_store(self) -> None:
        # parents[4] of scripts/conv_cli.py — the old wrong default must NOT appear.
        script = Path(__file__).resolve().parent.parent / "scripts" / "conv_cli.py"
        bad_default = script.parents[4] / "conv"
        existed_before = bad_default.exists()
        run_cli(["init"], cwd=self.tmp)
        if not existed_before:
            self.assertFalse(
                bad_default.exists(),
                f"resolution must not fabricate a store at {bad_default}",
            )

    # --- layer 1/2: explicit overrides ---------------------------------------

    def test_explicit_conv_root_bootstraps_and_writes_sentinel(self) -> None:
        root = self.tmp / "conv"
        proc = run_cli(["init", "--conv-root", root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), root)
        self.assertTrue((root / "log").is_dir())
        self.assertTrue(
            (root / ".conv-root").is_file(),
            "init must write a .conv-root sentinel inside the resolved root",
        )

    def test_flag_beats_env(self) -> None:
        flag_root = self.tmp / "flagroot"
        env_root = self.tmp / "envroot"
        proc = run_cli(
            ["init", "--conv-root", flag_root],
            cwd=self.tmp,
            env=clean_env(BRAIN_CONV=env_root),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), flag_root)

    def test_env_beats_marker(self) -> None:
        (self.tmp / "conv").mkdir()  # a marker that would otherwise resolve here
        env_root = self.tmp / "envroot"
        proc = run_cli(["init"], cwd=self.tmp, env=clean_env(BRAIN_CONV=env_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), env_root)

    # --- layer 3: marker search, depth-independent ---------------------------

    def test_marker_via_conv_subdir_from_descendant_cwd(self) -> None:
        (self.tmp / "conv").mkdir()
        work = self.tmp / "a" / "b" / "work"
        work.mkdir(parents=True)
        proc = run_cli(["init"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), self.tmp / "conv")

    def test_marker_via_sentinel_resolves_to_that_dir(self) -> None:
        store = self.tmp / "store"
        store.mkdir()
        (store / ".conv-root").write_text("", encoding="utf-8")
        work = store / "nested" / "work"
        work.mkdir(parents=True)
        proc = run_cli(["init"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # a dir holding `.conv-root` IS the root (not <dir>/conv)
        self.assertEqual(Path(load_json(proc)["conv_root"]), store)

    def test_nearest_marker_wins(self) -> None:
        (self.tmp / "conv").mkdir()
        inner = self.tmp / "inner"
        inner_store = inner / "conv"
        inner_store.mkdir(parents=True)
        work = inner / "work"
        work.mkdir()
        proc = run_cli(["init"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # nearest ancestor (inner) wins over the farther one (tmp)
        self.assertEqual(Path(load_json(proc)["conv_root"]), inner_store)

    def test_bootstrapped_store_is_rediscoverable(self) -> None:
        root = self.tmp / "conv"
        self.assertEqual(run_cli(["init", "--conv-root", root], cwd=self.tmp).returncode, 0)
        work = self.tmp / "later" / "work"
        work.mkdir(parents=True)
        # a later bare command from elsewhere must find the bootstrapped store
        proc = run_cli(["list"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
