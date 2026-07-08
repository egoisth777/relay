"""End-to-end CLI smoke for the installed global Plugin root.

This drives the installed CLI under an isolated HOME so the test matches the
runtime path users run while keeping the real home directory untouched.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import clean_env, load_json  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL = REPO_ROOT / "scripts" / "install.py"


CID = "conv_260704_global_e2e_smoke"
BRANCH_ID = "conv_260704_global_e2e_sidekick"
CONTINUATION_ID = "conv_260704_global_e2e_continue"


def run_install(args, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALL), *map(str, args)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def run_installed_cli(
    root: Path,
    args,
    cwd: Path,
    env: dict[str, str],
    input: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "conv_cli.py"), *map(str, args)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        input=input,
    )


def conversation_payload(*, summary_marker: str, resume_goal: str, updated: str) -> str:
    return json.dumps(
        {
            "id": CID,
            "topic": "global e2e smoke updated",
            "status": "active",
            "tags": ["e2e", "global-root"],
            "created": "2026-07-04T00:00:00Z",
            "updated": updated,
            "resume": {
                "goal": resume_goal,
                "next_steps": ["List, search, show, and rebuild from the global database"],
                "open_questions": ["none"],
                "suggested_skills": ["conversate:resume"],
            },
            "user_instructions": [
                "Use isolated HOME and USERPROFILE",
                "Do not require cwd-local markers",
            ],
            "condensed_transcript": [
                {
                    "u": "Save the global-root smoke record.",
                    "a": "Saved under the Conversation database.",
                },
                {
                    "u": "Update it and keep it resumable.",
                    "a": "Updated the resume fields.",
                },
            ],
            "sections": {
                "summary": f"{summary_marker} summary from the installed CLI",
                "dict": "- **global-root** - default Plugin installation root",
                "qa": "- **Q (open):** Can this resume after rebuild? **A:** yes.",
                "decisions": "- Default commands use ~/.conversate/convs.",
            },
        }
    )


def plant_local_decoy(work: Path) -> None:
    local_db = work / ".conversate" / "convs"
    local_db.mkdir(parents=True)
    (work / ".conv-root").write_text("", encoding="utf-8")
    (work / "conv").mkdir()
    (local_db / "2026-07-04_local-decoy.md").write_text(
        """+++
id = "conv_260704_local_decoy"
topic = "local decoy must be ignored"
status = "active"
tags = []
refs = []
created = "2026-07-04T00:00:00Z"
updated = "2026-07-04T00:00:00Z"
+++
## summary
local decoy

## dict
- **x** - y

## qa
- **Q:** q? **A:** a.

## resume
(none)

## user-instructions
(none)

