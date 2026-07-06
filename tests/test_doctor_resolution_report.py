"""Doctor reports runtime paths explicitly."""
from __future__ import annotations

import json
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


def record_text(cid: str, topic: str, body: str, refs: str = "refs = []") -> str:
    return f"""+++
id = "{cid}"
topic = "{topic}"
status = "active"
tags = []
{refs}
created = "2026-01-01T00:00:00Z"
updated = "2026-01-01T00:00:00Z"
+++
{body}
"""


class DoctorResolutionReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.env = clean_env(home=self.home)
        self.root = self.home / ".conversate"
        self.addCleanup(self._tmp.cleanup)

    def test_reports_default_global_paths(self) -> None:
        proc = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), self.root)
        self.assertEqual(Path(out["conversation_database"]), self.root / "convs")
        self.assertEqual(out["resolution"]["layer"], "default-global")
        self.assertFalse(out["resolution"]["compatibility"])
        self.assertNotIn("conv_root", out)
        aliases = out["deprecated"]["aliases"]
        self.assertEqual(Path(aliases["conv_root"]), self.root)
        self.assertEqual(Path(aliases["convs"]), self.root / "convs")
        self.assertEqual(out["records"], 0)

    def test_reports_explicit_compatibility_root(self) -> None:
        root = self.tmp / "compat-root"
        proc = run_cli(["doctor", "--conv-root", root], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["conversation_database"]), root / "convs")
        self.assertEqual(out["resolution"]["layer"], "compat-flag")
        self.assertTrue(out["resolution"]["compatibility"])

    def test_reports_compatibility_root_when_flag_precedes_subcommand(self) -> None:
        root = self.tmp / "compat-root"
        proc = run_cli(["--conv-root", root, "doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["conversation_database"]), root / "convs")
        self.assertEqual(out["resolution"]["layer"], "compat-flag")
        self.assertTrue(out["resolution"]["compatibility"])

    def test_reports_ignored_legacy_env_without_changing_default(self) -> None:
        env_root = self.tmp / "legacy-env-root"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(home=self.home, CONVERSATE_ROOT=env_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), self.root)
        self.assertEqual(out["resolution"]["ignored_legacy_env"], ["CONVERSATE_ROOT"])
        self.assertFalse(env_root.exists())

    def test_warns_on_records_missing_resumption_sections(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        (self.root / "convs" / "2026-01-01_legacy.md").write_text(LEGACY_RECORD, encoding="utf-8")
        proc = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(out["parse_errors"], [], "a legacy record must still parse")
        warned = [w for w in out["warnings"] if "legacy" in w["file"]]
        self.assertEqual(len(warned), 1, out["warnings"])
        self.assertEqual(
            set(warned[0]["missing_sections"]),
            {"resume", "user-instructions", "condensed-transcript"},
        )

    def test_fix_repairs_lifecycle_artifacts_and_remains_idempotent(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        (self.root / ".gitignore").write_text("old-cache/\n", encoding="utf-8")
        (self.root / "index.jsonl").write_text("{not-json}\n", encoding="utf-8")
        parent = record_text(
            "conv_260101_parent",
            "parent",
            """## qa
- **Q:** q? **A:** a.

## summary
s

## dict
- **p** - parent
""",
        )
        child = record_text(
            "conv_260101_child",
            "child",
            """## zebra
z

## qa
- **Q:** q? **A:** a.

## summary
s

## alpha
a

## dict
- **c** - child
""",
            refs='refs = [{ id = "conv_260101_parent", rel = "spawned-from" }]',
        )
        (self.root / "convs" / "2026-01-01_parent.md").write_text(parent, encoding="utf-8")
        (self.root / "convs" / "2026-01-01_child.md").write_text(child, encoding="utf-8")
        (self.root / "convs" / "2026-01-01_bad.md").write_text("not frontmatter\n", encoding="utf-8")

        first = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(first.returncode, 0, first.stderr)
        out = load_json(first)
        self.assertEqual(len(out["parse_errors"]), 1)
        self.assertEqual(out["warnings"], [])
        self.assertTrue(out["fix"]["gitignore"])
        self.assertGreaterEqual(out["fix"]["ref_changes"], 1)
        self.assertEqual(out["index_health"]["valid"], True)
        self.assertEqual(out["index_health"]["records"], 2)
        installer = out["fix"]["installer_repair"]
        self.assertTrue(installer["available"], installer)
        self.assertEqual(installer["returncode"], 0, installer)
        self.assertIn("--doctor-fix", installer["command"])
        self.assertTrue((self.root / "scripts" / "conv_cli.py").is_file())
        self.assertTrue((self.home / ".codex" / "hooks.json").is_file())
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), ".semble/\nindex.jsonl\n__pycache__/\n")

        child_md = (self.root / "convs" / "2026-01-01_child.md").read_text(encoding="utf-8")
        positions = [child_md.index(name) for name in ("## summary", "## dict", "## qa", "## resume", "## alpha", "## zebra")]
        self.assertEqual(positions, sorted(positions), child_md)
        parent_md = (self.root / "convs" / "2026-01-01_parent.md").read_text(encoding="utf-8")
        self.assertIn('rel = "spawned-to"', parent_md)

        second = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(second.returncode, 0, second.stderr)
        again = load_json(second)
        self.assertEqual(len(again["parse_errors"]), 1)
        self.assertFalse(again["fix"]["gitignore"])
        self.assertEqual(again["fix"]["canonical_records"], [])
        self.assertEqual(again["fix"]["ref_changes"], 0)
        self.assertEqual(again["fix"]["installer_repair"]["returncode"], 0)

    def test_fix_does_not_canonicalize_duplicate_section_records(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        path = self.root / "convs" / "2026-01-02_duplicate.md"
        original = record_text(
            "conv_260102_duplicate",
            "duplicate sections",
            """## summary
first summary

## dict
- **x** - y

## summary
second summary

## qa
- **Q:** q? **A:** a.
""",
        )
        path.write_text(original, encoding="utf-8")

        proc = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertNotIn("convs/2026-01-02_duplicate.md", out["fix"]["canonical_records"])
        duplicate_warnings = [w for w in out["warnings"] if w.get("duplicate_sections")]
        self.assertEqual(len(duplicate_warnings), 1, out["warnings"])
        self.assertEqual(duplicate_warnings[0]["duplicate_sections"], ["summary"])
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_fix_rebuilds_valid_but_stale_index_rows(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        (self.root / "convs" / "2026-01-03_valid.md").write_text(
            record_text(
                "conv_260103_valid",
                "fresh topic from database",
                """## summary
fresh summary

## dict
- **x** - y

## qa
- **Q:** q? **A:** a.
""",
            ),
            encoding="utf-8",
        )
        stale = {
            "id": "conv_260103_valid",
            "topic": "stale index topic",
            "status": "closed",
            "tags": [],
            "refs": [],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
            "file": "convs/2026-01-03_valid.md",
            "open": 99,
        }
        (self.root / "index.jsonl").write_text(f"{json.dumps(stale)}\n", encoding="utf-8")

        proc = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertTrue(out["index_health"]["valid"])
        self.assertEqual(out["fix"]["index_records"], 1)
        rebuilt = [json.loads(line) for line in (self.root / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rebuilt[0]["topic"], "fresh topic from database")
        self.assertEqual(rebuilt[0]["status"], "active")
        self.assertEqual(rebuilt[0]["open"], 0)

    def test_core_cli_help_uses_current_root_terms(self) -> None:
        expectations = {
            ("--help",): ("Plugin installation root", "Conversation database"),
            ("init", "--help"): ("Plugin installation root",),
            ("list", "--help"): ("Plugin installation root",),
            ("search", "--help"): ("Plugin installation root",),
            ("show", "--help"): ("Plugin installation root",),
            ("doctor", "--help"): ("Plugin installation root",),
        }
        forbidden = ("payload", "bundle", "data root", "conversation store", "active store")

        for args, required in expectations.items():
            with self.subTest(args=args):
                proc = run_cli(list(args), cwd=self.tmp, env=self.env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                normalized = " ".join(proc.stdout.split())
                for term in required:
                    self.assertIn(term, normalized)
                lowered = normalized.lower()
                for term in forbidden:
                    self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
