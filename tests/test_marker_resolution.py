"""Slice s-store-root-resolution / ticket t-marker-resolution.

Contract under test (conv_root resolution order):
  1. --conv-root flag
  2. $CONVERSATE_ROOT env
  3. $BRAIN_CONV env (legacy)
  4. marker search from cwd, then the script dir, nearest ancestor first:
       - a dir *named* `.conversate`                  -> that dir IS the root
       - a dir holding a `.conv-root` sentinel file    -> that dir IS the root
       - a dir holding a `.conversate/` subdirectory   -> `<dir>/.conversate`
       - (legacy) a dir holding a `conv/` subdirectory -> `<dir>/conv`
     The walk stops at a `.git` boundary but still checks the repo-root dir itself.
  5. else: read commands exit 2 with a clear ConvError (never path arithmetic);
     `init` is the sole exception -- it bootstraps `<cwd>/.conversate`.

`init` writes a `.conv-root` sentinel inside the resolved root.

Assumption: the test working dirs live under the system temp tree and the repo
checkout has no `.conversate`/`conv/`/`.conv-root` marker above `scripts/`, so a "clean"
run with no marker genuinely resolves to nothing.
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

    # --- fail-loud, no guessing (read commands) ------------------------------

    def test_no_marker_no_override_errors(self) -> None:
        # read commands still fail loud when nothing resolves; `init` is the exception.
        proc = run_cli(["list"], cwd=self.tmp)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(
            any(name in proc.stderr for name in ("BRAIN_CONV", "CONVERSATE_ROOT", "conv-root")),
            f"error should name the override options, got: {proc.stderr!r}",
        )

    def test_no_marker_does_not_create_arithmetic_store(self) -> None:
        # parents[4] of scripts/conv_cli.py — the old wrong default must NOT appear.
        script = Path(__file__).resolve().parent.parent / "scripts" / "conv_cli.py"
        bad_default = script.parents[4] / "conv"
        existed_before = bad_default.exists()
        run_cli(["list"], cwd=self.tmp)
        if not existed_before:
            self.assertFalse(
                bad_default.exists(),
                f"resolution must not fabricate a store at {bad_default}",
            )

    def test_init_with_no_marker_bootstraps_dot_conversate(self) -> None:
        # init is the sole command that creates a store from nothing: <cwd>/.conversate.
        proc = run_cli(["init"], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), self.tmp / ".conversate")
        self.assertTrue((self.tmp / ".conversate" / "convs").is_dir())

    # --- explicit overrides --------------------------------------------------

    def test_explicit_conv_root_bootstraps_and_writes_sentinel(self) -> None:
        root = self.tmp / ".conversate"
        proc = run_cli(["init", "--conv-root", root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), root)
        self.assertTrue((root / "convs").is_dir())
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
            env=clean_env(CONVERSATE_ROOT=env_root),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), flag_root)

    def test_conversate_root_beats_brain_conv(self) -> None:
        cr = self.tmp / "cr"
        bc = self.tmp / "bc"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(CONVERSATE_ROOT=cr, BRAIN_CONV=bc))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), cr)
        self.assertEqual(out["resolution"]["layer"], "env-conversate")

    def test_conversate_root_beats_marker(self) -> None:
        (self.tmp / ".conversate").mkdir()  # a marker that would otherwise resolve here
        cr = self.tmp / "elsewhere"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(CONVERSATE_ROOT=cr))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), cr)

    def test_brain_conv_still_works_and_beats_marker(self) -> None:
        (self.tmp / "conv").mkdir()  # legacy marker that would otherwise resolve here
        env_root = self.tmp / "envroot"
        proc = run_cli(["init"], cwd=self.tmp, env=clean_env(BRAIN_CONV=env_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), env_root)

    # --- marker search: .conversate ------------------------------------------

    def test_conversate_dirname_marker_from_inside(self) -> None:
        store = self.tmp / ".conversate"
        store.mkdir()
        proc = run_cli(["doctor"], cwd=store)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), store)
        self.assertEqual(out["resolution"]["layer"], "marker")

    def test_conversate_subdir_from_project_root(self) -> None:
        (self.tmp / ".conversate").mkdir()
        proc = run_cli(["doctor"], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), self.tmp / ".conversate")

    def test_conversate_marker_from_nested_subdir(self) -> None:
        (self.tmp / ".conversate").mkdir()
        work = self.tmp / "src" / "pkg" / "deep"
        work.mkdir(parents=True)
        proc = run_cli(["doctor"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), self.tmp / ".conversate")

    def test_conversate_wins_over_legacy_conv_in_same_dir(self) -> None:
        (self.tmp / ".conversate").mkdir()
        (self.tmp / "conv").mkdir()
        proc = run_cli(["doctor"], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(load_json(proc)["conv_root"]), self.tmp / ".conversate")

    # --- marker search: legacy shapes still resolve --------------------------

    def test_legacy_conv_subdir_from_descendant_cwd(self) -> None:
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
        # a dir holding `.conv-root` IS the root (not <dir>/.conversate)
        self.assertEqual(Path(load_json(proc)["conv_root"]), store)

    def test_nearest_marker_wins(self) -> None:
        (self.tmp / ".conversate").mkdir()
        inner = self.tmp / "inner"
        inner_store = inner / ".conversate"
        inner_store.mkdir(parents=True)
        work = inner / "work"
        work.mkdir()
        proc = run_cli(["doctor"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # nearest ancestor (inner) wins over the farther one (tmp)
        self.assertEqual(Path(load_json(proc)["conv_root"]), inner_store)

    def test_bootstrapped_store_is_rediscoverable(self) -> None:
        root = self.tmp / ".conversate"
        self.assertEqual(run_cli(["init", "--conv-root", root], cwd=self.tmp).returncode, 0)
        work = self.tmp / "later" / "work"
        work.mkdir(parents=True)
        # a later bare command from elsewhere must find the bootstrapped store
        proc = run_cli(["list"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # --- .git boundary -------------------------------------------------------

    def test_git_boundary_includes_repo_root(self) -> None:
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".conversate").mkdir()  # sits next to .git at the repo root
        work = repo / "src" / "pkg"
        work.mkdir(parents=True)
        proc = run_cli(["doctor"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(Path(out["conv_root"]), repo / ".conversate")
        self.assertEqual(out["resolution"]["layer"], "marker")

    def test_marker_above_git_boundary_is_not_used(self) -> None:
        (self.tmp / ".conversate").mkdir()  # above the repo boundary
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        work = repo / "work"
        work.mkdir(parents=True)
        proc = run_cli(["doctor"], cwd=work)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # the ancestor .conversate is beyond the .git boundary, so it is not resolved
        self.assertEqual(load_json(proc)["resolution"]["layer"], "none")


if __name__ == "__main__":
    unittest.main()
