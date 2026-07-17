"""Relay v2 fidelity schema and context-pack quality gates."""
from __future__ import annotations

import json
from pathlib import Path

from _util import RUST_BINARY, clean_env, load_json, run_cli


NOW = "2026-01-16T12:00:00Z"
PARENT = "conv_260116_context-parent"
BRANCH = "conv_260117_context-branch"


def base_payload(cid: str, topic: str) -> dict:
    return {
        "id": cid,
        "topic": topic,
        "status": "active",
        "tags": ["context", "v2"],
        "created": NOW,
        "updated": NOW,
        "sections": {
            "summary": f"{topic} summary",
            "dict": "- **context pack** - reconstruction-ordered Relay output",
            "qa": "- **Q (open):** complete? **A:** continue from the pack.",
            "decisions": "- preserve deterministic fidelity",
        },
        "resume": {
            "goal": f"resume {topic}",
            "checkpoints": ["schema agreed", "fixtures written"],
            "next_steps": ["implement the context renderer"],
            "open_questions": ["how small can the pack be?"],
        },
        "user_instructions": ["keep exact ordering", "do not fabricate state"],
    }


def parent_payload() -> str:
    raw = base_payload(PARENT, "context parent")
    raw["environment"] = ["platform: test", "repo: /reference/only"]
    raw["artifacts"] = ["tests/test_context_pack.py — forward gate", "commit: none"]
    raw["condensed_transcript"] = [
        {"u": "low " + "L" * 400, "a": "low answer", "w": 1},
        {"u": "medium " + "M" * 260, "a": "medium answer", "w": 2},
        {"u": "high " + "H" * 80, "a": "high answer", "w": 3},
        {"u": "default weight", "a": "defaults to one"},
    ]
    return json.dumps(raw)


def setup_context_graph(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    init = run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env)
    assert init.returncode == 0, init.stderr
    parent = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=tmp_path,
        env=env,
        input=parent_payload(),
    )
    assert parent.returncode == 0, parent.stderr

    branch = base_payload(BRANCH, "context branch")
    branch["refs"] = [{"id": PARENT, "rel": "spawned-from"}]
    created = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=tmp_path,
        env=env,
        input=json.dumps(branch),
    )
    assert created.returncode == 0, created.stderr
    closed = run_cli(
        ["return", BRANCH, "--digest", "Closed branch digest for one-hop recovery.", "--relay-root", root],
        cwd=tmp_path,
        env=env,
    )
    assert closed.returncode == 0, closed.stderr
    return root, env


def context(root: Path, env: dict[str, str], cwd: Path, *args: object):
    return run_cli(["context", PARENT, *args, "--relay-root", root], cwd=cwd, env=env)


def section_map(pack: dict) -> dict[str, str]:
    return {section["name"]: section["markdown"] for section in pack["sections"]}


def test_structured_fidelity_fields_are_durable_and_canonical(tmp_path: Path) -> None:
    root, env = setup_context_graph(tmp_path)
    shown = run_cli(["show", PARENT, "--markdown", "--relay-root", root], cwd=tmp_path, env=env)
    assert shown.returncode == 0, shown.stderr
    markdown = shown.stdout

    headers = [
        "## summary",
        "## dict",
        "## qa",
        "## decisions",
        "## environment",
        "## artifacts",
        "## resume",
        "## user-instructions",
        "## condensed-transcript",
    ]
    assert [markdown.index(header) for header in headers] == sorted(markdown.index(header) for header in headers)
    assert "- checkpoints:\n  - schema agreed\n  - fixtures written\n- next-steps:" in markdown
    assert "relay_schema = 2" in markdown
    assert markdown.count("<!-- relay:transcript-weight=") == 4
    assert "<!-- relay:transcript-weight=3 -->" in markdown
    assert "<!-- relay:transcript-weight=1 -->\n- U: default weight" in markdown

    rebuilt = run_cli(["rebuild-index", "--full", "--relay-root", root], cwd=tmp_path, env=env)
    assert rebuilt.returncode == 0, rebuilt.stderr
    shown_again = run_cli(["show", PARENT, "--markdown", "--relay-root", root], cwd=tmp_path, env=env)
    assert shown_again.stdout == markdown