## condensed-transcript
(none)
""",
        encoding="utf-8",
    )


class InstalledGlobalRootE2ESmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.work = self.tmp / "workspace"
        self.work.mkdir()
        self.env = clean_env(home=self.home)
        self.root = self.home / ".conversate"
        self.db = self.root / "convs"
        self.addCleanup(self._tmp.cleanup)

        install = run_install(["--agents", "codex"], cwd=self.work, env=self.env)
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertTrue((self.root / "scripts" / "conv_cli.py").is_file())

    def cli(self, args, *, input: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return run_installed_cli(self.root, args, cwd or self.work, self.env, input=input)

    def test_installed_cli_round_trip_uses_global_conversation_database(self) -> None:
        plant_local_decoy(self.work)

        init = self.cli(["init"])
        self.assertEqual(init.returncode, 0, init.stderr)
        init_out = load_json(init)
        self.assertEqual(Path(init_out["plugin_installation_root"]), self.root)
        self.assertEqual(Path(init_out["conversation_database"]), self.db)
        self.assertTrue(self.db.is_dir())

        first = self.cli(
            ["upsert", "--stdin"],
            input=conversation_payload(
                summary_marker="initial-resume-marker",
                resume_goal="Resume the initial global smoke checkpoint",
                updated="2026-07-04T00:01:00Z",
            ),
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self.cli(
            ["upsert", "--stdin"],
            input=conversation_payload(
                summary_marker="updated-resume-marker",
                resume_goal="Resume the updated global smoke checkpoint",
                updated="2026-07-04T00:02:00Z",
            ),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        saved = load_json(second)
        self.assertEqual(saved["id"], CID)
        self.assertTrue(saved["file"].startswith("convs/"), saved["file"])
        self.assertTrue((self.root / saved["file"]).is_file())
        self.assertFalse((self.work / saved["file"]).exists())

        listed = self.cli(["list", "--json"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        records = load_json(listed)
        self.assertEqual([record["id"] for record in records], [CID])
        self.assertEqual(records[0]["file"], saved["file"])
        self.assertEqual(records[0]["open"], 1)

        search = self.cli(["search", "global e2e smoke updated"])
        self.assertEqual(search.returncode, 0, search.stderr)
        self.assertEqual(load_json(search)[0]["id"], CID)

        shown = self.cli(["show", CID])
        self.assertEqual(shown.returncode, 0, shown.stderr)
        shown_json = load_json(shown)
        self.assertEqual(shown_json["id"], CID)
        for text in (
            "updated-resume-marker",
            "Resume the updated global smoke checkpoint",
            "## resume",
            "## user-instructions",
            "## condensed-transcript",
        ):
            self.assertIn(text, shown_json["body"])

        doctor = self.cli(["doctor"])
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        doctor_out = load_json(doctor)
        self.assertEqual(Path(doctor_out["plugin_installation_root"]), self.root)
        self.assertEqual(Path(doctor_out["conversation_database"]), self.db)
        self.assertEqual(doctor_out["resolution"]["layer"], "default-global")
        self.assertEqual(doctor_out["records"], 1)
        self.assertEqual(doctor_out["parse_errors"], [])
        self.assertEqual(doctor_out["warnings"], [])

        (self.root / "index.jsonl").unlink()
        markerless_cwd = self.tmp / "markerless-cwd"
        markerless_cwd.mkdir()
        rebuilt = self.cli(["rebuild-index"], cwd=markerless_cwd)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(load_json(rebuilt)["records"], 1)

        listed_after_rebuild = self.cli(["list", "--json"], cwd=markerless_cwd)
        self.assertEqual(listed_after_rebuild.returncode, 0, listed_after_rebuild.stderr)
        self.assertEqual([record["id"] for record in load_json(listed_after_rebuild)], [CID])

        markdown = self.cli(["show", "updated global smoke", "--markdown"], cwd=markerless_cwd)
        self.assertEqual(markdown.returncode, 0, markdown.stderr)
        self.assertIn("## resume", markdown.stdout)
        self.assertIn("Resume the updated global smoke checkpoint", markdown.stdout)
        self.assertNotIn("local decoy must be ignored", markdown.stdout)

    def test_installed_branch_lifecycle_uses_global_conversation_database(self) -> None:
        plant_local_decoy(self.work)

        init = self.cli(["init"])
        self.assertEqual(init.returncode, 0, init.stderr)

        parent_write = self.cli(
            ["upsert", "--stdin"],
            input=conversation_payload(
                summary_marker="branch-parent-marker",
                resume_goal="Resume the installed branch lifecycle parent",
                updated="2026-07-04T00:03:00Z",
            ),
        )
        self.assertEqual(parent_write.returncode, 0, parent_write.stderr)

        sidekick = self.cli(
            ["sidekick", CID, "installed branch lifecycle", "--id", BRANCH_ID],
        )
        self.assertEqual(sidekick.returncode, 0, sidekick.stderr)
        sidekick_out = load_json(sidekick)
        self.assertEqual(sidekick_out["id"], BRANCH_ID)
        self.assertEqual(sidekick_out["parent"], CID)
        self.assertEqual(sidekick_out["parent_status"]["status"], "parked")

        branch = load_json(self.cli(["show", BRANCH_ID]))
        self.assertEqual(branch["status"], "active")
        self.assertIn({"id": CID, "rel": "spawned-from"}, branch["refs"])
        self.assertTrue((self.root / branch["file"]).is_file())
        self.assertFalse((self.work / branch["file"]).exists())

        returned = self.cli(
            ["return", BRANCH_ID, "--digest", "Installed branch returned with a deterministic digest."],
        )
        self.assertEqual(returned.returncode, 0, returned.stderr)
        returned_out = load_json(returned)
        self.assertEqual(returned_out["parent"], CID)
        self.assertTrue(returned_out["digest_changed"])

        closed_branch = load_json(self.cli(["show", BRANCH_ID]))
        self.assertEqual(closed_branch["status"], "closed")
        self.assertIn("## digest\nInstalled branch returned with a deterministic digest.", closed_branch["body"])

        continued = self.cli(
            [
                "continue",
                CID,
                "--topic",
                "installed continuation lifecycle",
                "--id",
                CONTINUATION_ID,
            ],
        )
        self.assertEqual(continued.returncode, 0, continued.stderr)
        continued_out = load_json(continued)
        self.assertEqual(continued_out["id"], CONTINUATION_ID)
        self.assertEqual(continued_out["parent"], CID)

        continuation = load_json(self.cli(["show", CONTINUATION_ID]))
        self.assertEqual(continuation["status"], "active")
        self.assertIn({"id": CID, "rel": "continued-from"}, continuation["refs"])
        self.assertTrue((self.root / continuation["file"]).is_file())
        self.assertFalse((self.work / continuation["file"]).exists())

        parent = load_json(self.cli(["show", CID]))
        self.assertEqual(parent["status"], "parked")
        self.assertIn({"id": BRANCH_ID, "rel": "spawned-to"}, parent["refs"])
        self.assertIn({"id": CONTINUATION_ID, "rel": "continued-as"}, parent["refs"])

        listed = self.cli(["list", "--json"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        statuses = {record["id"]: record["status"] for record in load_json(listed)}
        self.assertEqual(statuses[CID], "parked")
        self.assertEqual(statuses[BRANCH_ID], "closed")
        self.assertEqual(statuses[CONTINUATION_ID], "active")
        self.assertNotIn("conv_260704_local_decoy", statuses)

    def test_installed_doctor_fix_runs_bundled_installer_repair(self) -> None:
        self.assertTrue((self.root / "scripts" / "install.py").is_file())
        self.assertFalse((self.root / ".conversate-repair-source").exists())

        root_hook = self.root / "hooks" / "codex" / "conv_turn_counter.py"
        root_hook.write_text("STALE HOOK\n", encoding="utf-8")

        doctor = self.cli(["doctor", "--fix"])
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        out = load_json(doctor)
        installer = out["fix"]["installer_repair"]
        self.assertTrue(installer["available"], installer)
        self.assertEqual(installer["returncode"], 0, installer)
        self.assertEqual(Path(installer["command"][1]), self.root / "scripts" / "install.py")

        self.assertNotIn("STALE HOOK", root_hook.read_text(encoding="utf-8"))
        self.assertTrue((self.home / ".codex" / "hooks.json").is_file())

    def test_installed_doctor_fix_reports_missing_installer_artifacts(self) -> None:
        manifest = self.root / "conversate" / ".claude-plugin" / "plugin.json"
        self.assertTrue(manifest.is_file())
        manifest.unlink()

        doctor = self.cli(["doctor", "--fix"])
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        out = load_json(doctor)
        installer = out["fix"]["installer_repair"]
        self.assertTrue(installer["available"], installer)
        self.assertEqual(installer["returncode"], 2, installer)
        self.assertTrue(any("repair source is missing installer artifact" in line for line in installer["stderr"]))
        self.assertTrue(
            any(warning.get("installer_repair") == "failed" for warning in out["warnings"]),
            out["warnings"],
        )
        self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()
