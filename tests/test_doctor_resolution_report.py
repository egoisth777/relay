"""Doctor reports runtime paths explicitly."""
from __future__ import annotations

import json
import os
import shutil
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


LEGACY_FIDELITY_RECORD = """+++
id = "conv_260102_legacy-fidelity"
topic = "legacy fidelity"
status = "active"
tags = []
refs = []
created = "2026-01-02T00:00:00Z"
updated = "2026-01-02T00:00:00Z"
+++
## summary
legacy summary

## dict
- **legacy** - meaning

## qa
- **Q:** q? **A:** a.

## resume
- goal: continue legacy work

## user-instructions
- retain compatibility

## condensed-transcript
(none)

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
        self.root = self.home / ".relay"
        self.addCleanup(self._tmp.cleanup)

    def env_with_tools(self, *names: str, **overrides: object) -> dict[str, str]:
        tools = self.tmp / "tools"
        tools.mkdir(exist_ok=True)
        extension = ".exe" if os.name == "nt" else ""
        for name in names:
            shim = tools / f"{name}{extension}"
            if os.name == "nt":
                shutil.copy2(sys.executable, shim)
            else:
                shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                shim.chmod(0o755)
        return clean_env(home=self.home, PATH=tools, **overrides)

    def test_reports_default_global_paths(self) -> None:
        proc = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), self.root)
        self.assertEqual(Path(out["relay_archive"]), self.root / "convs")
        self.assertEqual(out["conversation_database"], out["relay_archive"])
        self.assertEqual(out["resolution"]["layer"], "default-global")
        self.assertFalse(out["resolution"]["compatibility"])
        self.assertNotIn("conv_root", out)
        aliases = out["deprecated"]["aliases"]
        self.assertEqual(Path(aliases["conv_root"]), self.root)
        self.assertEqual(Path(aliases["convs"]), self.root / "convs")
        self.assertEqual(aliases["conversation_database"], out["relay_archive"])
        self.assertEqual(out["records"], 0)

    def test_semantic_search_reports_uvx_only_with_canonical_opt_in(self) -> None:
        enabled = run_cli(
            ["doctor"],
            cwd=self.tmp,
            env=self.env_with_tools("uvx", RELAY_USE_UVX_SEMBLE="1"),
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        out = load_json(enabled)
        self.assertFalse(out["tools"]["semble"])
        self.assertTrue(out["tools"]["uvx"])
        self.assertEqual(out["semantic_search"], "uvx semble (set RELAY_USE_UVX_SEMBLE=1)")

    def test_semantic_search_ignores_legacy_uvx_opt_in(self) -> None:
        ignored = run_cli(
            ["doctor"],
            cwd=self.tmp,
            env=self.env_with_tools("uvx", CONV_USE_UVX_SEMBLE="1"),
        )
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        out = load_json(ignored)
        self.assertFalse(out["tools"]["semble"])
        self.assertTrue(out["tools"]["uvx"])
        self.assertEqual(out["semantic_search"], "body fallback")

    def test_semantic_search_prefers_installed_semble(self) -> None:
        proc = run_cli(
            ["doctor"],
            cwd=self.tmp,
            env=self.env_with_tools("semble", "uvx", RELAY_USE_UVX_SEMBLE="1"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(load_json(proc)["semantic_search"], "semble")

    def test_reports_explicit_compatibility_root(self) -> None:
        root = self.tmp / "compat-root"
        proc = run_cli(["doctor", "--relay-root", root], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["relay_archive"]), root / "convs")
        self.assertEqual(out["conversation_database"], out["relay_archive"])
        self.assertEqual(out["resolution"]["layer"], "compat-flag")
        self.assertTrue(out["resolution"]["compatibility"])

    def test_reports_compatibility_root_when_flag_precedes_subcommand(self) -> None:
        root = self.tmp / "compat-root"
        proc = run_cli(["--relay-root", root, "doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), root)
        self.assertEqual(Path(out["relay_archive"]), root / "convs")
        self.assertEqual(out["conversation_database"], out["relay_archive"])
        self.assertEqual(out["resolution"]["layer"], "compat-flag")
        self.assertTrue(out["resolution"]["compatibility"])

    def test_reports_ignored_legacy_env_without_changing_default(self) -> None:
        env_root = self.tmp / "legacy-env-root"
        proc = run_cli(["doctor"], cwd=self.tmp, env=clean_env(home=self.home, RELAY_ROOT=env_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), self.root)
        self.assertEqual(out["resolution"]["ignored_legacy_env"], ["RELAY_ROOT"])
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

    def test_legacy_dict_counts_as_glossary_for_fidelity(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        path = self.root / "convs" / "2026-01-02_legacy-fidelity.md"
        path.write_text(LEGACY_FIDELITY_RECORD, encoding="utf-8")

        proc = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(out["parse_errors"], [])
        fidelity = [warning for warning in out["warnings"] if "fidelity" in warning]
        self.assertEqual(fidelity, [], out["warnings"])

    def test_doctor_warns_and_fix_preserves_conflicting_dict_and_glossary(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        path = self.root / "convs" / "2026-01-04_conflicting-sections.md"
        path.write_text(
            record_text(
                "conv_260104_conflicting-sections",
                "conflicting sections",
                """## summary