def test_branch_primitives_preserve_fidelity_fields_but_not_artifacts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    created = run_cli(
        ["upsert", "--stdin", "--relay-root", root],
        cwd=tmp_path,
        env=env,
        input=parent_payload(),
    )
    assert created.returncode == 0, created.stderr

    commands = [
        ["sidekick", PARENT, "side branch", "--id", "conv_260118_side", "--keep-parent-active"],
        ["continue", PARENT, "--topic", "continuation", "--id", "conv_260119_continued"],
    ]
    for args in commands:
        proc = run_cli([*args, "--relay-root", root], cwd=tmp_path, env=env)
        assert proc.returncode == 0, proc.stderr
        child_id = args[args.index("--id") + 1]
        shown = run_cli(["show", child_id, "--markdown", "--relay-root", root], cwd=tmp_path, env=env)
        assert shown.returncode == 0, shown.stderr
        markdown = shown.stdout
        assert "## environment" in markdown
        assert "## user-instructions" in markdown
        assert "- checkpoints:" in markdown
        assert markdown.count("<!-- relay:transcript-weight=") == 4
        assert "## artifacts" not in markdown


def test_pre_v2_marker_looking_text_stays_literal_content(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    source = tmp_path / "legacy-source"
    convs = source / "convs"
    convs.mkdir(parents=True)
    legacy_id = "conv_260115_legacy-marker"
    (convs / f"{legacy_id}.md").write_text(
        f'''+++
id = "{legacy_id}"
topic = "legacy marker"
status = "active"
tags = []
refs = []
created = "{NOW}"
updated = "{NOW}"
+++
## summary
legacy marker summary

## dict
- **legacy** - marker text predates schema 2

## qa
- **Q:** literal? **A:** yes.

## resume
(none)

## user-instructions
(none)

## condensed-transcript
<!-- relay:transcript-weight=3 -->
- U: this marker-looking line is literal
- A: preserve it verbatim
''',
        encoding="utf-8",
    )
    imported = run_cli(["import", "--from", source, "--relay-root", root], cwd=tmp_path, env=env)
    assert imported.returncode == 0, imported.stderr
    packed = run_cli(["context", legacy_id, "--json", "--relay-root", root], cwd=tmp_path, env=env)
    assert packed.returncode == 0, packed.stderr
    transcript = section_map(load_json(packed))["condensed-transcript"]
    assert "<!-- relay:transcript-weight=3 -->" in transcript
    assert "this marker-looking line is literal" in transcript


def test_invalid_transcript_weights_reject_before_writing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0

    for index, weight in enumerate((0, 4, 1.5, "3")):
        raw = base_payload(f"conv_26012{index}_invalid-weight", "invalid weight")
        raw["condensed_transcript"] = [{"u": "question", "a": "answer", "w": weight}]
        proc = run_cli(
            ["upsert", "--stdin", "--relay-root", root],
            cwd=tmp_path,
            env=env,
            input=json.dumps(raw),
        )
        assert proc.returncode == 2
        assert "weight" in proc.stderr.lower()
        assert not any(f'id = "{raw["id"]}"' in path.read_text(encoding="utf-8") for path in (root / "convs").glob("*.md"))


def test_context_text_and_json_have_reconstruction_order_and_one_hop_digest(tmp_path: Path) -> None:
    root, env = setup_context_graph(tmp_path)
    json_proc = context(root, env, tmp_path, "--json")
    assert json_proc.returncode == 0, json_proc.stderr
    pack = load_json(json_proc)

    assert pack["schema_version"] == 1
    assert pack["id"] == PARENT
    assert Path(pack["plugin_installation_root"]) == root
    assert pack["budget_tokens"] is None
    assert pack["truncated"] is False
    assert pack["estimated_tokens"] >= pack["minimum_tokens"] > 0
    assert [section["name"] for section in pack["sections"]] == [
        "summary",
        "dict",
        "user-instructions",
        "resume",
        "qa",
        "decisions",
        "environment",
        "artifacts",
        "condensed-transcript",
    ]
    assert "relay:transcript-weight" not in json.dumps(pack)
    assert pack["linked"] == [
        {
            "id": BRANCH,
            "rel": "spawned-to",
            "topic": "context branch",
            "status": "closed",
            "digest": "Closed branch digest for one-hop recovery.",
        }
    ]
    assert pack["action_argv"] == [str(RUST_BINARY.resolve()), "set-status", PARENT, "active", "--relay-root", str(root)]

    text_proc = context(root, env, tmp_path)
    assert text_proc.returncode == 0, text_proc.stderr
    text = text_proc.stdout
    ordered = [
        "## summary",
        "## dict",
        "## user-instructions",
        "## resume",
        "## qa",
        "## decisions",
        "## environment",
        "## artifacts",
        "## condensed-transcript",
        "## linked-context",
        "next action argv:",
        "truncated: no",
    ]
    assert [text.index(item) for item in ordered] == sorted(text.index(item) for item in ordered)
    assert text.rstrip().endswith("truncated: no")
    assert "relay:transcript-weight" not in text

    no_refs = context(root, env, tmp_path, "--json", "--no-refs")
    assert no_refs.returncode == 0, no_refs.stderr
    assert load_json(no_refs)["linked"] == []


def test_budget_drops_linked_context_before_owned_transcript(tmp_path: Path) -> None:
    root, env = setup_context_graph(tmp_path)
    full_proc = context(root, env, tmp_path, "--json")
    assert full_proc.returncode == 0, full_proc.stderr
    full = load_json(full_proc)
    proc = context(root, env, tmp_path, "--json", "--budget-tokens", full["estimated_tokens"] - 1)
    assert proc.returncode == 0, proc.stderr
    trimmed = load_json(proc)

    assert trimmed["truncated"] is True
    assert len(trimmed["linked"]) < len(full["linked"])
    assert section_map(trimmed)["condensed-transcript"] == section_map(full)["condensed-transcript"]
    assert trimmed["estimated_tokens"] <= trimmed["budget_tokens"]


def test_budget_trims_low_weight_first_and_enforces_exact_byte_cap(tmp_path: Path) -> None:
    root, env = setup_context_graph(tmp_path)
    full_proc = context(root, env, tmp_path, "--json")
    assert full_proc.returncode == 0, full_proc.stderr
    full = load_json(full_proc)
    budget = full["minimum_tokens"] + 60
    json_proc = context(root, env, tmp_path, "--json", "--budget-tokens", budget)
    assert json_proc.returncode == 0, json_proc.stderr
    pack = load_json(json_proc)
    transcript = section_map(pack).get("condensed-transcript", "")

    assert pack["truncated"] is True
    assert pack["estimated_tokens"] <= budget
    assert "high " in transcript
    assert "low " not in transcript
    assert "relay:transcript-weight" not in transcript

    text_proc = context(root, env, tmp_path, "--budget-tokens", budget)
    assert text_proc.returncode == 0, text_proc.stderr
    assert len(text_proc.stdout.encode("utf-8")) <= 4 * budget
    assert text_proc.stdout.rstrip().endswith("truncated: yes")


def test_missing_link_warning_is_an_optional_budget_unit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    raw = base_payload(PARENT, "missing link owner")
    raw["refs"] = [{"id": "conv_260199_missing", "rel": "informed-by"}]
    created = run_cli(["upsert", "--stdin", "--relay-root", root], cwd=tmp_path, env=env, input=json.dumps(raw))
    assert created.returncode == 0, created.stderr

    full_proc = context(root, env, tmp_path, "--json")
    assert full_proc.returncode == 0, full_proc.stderr
    full = load_json(full_proc)
    assert full["warnings"] == [{"id": "conv_260199_missing", "rel": "informed-by", "error": "missing"}]
    trimmed_proc = context(root, env, tmp_path, "--json", "--budget-tokens", full["estimated_tokens"] - 1)
    assert trimmed_proc.returncode == 0, trimmed_proc.stderr
    trimmed = load_json(trimmed_proc)
    assert trimmed["warnings"] == []
    assert trimmed["truncated"] is True

    text_proc = context(root, env, tmp_path, "--budget-tokens", full["estimated_tokens"] - 1)
    assert text_proc.returncode == 0, text_proc.stderr
    assert len(text_proc.stdout.encode("utf-8")) <= 4 * (full["estimated_tokens"] - 1)


def test_too_small_budget_errors_without_mutating_record_or_status(tmp_path: Path) -> None:
    root, env = setup_context_graph(tmp_path)
    full_proc = context(root, env, tmp_path, "--json")
    assert full_proc.returncode == 0, full_proc.stderr
    full = load_json(full_proc)
    record = next(path for path in (root / "convs").glob("*.md") if f'id = "{PARENT}"' in path.read_text(encoding="utf-8"))
    before = record.read_bytes()

    proc = context(root, env, tmp_path, "--budget-tokens", full["minimum_tokens"] - 1)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert str(full["minimum_tokens"]) in proc.stderr
    assert record.read_bytes() == before
    shown = load_json(run_cli(["show", PARENT, "--relay-root", root], cwd=tmp_path, env=env))
    assert shown["status"] == "active"


def test_context_action_argv_targets_the_resolved_custom_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    default_root = home / ".relay"
    custom_root = tmp_path / "custom root with spaces" / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    for root in (default_root, custom_root):
        assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
        raw = base_payload(PARENT, f"record in {root.parent.name}")
        raw["status"] = "parked"
        created = run_cli(
            ["upsert", "--stdin", "--relay-root", root],
            cwd=tmp_path,
            env=env,
            input=json.dumps(raw),
        )
        assert created.returncode == 0, created.stderr

    packed = run_cli(["context", PARENT, "--json", "--relay-root", custom_root], cwd=tmp_path, env=env)
    assert packed.returncode == 0, packed.stderr
    action = load_json(packed)["action_argv"]
    assert action == [str(RUST_BINARY.resolve()), "set-status", PARENT, "active", "--relay-root", str(custom_root)]
    activated = run_cli(action[1:], cwd=tmp_path, env=env)
    assert activated.returncode == 0, activated.stderr

    custom = load_json(run_cli(["show", PARENT, "--relay-root", custom_root], cwd=tmp_path, env=env))
    default = load_json(run_cli(["show", PARENT, "--relay-root", default_root], cwd=tmp_path, env=env))
    assert custom["status"] == "active"
    assert default["status"] == "parked"


def test_doctor_fidelity_warning_is_report_only_even_with_fix(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".relay"
    env = clean_env(home=home, RELAY_TEST_MODE="1", RELAY_TEST_NOW=NOW)
    assert run_cli(["init", "--relay-root", root], cwd=tmp_path, env=env).returncode == 0
    weak = base_payload("conv_260130_weak", "weak fidelity")
    weak.pop("resume")
    weak.pop("user_instructions")
    created = run_cli(["upsert", "--stdin", "--relay-root", root], cwd=tmp_path, env=env, input=json.dumps(weak))
    assert created.returncode == 0, created.stderr
    record = next((root / "convs").glob("*.md"))
    before = record.read_bytes()

    doctor = run_cli(["doctor", "--relay-root", root], cwd=tmp_path, env=env)
    assert doctor.returncode == 0, doctor.stderr
    warnings = [warning for warning in load_json(doctor)["warnings"] if "fidelity" in warning]
    assert len(warnings) == 1
    assert warnings[0]["fidelity"] <= 2
    missing_order = ["resume-goal", "next-step", "dict-entry", "user-instructions", "transcript-entries"]
    assert warnings[0]["missing"] == [item for item in missing_order if item in warnings[0]["missing"]]

    fixed = run_cli(["doctor", "--fix", "--relay-root", root], cwd=tmp_path, env=env)
    assert fixed.returncode == 0, fixed.stderr
    assert record.read_bytes() == before
