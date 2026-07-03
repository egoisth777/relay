"""Slice s-record-schema / ticket t-resumption-sections.

Contract: every record is a resumption point. Beyond the mandatory summary/dict/qa, the
body ALWAYS carries `## resume`, `## user-instructions`, and `## condensed-transcript`,
rendered from structured upsert payload keys and placed in a fixed order. Empty fields
render as `(none)` rather than hard-failing upsert. Legacy records that predate these
sections are tolerated by rebuild-index/show (no crash).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import load_json, run_cli  # noqa: E402

FULL_PAYLOAD = {
    "topic": "resumption schema",
    "status": "active",
    "tags": ["schema"],
    "sections": {
        "summary": "one line summary",
        "dict": "- **term** - meaning",
        "qa": "- **Q:** q? **A:** a.",
        "decisions": "1. a settled decision",
    },
    "resume": {
        "goal": "finish the redesign",
        "next_steps": ["write tests", "update docs"],
        "open_questions": ["adapter details?"],
        "suggested_skills": ["conv:save"],
    },
    "user_instructions": ["use PowerShell", "never commit"],
    "condensed_transcript": [
        {"u": "do the thing", "a": "did the thing (see scripts/conv_cli.py)"},
        "note: referenced a commit by hash",
    ],
}

LEGACY_RECORD = """+++
id = "conv_250101_old"
topic = "old record"
status = "parked"
tags = []
refs = []
created = "2025-01-01T00:00:00Z"
updated = "2025-01-01T00:00:00Z"
+++
## summary
predates resumption sections

## dict
- **x** - y

## qa
- **Q:** q? **A:** a.
"""


def _order(haystack: str, *needles: str) -> list[int]:
    return [haystack.index(n) for n in needles]


class RecordSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        self.root = self.tmp / ".conversate"
        self.assertEqual(run_cli(["init", "--conv-root", self.root], cwd=self.tmp).returncode, 0)

    def _upsert(self, payload: dict) -> str:
        proc = run_cli(
            ["upsert", "--stdin", "--conv-root", self.root],
            cwd=self.tmp,
            input=json.dumps(payload),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return load_json(proc)["id"]

    def _markdown(self, cid: str) -> str:
        proc = run_cli(["show", cid, "--markdown", "--conv-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_full_payload_renders_all_sections_in_order(self) -> None:
        md = self._markdown(self._upsert(FULL_PAYLOAD))
        positions = _order(
            md,
            "## summary",
            "## dict",
            "## qa",
            "## resume",
            "## user-instructions",
            "## condensed-transcript",
            "## decisions",
        )
        self.assertEqual(positions, sorted(positions), f"sections out of order:\n{md}")

    def test_resume_structure_is_rendered(self) -> None:
        md = self._markdown(self._upsert(FULL_PAYLOAD))
        self.assertIn("- goal: finish the redesign", md)
        self.assertIn("- next-steps:", md)
        self.assertIn("  - write tests", md)
        self.assertIn("- open-questions:", md)
        self.assertIn("- suggested-skills:", md)
        self.assertIn("  - conv:save", md)

    def test_user_instructions_and_transcript_rendered(self) -> None:
        md = self._markdown(self._upsert(FULL_PAYLOAD))
        self.assertIn("- use PowerShell", md)
        self.assertIn("- never commit", md)
        self.assertIn("- U: do the thing", md)
        self.assertIn("- A: did the thing", md)
        self.assertIn("- note: referenced a commit by hash", md)

    def test_missing_new_fields_render_none_placeholders(self) -> None:
        cid = self._upsert(
            {
                "topic": "bare record",
                "sections": {
                    "summary": "s",
                    "dict": "- **t** - m",
                    "qa": "- **Q:** q? **A:** a.",
                },
            }
        )
        md = self._markdown(cid)
        for header in ("## resume", "## user-instructions", "## condensed-transcript"):
            self.assertIn(header, md, f"{header} must always appear")
        # each of the three empty sections renders the placeholder
        self.assertEqual(md.count("(none)"), 3, md)

    def test_rebuild_index_tolerates_legacy_record(self) -> None:
        (self.root / "convs" / "2025-01-01_old.md").write_text(LEGACY_RECORD, encoding="utf-8")
        proc = run_cli(["rebuild-index", "--conv-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(load_json(proc)["records"], 1)

    def test_show_tolerates_legacy_record(self) -> None:
        (self.root / "convs" / "2025-01-01_old.md").write_text(LEGACY_RECORD, encoding="utf-8")
        run_cli(["rebuild-index", "--conv-root", self.root], cwd=self.tmp)
        proc = run_cli(["show", "conv_250101_old", "--conv-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(load_json(proc)["id"], "conv_250101_old")


if __name__ == "__main__":
    unittest.main()
