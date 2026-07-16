"""Hidden-style CLI edge cases for path and store-state handling."""
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


def payload(topic: str, *, cid: str | None = None) -> str:
    raw = {
        "topic": topic,
        "sections": {
            "summary": f"{topic} summary",
            "dict": "- **edge** - case",
            "qa": "- **Q:** stable? **A:** yes.",
        },
    }
    if cid:
        raw["id"] = cid
    return json.dumps(raw)


def record_text(
    *,
    cid: str,
    topic: str,
    status: str = "active",
    marker: str = "edge marker",
    refs: str = "refs = []",
) -> str:
    return f"""+++
id = "{cid}"
topic = "{topic}"
status = "{status}"
tags = ["edge"]
{refs}
created = "2026-01-01T00:00:00Z"
updated = "2026-01-01T00:00:00Z"
+++
## summary
{marker}

## dict
- **edge** - case

## qa
- **Q:** stable? **A:** yes.

## resume
(none)

## user-instructions
(none)

## condensed-transcript
(none)
"""


class CliEdgeCasesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.env = clean_env(home=self.home)
        self.root = self.home / ".relay"
        self.db = self.root / "convs"
        self.addCleanup(self._tmp.cleanup)

    def test_existing_global_root_without_convs_is_created_by_read_commands(self) -> None:
        self.root.mkdir(parents=True)

        listed = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(load_json(listed), [])
        self.assertTrue(self.db.is_dir())
        self.assertTrue((self.root / "index.jsonl").is_file())

        searched = run_cli(["search", "anything"], cwd=self.tmp, env=self.env)
        self.assertEqual(searched.returncode, 0, searched.stderr)
        self.assertEqual(load_json(searched), [])

    def test_relative_conv_root_before_subcommand_resolves_from_cwd(self) -> None:
        proc = run_cli(
            ["--relay-root", Path("nested") / ".." / "compat store", "doctor"],
            cwd=self.tmp,
            env=self.env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)
        expected = (self.tmp / "compat store").resolve()

        self.assertEqual(Path(out["plugin_installation_root"]), expected)
        self.assertEqual(Path(out["conversation_database"]), expected / "convs")
        self.assertEqual(out["resolution"]["layer"], "compat-flag")
        self.assertFalse(self.root.exists(), "explicit compatibility root must not create the default root")

    def test_read_commands_do_not_create_write_lock_or_invalid_root(self) -> None:
        created = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("read-only dispatch", cid="conv_260123_read-only"),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        lock = self.root / ".semble" / "write.lock"
        lock.unlink()

        commands = [
            (["list", "--json"], lambda out: self.assertEqual(out[0]["id"], "conv_260123_read-only")),
            (
                ["show", "conv_260123_read-only"],
                lambda out: self.assertEqual(out["id"], "conv_260123_read-only"),
            ),
            (
                ["search", "read-only dispatch"],
                lambda out: self.assertEqual(out[0]["id"], "conv_260123_read-only"),
            ),
            (["doctor"], lambda out: self.assertEqual(out["records"], 1)),
        ]
        for args, assert_output in commands:
            with self.subTest(args=args):
                proc = run_cli(args, cwd=self.tmp, env=self.env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                assert_output(load_json(proc))
                self.assertFalse(lock.exists())

        invalid_root = self.tmp / "invalid-command-root"
        invalid = run_cli(
            ["--relay-root", invalid_root, "invalid-command"],
            cwd=self.tmp,
            env=self.env,
        )
        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertFalse(invalid_root.exists())

    def test_odd_filename_and_status_record_does_not_break_record_commands(self) -> None:
        self.db.mkdir(parents=True)
        odd = self.db / "2026-01-02_Mixed Slug.status.md"
        odd.write_text(
            record_text(
                cid="conv_260102_mixed-slug",
                topic="Mixed Slug Status",
                status="needs-review",
                marker="unique edge marker",
            ),
            encoding="utf-8",
        )

        listed = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(load_json(listed)[0]["status"], "needs-review")

        searched = run_cli(["search", "unique edge"], cwd=self.tmp, env=self.env)
        self.assertEqual(searched.returncode, 0, searched.stderr)
        self.assertEqual(load_json(searched)[0]["id"], "conv_260102_mixed-slug")

        shown = run_cli(["show", "conv_260102_mixed-slug"], cwd=self.tmp, env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(load_json(shown)["status"], "needs-review")

        doctor = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertEqual(load_json(doctor)["parse_errors"], [])

    def test_doctor_reports_malformed_record_without_crashing(self) -> None:
        self.db.mkdir(parents=True)
        bad = self.db / "2026-01-03_bad.md"
        bad.write_text("not a conversation record\n", encoding="utf-8")

        proc = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(len(out["parse_errors"]), 1)
        self.assertIn(str(bad), out["parse_errors"][0]["file"])
        self.assertIn("frontmatter", out["parse_errors"][0]["error"])

    def test_malformed_index_is_rebuilt_from_conversation_database(self) -> None:
        self.db.mkdir(parents=True)
        (self.db / "2026-01-04_valid.md").write_text(
            record_text(cid="conv_260104_valid", topic="valid after corrupt index"),
            encoding="utf-8",
        )
        (self.root / "index.jsonl").write_text("{not-json}\n", encoding="utf-8")

        proc = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        records = load_json(proc)

        self.assertEqual([record["id"] for record in records], ["conv_260104_valid"])
        self.assertNotIn("{not-json}", (self.root / "index.jsonl").read_text(encoding="utf-8"))

    def test_invalid_json_index_row_is_rebuilt_for_search(self) -> None:
        self.db.mkdir(parents=True)
        (self.db / "2026-01-05_valid.md").write_text(
            record_text(
                cid="conv_260105_valid",
                topic="valid after invalid index row",
                marker="needle from database",
            ),
            encoding="utf-8",
        )
        (self.root / "index.jsonl").write_text(
            json.dumps({"topic": "needle from stale index", "file": "../escape.md"}) + "\n",
            encoding="utf-8",
        )

        proc = run_cli(["search", "needle"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        records = load_json(proc)

        self.assertEqual([record["id"] for record in records], ["conv_260105_valid"])
        self.assertNotIn("../escape.md", (self.root / "index.jsonl").read_text(encoding="utf-8"))

    def test_doctor_counts_conversation_database_records_not_stale_index_rows(self) -> None:
        self.db.mkdir(parents=True)
        (self.db / "2026-01-06_valid.md").write_text(
            record_text(cid="conv_260106_valid", topic="valid doctor count"),
            encoding="utf-8",
        )
        (self.root / "index.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"id": "stale-one"}),
                    json.dumps({"id": "stale-two"}),
                    "",
                ]
            ),
            encoding="utf-8",
        )

        proc = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(out["records"], 1)
        self.assertFalse(out["index_health"]["valid"])

    def test_phantom_index_row_is_unhealthy_and_filtered_from_list(self) -> None:
        self.db.mkdir(parents=True)
        (self.db / "2026-01-14_valid.md").write_text(
            record_text(cid="conv_260114_valid", topic="valid database row"),
            encoding="utf-8",
        )
        phantom_row = {
            "id": "conv_260115_phantom",
            "topic": "phantom index row",
            "status": "active",
            "tags": [],
            "refs": [],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
            "file": "convs/2026-01-15_phantom.md",
            "open": 0,
        }
        (self.root / "index.jsonl").write_text(json.dumps(phantom_row) + "\n", encoding="utf-8")

        doctor = run_cli(["doctor"], cwd=self.tmp, env=self.env)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        doctor_out = load_json(doctor)
        self.assertEqual(doctor_out["records"], 1)
        self.assertFalse(doctor_out["index_health"]["valid"])
        self.assertIn("missing relay record", doctor_out["index_health"]["error"])

        listed = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        records = load_json(listed)
        self.assertEqual([record["id"] for record in records], ["conv_260114_valid"])
        self.assertNotIn("conv_260115_phantom", [record["id"] for record in records])

    def test_upsert_updates_frontmatter_id_when_valid_index_points_elsewhere(self) -> None:
        self.db.mkdir(parents=True)
        cid = "conv_260109_stale-index"
        actual = self.db / "custom-stale-index-name.md"
        wrong = self.db / "2026-01-10_wrong.md"
        actual.write_text(
            record_text(cid=cid, topic="actual stale index target", marker="original marker"),
            encoding="utf-8",
        )
        wrong.write_text(
            record_text(cid="conv_260110_wrong", topic="wrong index target", marker="wrong marker"),
            encoding="utf-8",
        )
        stale_row = {
            "id": cid,
            "topic": "stale row",
            "status": "active",
            "tags": [],
            "refs": [],
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
            "file": "convs/2026-01-10_wrong.md",
            "open": 0,
        }
        (self.root / "index.jsonl").write_text(json.dumps(stale_row) + "\n", encoding="utf-8")

        proc = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("updated through frontmatter id", cid=cid),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        self.assertIn("updated through frontmatter id summary", actual.read_text(encoding="utf-8"))
        self.assertIn("wrong marker", wrong.read_text(encoding="utf-8"))
        self.assertFalse((self.db / "2026-01-09_stale-index.md").exists())

    def test_upsert_removes_and_retargets_stale_reverse_refs(self) -> None:
        self.db.mkdir(parents=True)
        parent_a = "conv_260111_parent-a"
        parent_b = "conv_260112_parent-b"
        child = "conv_260113_child"
        (self.db / "2026-01-11_parent-a.md").write_text(
            record_text(
                cid=parent_a,
                topic="parent a",
                refs=f'refs = [{{ id = "{child}", rel = "spawned-to" }}]',
            ),
            encoding="utf-8",
        )
        (self.db / "2026-01-12_parent-b.md").write_text(
            record_text(cid=parent_b, topic="parent b"),
            encoding="utf-8",
        )
        (self.db / "2026-01-13_child.md").write_text(
            record_text(
                cid=child,
                topic="child",
                refs=f'refs = [{{ id = "{parent_a}", rel = "spawned-from" }}]',
            ),
            encoding="utf-8",
        )

        removed = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=json.dumps(
                {
                    "id": child,
                    "topic": "child without parent",
                    "refs": [],
                    "sections": {
                        "summary": "child summary",
                        "dict": "- **child** - moved",
                        "qa": "- **Q:** linked? **A:** no.",
                    },
                }
            ),
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        parent_a_after_remove = load_json(run_cli(["show", parent_a], cwd=self.tmp, env=self.env))
        child_after_remove = load_json(run_cli(["show", child], cwd=self.tmp, env=self.env))
        self.assertNotIn({"id": child, "rel": "spawned-to"}, parent_a_after_remove["refs"])
        self.assertEqual(child_after_remove["refs"], [])

        retargeted = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=json.dumps(
                {
                    "id": child,
                    "topic": "child with new parent",
                    "refs": [{"id": parent_b, "rel": "spawned-from"}],
                    "sections": {
                        "summary": "child summary",
                        "dict": "- **child** - moved",
                        "qa": "- **Q:** linked? **A:** yes.",
                    },
                }
            ),
        )
        self.assertEqual(retargeted.returncode, 0, retargeted.stderr)
        parent_a_after_retarget = load_json(run_cli(["show", parent_a], cwd=self.tmp, env=self.env))
        parent_b_after_retarget = load_json(run_cli(["show", parent_b], cwd=self.tmp, env=self.env))
        self.assertNotIn({"id": child, "rel": "spawned-to"}, parent_a_after_retarget["refs"])
        self.assertIn({"id": child, "rel": "spawned-to"}, parent_b_after_retarget["refs"])

    def test_upsert_rejects_pathlike_id_and_writes_nothing_outside_convs(self) -> None:
        proc = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("escape attempt", cid="../outside"),
        )

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("Relay archive", proc.stderr)
        self.assertFalse((self.root / "outside.md").exists())
        self.assertEqual(list(self.db.glob("*.md")) if self.db.exists() else [], [])

    def test_upsert_rejects_portable_invalid_ids(self) -> None:
        invalid_ids = [
            "bad:name",
            "bad*name",
            'bad"name',
            "bad\u0001name",
            "CON",
            "NUL.txt",
            "bad.",
            "bad ",
        ]
        for cid in invalid_ids:
            with self.subTest(cid=cid):
                proc = run_cli(
                    ["upsert", "--stdin"],
                    cwd=self.tmp,
                    env=self.env,
                    input=payload("portable invalid id", cid=cid),
                )
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("relay:", proc.stderr)
                self.assertIn("portable filename", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(list(self.db.glob("*.md")) if self.db.exists() else [], [])

    def test_upsert_rejects_invalid_ref_ids_and_writes_nothing(self) -> None:
        invalid_refs = [
            ("../outside", "Relay archive"),
            ("bad:name", "portable filename"),
            ("NUL.txt", "portable filename"),
            ("bad ", "portable filename"),
        ]
        for index, (ref_id, message) in enumerate(invalid_refs):
            with self.subTest(ref_id=ref_id):
                raw = json.loads(payload("invalid ref id", cid=f"conv_260120_invalid-ref-{index}"))
                raw["refs"] = [{"id": ref_id, "rel": "spawned-from"}]
                proc = run_cli(
                    ["upsert", "--stdin"],
                    cwd=self.tmp,
                    env=self.env,
                    input=json.dumps(raw),
                )
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("relay:", proc.stderr)
                self.assertIn(message, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(list(self.db.glob("*.md")) if self.db.exists() else [], [])

    def test_upsert_bad_stdin_json_reports_conv_error(self) -> None:
        proc = run_cli(["upsert", "--stdin"], cwd=self.tmp, env=self.env, input="{not-json}")

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("relay:", proc.stderr)
        self.assertIn("invalid JSON", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_upsert_missing_json_file_reports_conv_error(self) -> None:
        proc = run_cli(["upsert", "--json", self.tmp / "missing.json"], cwd=self.tmp, env=self.env)

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("relay:", proc.stderr)
        self.assertIn("cannot read JSON", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_existing_file_as_conv_root_reports_conv_error(self) -> None:
        root_file = self.tmp / "not-a-root"
        root_file.write_text("not a directory", encoding="utf-8")

        proc = run_cli(["--relay-root", root_file, "list", "--json"], cwd=self.tmp, env=self.env)

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("relay:", proc.stderr)
        self.assertIn("Plugin installation root", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_list_and_search_reject_negative_limit(self) -> None:
        for args in (["list", "--limit", "-1"], ["search", "anything", "--limit", "-1"]):
            with self.subTest(args=args):
                proc = run_cli(args, cwd=self.tmp, env=self.env)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("relay:", proc.stderr)
                self.assertIn("--limit", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_show_missing_target_names_conversation_database(self) -> None:
        proc = run_cli(["show", "missing target"], cwd=self.tmp, env=self.env)

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("not found", proc.stderr)
        self.assertIn("Relay archive", proc.stderr)
        self.assertIn("list", proc.stderr)
        self.assertIn("search", proc.stderr)

    def test_show_ambiguous_target_lists_matches(self) -> None:
        first = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("shared show target one", cid="conv_260107_shared-one"),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("shared show target two", cid="conv_260108_shared-two"),
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        proc = run_cli(["show", "shared show target"], cwd=self.tmp, env=self.env)

        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("ambiguous", proc.stderr)
        self.assertIn("conv_260107_shared-one", proc.stderr)
        self.assertIn("conv_260108_shared-two", proc.stderr)
        self.assertIn("list", proc.stderr)
        self.assertIn("search", proc.stderr)


    def test_search_uses_semble_result_and_falls_back_to_body_search(self) -> None:
        self.db.mkdir(parents=True)
        semantic_query = "semantic-nebula;echo injected&pipe|"
        semble_hit = self.db / "2026-01-22_beta.md"
        body_hit = self.db / "2026-01-21_alpha.md"
        body_hit.write_text(
            record_text(
                cid="conv_260121_alpha",
                topic="alpha catalog",
                marker="semantic-nebula body-only result",
            ),
            encoding="utf-8",
        )
        semble_hit.write_text(
            record_text(
                cid="conv_260122_beta",
                topic="beta catalog",
                marker="unrelated body",
            ),
            encoding="utf-8",
        )

        fake_dir = self.tmp / "fake-bin"
        fake_dir.mkdir()
        args_file = self.tmp / "semble-args.json"
        runner = self.tmp / "search" if os.name == "nt" else fake_dir / "semble_runner.py"
        runner.write_text(
            """import json
import os
import sys
from pathlib import Path

Path(os.environ["SEMBLE_ARGS_FILE"]).write_text(
    json.dumps(sys.argv), encoding="utf-8"
)
if os.environ.get("SEMBLE_EXIT") == "1":
    raise SystemExit(7)
print(os.environ["SEMBLE_OUTPUT"])
""",
            encoding="utf-8",
        )
        if os.name == "nt":
            fake = fake_dir / "semble.exe"
            shutil.copy2(sys.executable, fake)
        else:
            fake = fake_dir / "semble"
            fake.write_text(
                f"#!{sys.executable}\n"
                + runner.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            fake.chmod(0o755)

        env = dict(self.env)
        env["PATH"] = os.pathsep.join([str(fake_dir), env.get("PATH", "")])
        env["SEMBLE_ARGS_FILE"] = str(args_file)
        env["SEMBLE_OUTPUT"] = "convs/2026-01-22_beta.md"

        semantic = run_cli(["search", semantic_query], cwd=self.tmp, env=env)
        self.assertEqual(semantic.returncode, 0, semantic.stderr)
        semantic_records = load_json(semantic)
        self.assertEqual([record["id"] for record in semantic_records], ["conv_260122_beta"])
        self.assertEqual(semantic_records[0]["layer"], "semble")
        recorded_args = json.loads(args_file.read_text(encoding="utf-8"))
        self.assertEqual(recorded_args.count(semantic_query), 1)

        env["SEMBLE_EXIT"] = "1"
        fallback = run_cli(["search", semantic_query], cwd=self.tmp, env=env)
        self.assertEqual(fallback.returncode, 0, fallback.stderr)
        fallback_records = load_json(fallback)
        self.assertEqual([record["id"] for record in fallback_records], ["conv_260121_alpha"])
        self.assertEqual(fallback_records[0]["layer"], "semble-body-fallback")
if __name__ == "__main__":
    unittest.main()