summary

## dict
- **old** - meaning

## glossary
- **new** - meaning

## qa
- **Q:** q? **A:** a.
""",
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()

        doctor = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        out = load_json(doctor)

        warnings = [
            warning
            for warning in out["warnings"]
            if path.name in json.dumps(warning)
            and warning.get("conflicting_sections") == ["dict", "glossary"]
        ]
        self.assertEqual(out["parse_errors"], [])
        self.assertEqual(out["records"], 1)
        self.assertFalse(
            any(path.name in json.dumps(warning) and "fidelity" in warning for warning in out["warnings"])
        )
        self.assertEqual(len(warnings), 1, out["warnings"])

        fixed = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        fixed_out = load_json(fixed)
        fixed_warnings = [
            warning
            for warning in fixed_out["warnings"]
            if path.name in json.dumps(warning)
            and warning.get("conflicting_sections") == ["dict", "glossary"]
        ]
        self.assertEqual(fixed_out["parse_errors"], [])
        self.assertEqual(fixed_out["records"], 1)
        self.assertFalse(
            any(path.name in json.dumps(warning) and "fidelity" in warning for warning in fixed_out["warnings"])
        )
        self.assertEqual(len(fixed_warnings), 1, fixed_out["warnings"])
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("convs/2026-01-04_conflicting-sections.md", fixed_out["fix"]["canonical_records"])

    def test_doctor_fix_coalesces_identical_dict_and_glossary(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        path = self.root / "convs" / "2026-01-05_identical-sections.md"
        path.write_text(
            record_text(
                "conv_260105_identical-sections",
                "identical sections",
                """## summary
summary

## dict
- **same** - meaning

## glossary
  - **same** - meaning

## qa
- **Q:** q? **A:** a.
""",
            ),
            encoding="utf-8",
        )

        fixed = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        out = load_json(fixed)
        self.assertIn("convs/2026-01-05_identical-sections.md", out["fix"]["canonical_records"])
        markdown = path.read_text(encoding="utf-8")
        self.assertNotIn("## dict", markdown)
        self.assertEqual(markdown.count("## glossary"), 1)

    def test_fix_canonicalizes_legacy_dict_to_glossary(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        path = self.root / "convs" / "2026-01-03_legacy-dict.md"
        path.write_text(LEGACY_RECORD, encoding="utf-8")

        proc = run_cli(["doctor", "--fix"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertIn("convs/2026-01-03_legacy-dict.md", out["fix"]["canonical_records"])

        markdown = path.read_text(encoding="utf-8")
        self.assertNotIn("## dict", markdown)
        self.assertIn("## glossary", markdown)
        headers = ["## summary", "## glossary", "## qa", "## resume", "## user-instructions", "## condensed-transcript"]
        positions = [markdown.index(header) for header in headers]
        self.assertEqual(positions, sorted(positions), markdown)

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

## glossary
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

## glossary
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
        self.assertEqual(
            [warning.get("installer_repair") for warning in out["warnings"]],
            ["unavailable"],
        )
        self.assertTrue(out["fix"]["gitignore"])
        self.assertGreaterEqual(out["fix"]["ref_changes"], 1)
        self.assertEqual(out["index_health"]["valid"], True)
        self.assertEqual(out["index_health"]["records"], 2)
        installer = out["fix"]["installer_repair"]
        self.assertFalse(installer["available"], installer)
        self.assertIn("Plugin installation root", installer["reason"])
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), ".semble/\nindex.jsonl\n__pycache__/\n")

        child_md = (self.root / "convs" / "2026-01-01_child.md").read_text(encoding="utf-8")
        positions = [child_md.index(name) for name in ("## summary", "## glossary", "## qa", "## resume", "## alpha", "## zebra")]
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
        self.assertFalse(again["fix"]["installer_repair"]["available"])

    def test_fix_does_not_canonicalize_duplicate_section_records(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        path = self.root / "convs" / "2026-01-02_duplicate.md"
        original = record_text(
            "conv_260102_duplicate",
            "duplicate sections",
            """## summary
first summary

## glossary
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

## glossary
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
            ("--help",): ("Plugin installation root", "Relay archive"),
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
