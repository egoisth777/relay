"""Slice s-record-schema / ticket t-resumption-sections.

Contract: every record is a resumption point. Beyond the mandatory summary/glossary/qa, the
body ALWAYS carries `## resume`, `## user-instructions`, and `## condensed-transcript`,
rendered from structured upsert input keys and placed in a fixed order. Empty fields
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
        "glossary": "- **term** - meaning",
        "qa": "- **Q:** q? **A:** a.",
        "sources": "- source note",
        "insights": "- useful signal",
        "decisions": "1. a settled decision",
        "digest": "final digest",
    },
    "resume": {
        "goal": "finish the redesign",
        "next_steps": ["write tests", "update docs"],
        "open_questions": ["adapter details?"],
        "suggested_skills": ["relay:save"],
    },
    "user_instructions": ["use PowerShell", "never commit"],
    "condensed_transcript": [
        {"u": "do the thing", "a": "did the thing (see relay help)"},
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
        self.root = self.tmp / ".relay"
        self.assertEqual(run_cli(["init", "--relay-root", self.root], cwd=self.tmp).returncode, 0)

    def _upsert(self, payload: dict) -> str:
        proc = run_cli(
            ["upsert", "--stdin", "--relay-root", self.root],
            cwd=self.tmp,
            input=json.dumps(payload),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return load_json(proc)["id"]

    def _markdown(self, cid: str) -> str:
        proc = run_cli(["show", cid, "--markdown", "--relay-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_full_payload_renders_all_sections_in_order(self) -> None:
        md = self._markdown(self._upsert(FULL_PAYLOAD))
        positions = _order(
            md,
            "## summary",
            "## glossary",
            "## qa",
            "## sources",
            "## insights",
            "## decisions",
            "## digest",
            "## resume",
            "## user-instructions",
            "## condensed-transcript",
        )
        self.assertEqual(positions, sorted(positions), f"sections out of order:\n{md}")

    def test_structured_unknown_sections_render_last_alphabetically(self) -> None:
        payload = {
            "topic": "unknown section order",
            "sections": {
                "summary": "s",
                "glossary": "- **t** - m",
                "qa": "- **Q:** q? **A:** a.",
                "zebra": "z",
                "alpha": "a",
            },
        }
        md = self._markdown(self._upsert(payload))
        positions = _order(
            md,
            "## summary",
            "## glossary",
            "## qa",
            "## resume",
            "## user-instructions",
            "## condensed-transcript",
            "## alpha",
            "## zebra",
        )
        self.assertEqual(positions, sorted(positions), f"sections out of order:\n{md}")

    def test_raw_body_is_rewritten_to_canonical_section_order(self) -> None:
        cid = self._upsert(
            {
                "topic": "raw body order",
                "body": """## zebra
z

## qa
- **Q:** q? **A:** a.

## summary
s

## alpha
a

## decisions
d

## glossary
- **t** - m
""",
            }
        )
        md = self._markdown(cid)
        positions = _order(
            md,
            "## summary",
            "## glossary",
            "## qa",
            "## decisions",
            "## resume",
            "## user-instructions",
            "## condensed-transcript",
            "## alpha",
            "## zebra",
        )
        self.assertEqual(positions, sorted(positions), f"sections out of order:\n{md}")

    def test_structured_legacy_dict_key_renders_canonical_glossary(self) -> None:
        payload = {
            "topic": "legacy structured glossary",
            "sections": {
                "summary": "s",
                "dict": "- **legacy** - accepted",
                "qa": "- **Q:** q? **A:** a.",
            },
        }
        md = self._markdown(self._upsert(payload))
        self.assertIn("## glossary", md)
        self.assertNotIn("## dict", md)

    def test_raw_legacy_dict_heading_renders_canonical_glossary(self) -> None:
        cid = self._upsert(
            {
                "topic": "legacy raw glossary",
                "body": """## summary
s

