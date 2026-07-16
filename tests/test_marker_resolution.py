"""CLI global root defaults.

Contract: normal CLI commands use the Plugin installation root at `~/.relay/`
and the Relay archive at `~/.relay/convs/`. Legacy cwd markers and
environment roots are not default resolution inputs.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import clean_env, load_json, run_cli  # noqa: E402


def payload(topic: str, *, cid: str | None = None, refs: list[dict[str, str]] | None = None) -> str:
    raw = {
        "topic": topic,
        "sections": {
            "summary": f"{topic} summary",
            "dict": "- **term** - meaning",
            "qa": "- **Q:** q? **A:** a.",
        },
    }
    if cid:
        raw["id"] = cid
    if refs is not None:
        raw["refs"] = refs
    return json.dumps(raw)


def plant_legacy_marker_shape(work: Path) -> None:
    (work / ".relay").mkdir(exist_ok=True)
    (work / ".conv-root").write_text("", encoding="utf-8")
    (work / "conv").mkdir(exist_ok=True)


class GlobalRootDefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.env = clean_env(home=self.home)
        self.root = self.home / ".relay"
        self.db = self.root / "convs"
        self.addCleanup(self._tmp.cleanup)

    def test_read_command_defaults_to_global_conversation_database(self) -> None:
        proc = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(load_json(proc), [])
        self.assertTrue(self.db.is_dir())
        self.assertFalse((self.tmp / ".relay").exists())

    def test_init_uses_global_root_even_when_cwd_marker_shape_exists(self) -> None:
        cwd_root = self.tmp / ".relay"
        plant_legacy_marker_shape(self.tmp)

        proc = run_cli(["init"], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), self.root)
        self.assertEqual(Path(out["conversation_database"]), self.db)
        self.assertFalse((cwd_root / "index.jsonl").exists(), "cwd marker shape must not be used")

    def test_legacy_env_roots_are_reported_but_ignored_by_default(self) -> None:
        env_root = self.tmp / "env-root"
        proc = run_cli(
            ["doctor"],
            cwd=self.tmp,
            env=clean_env(home=self.home, RELAY_ROOT=env_root, BRAIN_CONV=self.tmp / "brain-root"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), self.root)
        self.assertEqual(Path(out["conversation_database"]), self.db)
        self.assertEqual(out["resolution"]["layer"], "default-global")
        self.assertFalse(out["resolution"]["compatibility"])
        self.assertEqual(out["resolution"]["ignored_legacy_env"], ["RELAY_ROOT", "BRAIN_CONV"])
        self.assertFalse(env_root.exists())

    def test_write_command_ignores_legacy_env_roots_by_default(self) -> None:
        env_root = self.tmp / "env-root"
        brain_root = self.tmp / "brain-root"
        proc = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=clean_env(home=self.home, RELAY_ROOT=env_root, BRAIN_CONV=brain_root),
            input=payload("env ignored", cid="conv_260104_env-ignored"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rel = load_json(proc)["file"]

        self.assertTrue((self.root / rel).is_file())
        self.assertFalse(env_root.exists(), "RELAY_ROOT must not become the default Plugin installation root")
        self.assertFalse(brain_root.exists(), "BRAIN_CONV must not become the default Plugin installation root")

    def test_explicit_conv_root_remains_compatibility_override(self) -> None:
        compat_root = self.tmp / "compat-root"
        proc = run_cli(["doctor", "--relay-root", compat_root], cwd=self.tmp, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = load_json(proc)

        self.assertEqual(Path(out["plugin_installation_root"]), compat_root)
        self.assertEqual(Path(out["conversation_database"]), compat_root / "convs")
        self.assertEqual(out["resolution"]["layer"], "compat-flag")
        self.assertTrue(out["resolution"]["compatibility"])

    def test_default_supported_commands_round_trip_through_global_database(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        first = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("alpha default", cid="conv_260101_alpha"),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload(
                "beta default",
                cid="conv_260102_beta",
                refs=[{"id": "conv_260101_alpha", "rel": "spawned-from"}],
            ),
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        listed = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual({record["id"] for record in load_json(listed)}, {"conv_260101_alpha", "conv_260102_beta"})

        hit = run_cli(["search", "alpha"], cwd=self.tmp, env=self.env)
        self.assertEqual(hit.returncode, 0, hit.stderr)
        self.assertEqual(load_json(hit)[0]["id"], "conv_260101_alpha")

        shown = run_cli(["show", "conv_260101_alpha"], cwd=self.tmp, env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(load_json(shown)["id"], "conv_260101_alpha")

        status = run_cli(["set-status", "conv_260101_alpha", "parked"], cwd=self.tmp, env=self.env)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(load_json(status)["status"], "parked")

        refs = run_cli(["regen-refs"], cwd=self.tmp, env=self.env)
        self.assertEqual(refs.returncode, 0, refs.stderr)
        self.assertEqual(load_json(refs)["records"], 2)
        alpha = run_cli(["show", "conv_260101_alpha"], cwd=self.tmp, env=self.env)
        alpha_refs = load_json(alpha)["refs"]
        self.assertIn({"id": "conv_260102_beta", "rel": "spawned-to"}, alpha_refs)

        (self.root / "index.jsonl").unlink()
        rebuilt = run_cli(["rebuild-index"], cwd=self.tmp, env=self.env)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(load_json(rebuilt)["records"], 2)

    def test_read_commands_ignore_cwd_compatibility_root_without_flag(self) -> None:
        local_root = self.tmp / ".relay"
        self.assertEqual(run_cli(["init", "--relay-root", local_root], cwd=self.tmp, env=self.env).returncode, 0)
        local_upsert = run_cli(
            ["upsert", "--stdin", "--relay-root", local_root],
            cwd=self.tmp,
            env=self.env,
            input=payload("local only", cid="conv_260105_local-only"),
        )
        self.assertEqual(local_upsert.returncode, 0, local_upsert.stderr)
        plant_legacy_marker_shape(self.tmp)

        listed = run_cli(["list", "--json"], cwd=self.tmp, env=self.env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(load_json(listed), [])

        hits = run_cli(["search", "local only"], cwd=self.tmp, env=self.env)
        self.assertEqual(hits.returncode, 0, hits.stderr)
        self.assertEqual(load_json(hits), [])

        shown = run_cli(["show", "conv_260105_local-only"], cwd=self.tmp, env=self.env)
        self.assertEqual(shown.returncode, 2)
        self.assertIn("relay:", shown.stderr)
        self.assertNotIn("Traceback", shown.stderr)

    def test_rebuild_index_uses_only_global_conversation_database(self) -> None:
        self.assertEqual(run_cli(["init"], cwd=self.tmp, env=self.env).returncode, 0)
        global_upsert = run_cli(
            ["upsert", "--stdin"],
            cwd=self.tmp,
            env=self.env,
            input=payload("global only", cid="conv_260103_global"),
        )
        self.assertEqual(global_upsert.returncode, 0, global_upsert.stderr)

        local_db = self.tmp / ".relay" / "convs"
        local_db.mkdir(parents=True)
        plant_legacy_marker_shape(self.tmp)
        (local_db / "2026-01-03_local.md").write_text(
            """+++
id = "conv_260103_local"
topic = "local must be ignored"
status = "active"
tags = []
refs = []
created = "2026-01-03T00:00:00Z"
updated = "2026-01-03T00:00:00Z"
+++
## summary
local

## dict
- **x** - y

## qa
- **Q:** q? **A:** a.
""",
            encoding="utf-8",
        )

        (self.root / "index.jsonl").unlink()
        rebuilt = run_cli(["rebuild-index"], cwd=self.tmp, env=self.env)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(load_json(rebuilt)["records"], 1)
        records = [json.loads(line) for line in (self.root / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["id"] for record in records], ["conv_260103_global"])


if __name__ == "__main__":
    unittest.main()
