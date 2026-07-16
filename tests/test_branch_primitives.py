"""Focused CLI coverage for deterministic branch primitives."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import load_json, run_cli  # noqa: E402


PARENT_PAYLOAD = {
    "id": "conv_260101_parent",
    "topic": "parent topic",
    "status": "active",
    "tags": ["branch"],
    "sections": {
        "summary": "parent summary",
        "dict": "- **parent** - meaning",
        "qa": "- **Q:** settled? **A:** yes.",
        "sources": "- parent design doc\n- parent issue tracker",
        "insights": "- useful pattern",
        "decisions": "- keep the interface small",
    },
    "resume": {"goal": "finish parent"},
    "user_instructions": ["keep it deterministic"],
}


def existing_child_payload(cid: str, summary: str) -> dict:
    return {
        "id": cid,
        "topic": "existing child",
        "status": "active",
        "sections": {
            "summary": summary,
            "dict": "- **child** - existing",
            "qa": "- **Q:** keep? **A:** yes.",
        },
    }


class BranchPrimitivesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        self.root = self.tmp / ".relay"
        self.assertEqual(run_cli(["init", "--relay-root", self.root], cwd=self.tmp).returncode, 0)
        proc = run_cli(
            ["upsert", "--stdin", "--relay-root", self.root],
            cwd=self.tmp,
            input=json.dumps(PARENT_PAYLOAD),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def _show(self, cid: str) -> dict:
        proc = run_cli(["show", cid, "--relay-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return load_json(proc)

    def _markdown(self, cid: str) -> str:
        proc = run_cli(["show", cid, "--markdown", "--relay-root", self.root], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def _upsert(self, payload: dict) -> None:
        proc = run_cli(
            ["upsert", "--stdin", "--relay-root", self.root],
            cwd=self.tmp,
            input=json.dumps(payload),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_sidekick_parks_parent_and_links_refs(self) -> None:
        proc = run_cli(
            [
                "sidekick",
                "conv_260101_parent",
                "branch topic",
                "--id",
                "conv_260101_branch",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(out["id"], "conv_260101_branch")
        self.assertEqual(self._show("conv_260101_parent")["status"], "parked")
        branch = self._show("conv_260101_branch")
        self.assertEqual(branch["status"], "active")
        self.assertIn({"id": "conv_260101_parent", "rel": "spawned-from"}, branch["refs"])
        self.assertIn({"id": "conv_260101_branch", "rel": "spawned-to"}, self._show("conv_260101_parent")["refs"])

    def test_sidekick_keep_parent_active_keeps_parent_active_and_links_refs(self) -> None:
        proc = run_cli(
            [
                "sidekick",
                "conv_260101_parent",
                "branch topic",
                "--id",
                "conv_260101_branch",
                "--keep-parent-active",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parent = self._show("conv_260101_parent")
        self.assertEqual(parent["status"], "active")
        self.assertIn({"id": "conv_260101_branch", "rel": "spawned-to"}, parent["refs"])

    def test_sidekick_id_collision_preserves_existing_child_and_parent(self) -> None:
        self._upsert(existing_child_payload("conv_260101_branch", "existing sidekick record"))
        proc = run_cli(
            [
                "sidekick",
                "conv_260101_parent",
                "branch topic",
                "--id",
                "conv_260101_branch",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("already exists", proc.stderr)

        parent = self._show("conv_260101_parent")
        self.assertEqual(parent["status"], "active")
        self.assertNotIn({"id": "conv_260101_branch", "rel": "spawned-to"}, parent["refs"])
        child_md = self._markdown("conv_260101_branch")
        self.assertIn("existing sidekick record", child_md)
        self.assertNotIn("Sidekick of conv_260101_parent", child_md)

    def test_continue_parks_parent_and_links_refs(self) -> None:
        proc = run_cli(
            [
                "continue",
                "conv_260101_parent",
                "--topic",
                "fresh parent topic",
                "--id",
                "conv_260101_continued",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        child = self._show("conv_260101_continued")
        self.assertEqual(child["status"], "active")
        self.assertIn({"id": "conv_260101_parent", "rel": "continued-from"}, child["refs"])
        parent = self._show("conv_260101_parent")
        self.assertEqual(parent["status"], "parked")
        self.assertIn({"id": "conv_260101_continued", "rel": "continued-as"}, parent["refs"])

    def test_continue_carries_parent_sources(self) -> None:
        proc = run_cli(
            [
                "continue",
                "conv_260101_parent",
                "--topic",
                "fresh parent topic",
                "--id",
                "conv_260101_continued",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        md = self._markdown("conv_260101_continued")
        self.assertIn("## sources", md)
        self.assertIn("- continued-from: conv_260101_parent", md)
        self.assertIn("- parent design doc", md)
        self.assertIn("- parent issue tracker", md)

    def test_continue_id_collision_preserves_existing_child_and_parent(self) -> None:
        self._upsert(existing_child_payload("conv_260101_continued", "existing continuation record"))
        proc = run_cli(
            [
                "continue",
                "conv_260101_parent",
                "--topic",
                "fresh parent topic",
                "--id",
                "conv_260101_continued",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("already exists", proc.stderr)

        parent = self._show("conv_260101_parent")
        self.assertEqual(parent["status"], "active")
        self.assertNotIn({"id": "conv_260101_continued", "rel": "continued-as"}, parent["refs"])
        child_md = self._markdown("conv_260101_continued")
        self.assertIn("existing continuation record", child_md)
        self.assertNotIn("Continuation of conv_260101_parent", child_md)

    def test_return_closes_branch_and_writes_digest(self) -> None:
        created = run_cli(
            [
                "sidekick",
                "conv_260101_parent",
                "branch topic",
                "--id",
                "conv_260101_branch",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        proc = run_cli(
            [
                "return",
                "conv_260101_branch",
                "--digest",
                "Explored the branch and found the answer.",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        self.assertEqual(out["parent"], "conv_260101_parent")
        self.assertTrue(out["digest_changed"])
        branch = self._show("conv_260101_branch")
        self.assertEqual(branch["status"], "closed")
        md = self._markdown("conv_260101_branch")
        self.assertIn("## digest\nExplored the branch and found the answer.", md)

    def test_return_rejects_explicit_unrelated_parent_without_closing_branch(self) -> None:
        created = run_cli(
            [
                "sidekick",
                "conv_260101_parent",
                "branch topic",
                "--id",
                "conv_260101_branch",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        self._upsert(
            {
                "id": "conv_260101_unrelated-parent",
                "topic": "unrelated parent",
                "status": "active",
                "sections": {
                    "summary": "unrelated",
                    "dict": "- **other** - parent",
                    "qa": "- **Q:** related? **A:** no.",
                },
            }
        )

        proc = run_cli(
            [
                "return",
                "conv_260101_branch",
                "--digest",
                "This must not be written.",
                "--parent",
                "conv_260101_unrelated-parent",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("not a branch parent", proc.stderr)
        branch = self._show("conv_260101_branch")
        self.assertEqual(branch["status"], "active")
        self.assertNotIn("This must not be written.", self._markdown("conv_260101_branch"))

    def test_return_rejects_ambiguous_branch_parents_without_closing_branch(self) -> None:
        self._upsert(
            {
                "id": "conv_260101_second-parent",
                "topic": "second parent",
                "status": "active",
                "sections": {
                    "summary": "second parent",
                    "dict": "- **other** - parent",
                    "qa": "- **Q:** related? **A:** yes.",
                },
            }
        )
        self._upsert(
            {
                "id": "conv_260101_ambiguous-branch",
                "topic": "ambiguous branch",
                "status": "active",
                "refs": [
                    {"id": "conv_260101_parent", "rel": "spawned-from"},
                    {"id": "conv_260101_second-parent", "rel": "continued-from"},
                ],
                "sections": {
                    "summary": "branch summary",
                    "dict": "- **branch** - ambiguous",
                    "qa": "- **Q:** close it? **A:** no.",
                },
            }
        )
        before = self._show("conv_260101_ambiguous-branch")

        proc = run_cli(
            [
                "return",
                "conv_260101_ambiguous-branch",
                "--digest",
                "This ambiguous return must not be written.",
                "--relay-root",
                self.root,
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("multiple branch parent refs", proc.stderr)

        after = self._show("conv_260101_ambiguous-branch")
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["refs"], before["refs"])
        self.assertEqual(after["updated"], before["updated"])
        self.assertNotIn(
            "This ambiguous return must not be written.",
            self._markdown("conv_260101_ambiguous-branch"),
        )


if __name__ == "__main__":
    unittest.main()
