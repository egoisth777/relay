"""CLI default layout.

Contract: the Plugin installation root defaults to `~/.relay/`, and the
Relay archive lives at `~/.relay/convs/`.
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
        root = self.home / ".relay"
        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["conversation_database"]), root / "convs")
        self.assertTrue((root / "convs").is_dir(), "records dir convs/ must exist")
        self.assertTrue((root / "index.jsonl").is_file(), "derived index must exist")
        self.assertFalse((self.tmp / ".relay").exists(), "cwd must not become the default runtime path")
        self.assertFalse((root / ".conv-root").exists(), "default layout must not create marker sentinel")
        self.assertTrue((root / ".semble").is_dir(), "semantic cache dir must exist")
        self.assertTrue((root / ".gitignore").is_file(), ".gitignore must exist")

    def test_init_gitignore_covers_derived_artifacts(self) -> None:
        run_cli(["init", "--relay-root", self.tmp / ".relay"], cwd=self.tmp)
        gitignore = (self.tmp / ".relay" / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".semble/", "index.jsonl", "__pycache__/"):
            self.assertIn(pattern, gitignore, f"{pattern!r} must be ignored")

    def test_init_with_explicit_compat_root_does_not_touch_global_default(self) -> None:
        compat_root = self.tmp / "compat-root"
        proc = run_cli(["init", "--relay-root", compat_root], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), compat_root)
        self.assertEqual(Path(out["conversation_database"]), compat_root / "convs")
        self.assertTrue((compat_root / "convs").is_dir())
        self.assertFalse((self.home / ".relay").exists())

    def test_init_output_uses_canonical_paths_and_deprecated_aliases(self) -> None:
        proc = run_cli(["init"], cwd=self.tmp, env=self.env)
        out = load_json(proc)
        root = self.home / ".relay"
        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["relay_archive"]), root / "convs")
        self.assertEqual(out["conversation_database"], out["relay_archive"])
        self.assertNotIn("conv_root", out)
        self.assertNotIn("convs", out)
        aliases = out["deprecated"]["aliases"]
        self.assertEqual(Path(aliases["conv_root"]), root)
        self.assertEqual(Path(aliases["convs"]), root / "convs")
        self.assertEqual(aliases["conversation_database"], out["relay_archive"])

    def test_records_are_written_under_convs(self) -> None:
        root = self.home / ".relay"
        payload = (
            '{"topic": "layout check", "sections": '
            '{"summary": "s", "glossary": "- **t** - m", "qa": "- **Q:** q? **A:** a."}}'
        )
        run_cli(["init"], cwd=self.tmp, env=self.env)
        proc = run_cli(["upsert", "--stdin"], cwd=self.tmp, env=self.env, input=payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rel = load_json(proc)["file"]
        self.assertTrue(rel.startswith("convs/"), rel)
        self.assertTrue((root / rel).is_file())

    def test_import_preserves_legacy_archive_and_never_overwrites_collisions(self) -> None:
        legacy_root = self.home / ".conversate"
        legacy_record = legacy_root / "convs" / "2026-01-01_legacy.md"
        legacy_record.parent.mkdir(parents=True)
        legacy_bytes = b'''+++\nid = "conv_260101_legacy"\ntopic = "legacy handoff"\nstatus = "active"\ntags = []\nrefs = []\ncreated = "2026-01-01T00:00:00Z"\nupdated = "2026-01-01T00:00:00Z"\n+++\n## summary\nlegacy\n\n## dict\n- **legacy** - preserved\n\n## qa\n- **Q:** imported? **A:** yes.\n\n## resume\n(none)\n\n## user-instructions\n(none)\n\n## condensed-transcript\n(none)\n'''
        legacy_record.write_bytes(legacy_bytes)

        imported = run_cli(["import", "--from", legacy_root], cwd=self.tmp, env=self.env)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        result = load_json(imported)
        self.assertEqual(result["copied"], ["2026-01-01_legacy.md"])
        self.assertEqual(result["collisions"], [])
        relay_record = self.home / ".relay" / "convs" / "2026-01-01_legacy.md"
        self.assertEqual(relay_record.read_bytes(), legacy_bytes)
        self.assertEqual(legacy_record.read_bytes(), legacy_bytes)

        relay_record.write_bytes(b"Relay record wins on collision\n")
        collided = run_cli(["import", "--from", legacy_root], cwd=self.tmp, env=self.env)
        self.assertEqual(collided.returncode, 0, collided.stderr)
        result = load_json(collided)
        self.assertEqual(result["copied"], [])
        self.assertEqual(result["collisions"], ["2026-01-01_legacy.md"])
        self.assertEqual(relay_record.read_bytes(), b"Relay record wins on collision\n")
        self.assertEqual(legacy_record.read_bytes(), legacy_bytes)

    def test_legacy_conv_root_flag_remains_an_explicit_compatibility_alias(self) -> None:
        legacy_root = self.tmp / "legacy-root"
        proc = run_cli(["init", "--conv-root", legacy_root], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = load_json(proc)
        self.assertEqual(Path(result["plugin_installation_root"]), legacy_root)
        self.assertFalse((self.home / ".relay").exists())


if __name__ == "__main__":
    unittest.main()