## dict
- **legacy** - accepted

## qa
- **Q:** q? **A:** a.
""",
            }
        )
        md = self._markdown(cid)
        self.assertIn("## glossary", md)
        self.assertNotIn("## dict", md)

    def test_identical_dict_and_glossary_coalesce_to_one_canonical_section(self) -> None:
        cid = self._upsert(
            {
                "topic": "coalesced glossary",
                "sections": {
                    "summary": "s",
                    "dict": "  - **same** - content  \n",
                    "glossary": "- **same** - content",
                    "qa": "- **Q:** q? **A:** a.",
                },
            }
        )
        md = self._markdown(cid)
        self.assertEqual(md.count("## glossary"), 1, md)
        self.assertNotIn("## dict", md)

    def test_conflicting_dict_and_glossary_fail_like_missing_mandatory(self) -> None:
        missing = run_cli(
            ["upsert", "--stdin", "--relay-root", self.root],
            cwd=self.tmp,
            input=json.dumps(
                {
                    "topic": "missing glossary",
                    "sections": {
                        "summary": "s",
                        "qa": "- **Q:** q? **A:** a.",
                    },
                }
            ),
        )
        self.assertNotEqual(missing.returncode, 0, missing.stdout)

        conflict = run_cli(
            ["upsert", "--stdin", "--relay-root", self.root],
            cwd=self.tmp,
            input=json.dumps(
                {
                    "topic": "conflicting glossary",
                    "sections": {
                        "summary": "s",
                        "dict": "- **one** - content",
                        "glossary": "- **two** - content",
                        "qa": "- **Q:** q? **A:** a.",
                    },
                }
            ),
        )
        self.assertEqual(conflict.returncode, missing.returncode, conflict.stdout)
        message = f"{conflict.stdout}\n{conflict.stderr}".lower()
        self.assertIn("conflicting", message)
        self.assertIn("dict", message)
        self.assertIn("glossary", message)

    def test_raw_body_rejects_duplicate_sections_without_replacing_existing_record(self) -> None:
        cid = self._upsert(
            {
                "id": "conv_260101_duplicate-raw",
                "topic": "duplicate raw",
                "sections": {
                    "summary": "original summary",
                    "glossary": "- **t** - m",
                    "qa": "- **Q:** q? **A:** a.",
                },
            }
        )
        before = self._markdown(cid)

        proc = run_cli(
            ["upsert", "--stdin", "--relay-root", self.root],
            cwd=self.tmp,
            input=json.dumps(
                {
                    "id": cid,
                    "topic": "duplicate raw",
                    "body": """## summary
new summary

## glossary
- **t** - m

## summary
second summary must not replace data

## qa
- **Q:** q? **A:** a.
""",
                }
            ),
        )

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("duplicate section", proc.stderr)
        self.assertIn("summary", proc.stderr)
        self.assertEqual(self._markdown(cid), before)

    def test_resume_structure_is_rendered(self) -> None:
        md = self._markdown(self._upsert(FULL_PAYLOAD))
        self.assertIn("- goal: finish the redesign", md)
        self.assertIn("- next-steps:", md)
        self.assertIn("  - write tests", md)
        self.assertIn("- open-questions:", md)
        self.assertIn("- suggested-skills:", md)
        self.assertIn("  - relay:save", md)

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
                    "glossary": "- **t** - m",
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
        proc = run_cli(["rebuild-index", "--relay-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(load_json(proc)["records"], 1)

    def test_show_tolerates_legacy_record(self) -> None:
        (self.root / "convs" / "2025-01-01_old.md").write_text(LEGACY_RECORD, encoding="utf-8")
        run_cli(["rebuild-index", "--relay-root", self.root], cwd=self.tmp)
        proc = run_cli(["show", "conv_250101_old", "--relay-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(load_json(proc)["id"], "conv_250101_old")


if __name__ == "__main__":
    unittest.main()
