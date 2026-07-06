"""CLI default layout.

Contract: the Plugin installation root defaults to `~/.conversate/`, and the
Conversation database lives at `~/.conversate/convs/`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import clean_env, load_json, run_cli  # noqa: E402


class StoreLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.env = clean_env(home=self.home)
        self.addCleanup(self._tmp.cleanup)

    def test_init_creates_global_plugin_root_layout(self) -> None:
        proc = run_cli(["init"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        root = self.home / ".conversate"
        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["conversation_database"]), root / "convs")
        self.assertTrue((root / "convs").is_dir(), "records dir convs/ must exist")
        self.assertTrue((root / "index.jsonl").is_file(), "derived index must exist")
        self.assertFalse((self.tmp / ".conversate").exists(), "cwd must not become the default runtime path")
        self.assertFalse((root / ".conv-root").exists(), "default layout must not create marker sentinel")
        self.assertTrue((root / ".semble").is_dir(), "semantic cache dir must exist")
        self.assertTrue((root / ".gitignore").is_file(), ".gitignore must exist")

    def test_init_gitignore_covers_derived_artifacts(self) -> None:
        run_cli(["init", "--conv-root", self.tmp / ".conversate"], cwd=self.tmp)
        gitignore = (self.tmp / ".conversate" / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".semble/", "index.jsonl", "__pycache__/"):
            self.assertIn(pattern, gitignore, f"{pattern!r} must be ignored")

    def test_init_with_explicit_compat_root_does_not_touch_global_default(self) -> None:
        compat_root = self.tmp / "compat-root"
        proc = run_cli(["init", "--conv-root", compat_root], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), compat_root)
        self.assertEqual(Path(out["conversation_database"]), compat_root / "convs")
        self.assertTrue((compat_root / "convs").is_dir())
        self.assertFalse((self.home / ".conversate").exists())

    def test_init_output_uses_canonical_paths_and_deprecated_aliases(self) -> None:
        proc = run_cli(["init"], cwd=self.tmp, env=self.env)
        out = load_json(proc)
        root = self.home / ".conversate"
        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["conversation_database"]), root / "convs")
        self.assertNotIn("conv_root", out)
        self.assertNotIn("convs", out)
        aliases = out["deprecated"]["aliases"]
        self.assertEqual(Path(aliases["conv_root"]), root)
        self.assertEqual(Path(aliases["convs"]), root / "convs")

    def test_records_are_written_under_convs(self) -> None:
        root = self.home / ".conversate"
        payload = (
            '{"topic": "layout check", "sections": '
            '{"summary": "s", "dict": "- **t** - m", "qa": "- **Q:** q? **A:** a."}}'
        )
        run_cli(["init"], cwd=self.tmp, env=self.env)
        proc = run_cli(["upsert", "--stdin"], cwd=self.tmp, env=self.env, input=payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rel = load_json(proc)["file"]
        self.assertTrue(rel.startswith("convs/"), rel)
        self.assertTrue((root / rel).is_file())


if __name__ == "__main__":
    unittest.main()
