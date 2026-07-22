"""Focused installer artifact contracts."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


from _util import clean_env


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL = REPO_ROOT / "scripts" / "install.py"
BASE_SKILL = "relay"


def run_install(target: Path, *extra: object) -> subprocess.CompletedProcess:
    env = clean_env(home=target.parent / f"{target.name}-agent-home")
    env["RUSTUP_HOME"] = str(Path.home() / ".rustup")
    env["CARGO_HOME"] = str(Path.home() / ".cargo")
    return subprocess.run(
        [sys.executable, str(INSTALL), "--target", str(target), *map(str, extra)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def json_hook_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for groups in data.get("hooks", {}).values():
        for group in groups:
            out.extend(group.get("hooks", []))
    return out


def plugin_skill_dirs(root: Path) -> list[str]:
    return sorted(path.name for path in (root / "skills").iterdir() if path.is_dir())


def source_conv_skill_names(plugin_root: Path) -> list[str]:
    return plugin_skill_dirs(plugin_root)


def source_conv_verbs(plugin_root: Path) -> set[str]:
    return set(source_conv_skill_names(plugin_root)) - {BASE_SKILL}


def conv_verbs_in_text(text: str) -> set[str]:
    return set(re.findall(r"\brelay:([a-z][a-z0-9_-]*)\b", text))


def plain_verb_inventory_from_description(manifest: dict, label: str) -> set[str]:
    description = manifest.get("description")
    assert isinstance(description, str), f"{label} has no description"
    match = re.search(r":\s*(.*?)\s+topic-bound records\b", description)
    assert match, f"{label} description has no structured verb inventory"
    inventory = match.group(1).replace(", and ", ", ").replace(" and ", ", ")
    return {item.strip() for item in inventory.split(",") if item.strip()}



def hook_state_dir(home: Path) -> Path:
    return home / ".relay" / ".semble" / "hook-state"


def hook_counter_path(session: str, home: Path) -> Path:
    value = 0
    for byte in session.encode("utf-8"):
        value = (value * 131 + byte) & ((1 << 64) - 1)
    return hook_state_dir(home) / f"relay-hook-{value}.count"


def hook_lock_path(session: str, home: Path) -> Path:
    return hook_counter_path(session, home).with_suffix(".lock")

def test_install_creates_conversation_database_without_clobbering_existing_records(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    record = root / "convs" / "2026-07-04_keep.md"
    nested_record = root / "convs" / "nested" / "2026-07-04_nested.md"
    record.parent.mkdir(parents=True)
    nested_record.parent.mkdir()

    before = b"existing conversation bytes\r\n"
    nested_before = b"nested record bytes\n"
    record.write_bytes(before)
    nested_record.write_bytes(nested_before)

    proc = run_install(root)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (root / "convs").is_dir()
    assert f"relay_archive = {(root / 'convs').resolve()}" in proc.stdout.splitlines()
    assert f"conversation_database = {(root / 'convs').resolve()}" in proc.stdout.splitlines()
    assert record.read_bytes() == before
    assert nested_record.read_bytes() == nested_before


def test_update_and_force_refresh_artifacts_without_touching_conversation_records(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    first = run_install(root)
    assert first.returncode == 0, first.stderr + first.stdout

    record = root / "convs" / "2026-07-04_keep.md"
    before = b"\x00conversation record bytes\xff\n"
    record.write_bytes(before)
    installed_reference = root / "references" / "save.md"

    installed_reference.write_text("STALE INSTALLED REFERENCE\n", encoding="utf-8")
    updated = run_install(root, "--update")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    assert record.read_bytes() == before

    installed_reference.write_text("STALE INSTALLED REFERENCE AGAIN\n", encoding="utf-8")
    forced = run_install(root, "--force")
    assert forced.returncode == 0, forced.stderr + forced.stdout
    assert record.read_bytes() == before


def test_existing_file_target_fails_cleanly_without_partial_artifacts(tmp_path: Path) -> None:
    target = tmp_path / "plugin-root"
    target.write_text("not a directory\n", encoding="utf-8")

    proc = run_install(target)

    assert proc.returncode == 2
    assert "not a directory" in proc.stderr
    assert target.read_text(encoding="utf-8") == "not a directory\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["plugin-root"]


def hook_environment(home: Path, temp_dir: Path) -> dict[str, str]:
    return clean_env(home=home, TMP=temp_dir, TEMP=temp_dir, TMPDIR=temp_dir)


def run_installed_hook(
    root: Path, project: Path, payload: str, env: dict[str, str], agent: str = "codex"
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(installed_binary(root)), "hook", "--agent", agent],
        input=payload,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )


def assert_hook_no_error(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stderr == "", proc.stderr


def test_installed_codex_hook_ignores_malformed_and_oversized_payloads(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout
    project = tmp_path / "project"
    project.mkdir()
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    session = f"malformed-and-oversized-{tmp_path.resolve()}"
    env = hook_environment(hook_home, hook_temp)

    ignored_payloads = [
        "not-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"hook_event_name": "SessionStart", "session_id": session, "cwd": str(project)}),
    ]
    oversized = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session,
            "padding": "x" * (1024 * 1024),
        }
    )
    assert len(oversized.encode("utf-8")) > 1024 * 1024
    ignored_payloads.append(oversized)

    for payload in ignored_payloads:
        proc = run_installed_hook(root, project, payload, env)
        assert_hook_no_error(proc)
        assert proc.stdout == "", proc.stdout
    assert not hook_counter_path(session, hook_home).exists()
    assert not hook_lock_path(session, hook_home).exists()


def test_installed_codex_hook_reminds_once_on_tenth_turn(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout
    project = tmp_path / "project"
    project.mkdir()
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    session = f"tenth-turn-{tmp_path.resolve()}"
    env = hook_environment(hook_home, hook_temp)
    payload = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": str(project)}
    )

    outputs = []
    for _ in range(10):
        proc = run_installed_hook(root, project, payload, env)
        assert_hook_no_error(proc)
        outputs.append(proc.stdout)

    reminder_count = sum(output.count("RELAY HANDOFF") for output in outputs)
    assert reminder_count == 1
    assert outputs[:9] == [""] * 9
    assert outputs[9].count("RELAY HANDOFF") == 1
    assert hook_counter_path(session, hook_home).read_text(encoding="utf-8") == "10"


def test_installed_codex_hook_parallel_turns_have_one_tenth_turn_reminder(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout
    project = tmp_path / "project"
    project.mkdir()
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    session = f"parallel-turns-{tmp_path.resolve()}"
    env = hook_environment(hook_home, hook_temp)
    payload = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": str(project)}
    )

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(lambda _: run_installed_hook(root, project, payload, env), range(10))
        )

    for proc in results:
        assert_hook_no_error(proc)
    reminder_count = sum(proc.stdout.count("RELAY HANDOFF") for proc in results)
    assert reminder_count == 1
    assert hook_counter_path(session, hook_home).read_text(encoding="utf-8") == "10"


def installed_binary(root: Path) -> Path:
    return root / "bin" / ("relay.exe" if os.name == "nt" else "relay")


def test_codex_hook_counts_without_cwd_local_relay_marker(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout
    project = tmp_path / "project-without-marker"
    project.mkdir()
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    env = hook_environment(hook_home, hook_temp)
    payload = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": f"cwd-marker-free-{tmp_path.resolve()}",
            "cwd": str(project),
        }
    )
    for _ in range(9):
        proc = run_installed_hook(root, project, payload, env)
        assert_hook_no_error(proc)
        assert proc.stdout == "", proc.stdout
    threshold = run_installed_hook(root, project, payload, env)
    assert_hook_no_error(threshold)
    assert "RELAY HANDOFF" in threshold.stdout


def test_codex_hook_ignores_malformed_non_object_and_non_prompt_payloads(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout
    project = tmp_path / "project"
    project.mkdir()
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    env = hook_environment(hook_home, hook_temp)

    def run_hook(payload: str) -> subprocess.CompletedProcess:
        return run_installed_hook(root, project, payload, env)

    for payload in (
        "not-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"hook_event_name": "SessionStart", "cwd": str(project)}),
    ):
        proc = run_hook(payload)
        assert_hook_no_error(proc)
        assert proc.stdout == "", proc.stdout
    assert not list(hook_state_dir(hook_home).glob("*.count"))
    assert not list(hook_state_dir(hook_home).glob("*.lock"))


def test_claude_hook_ignores_malformed_non_object_missing_session_and_non_prompt_payloads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    project = tmp_path / "project"
    project.mkdir()
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    env = hook_environment(hook_home, hook_temp)
    ignored_payloads = [
        "not-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(project)}),
        json.dumps({"hook_event_name": "SessionStart", "session_id": "session", "cwd": str(project)}),
    ]
    for payload in ignored_payloads:
        proc = run_installed_hook(root, project, payload, env, agent="claude")
        assert_hook_no_error(proc)
        assert proc.stdout == "", proc.stdout
    assert not list(hook_state_dir(hook_home).glob("*.count"))
    assert not list(hook_state_dir(hook_home).glob("*.lock"))


def test_claude_binary_accepts_valid_prompt_input(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    payload = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": f"claude-{tmp_path.resolve()}",
            "cwd": str(tmp_path),
        }
    )
    env = hook_environment(hook_home, hook_temp)
    for _ in range(9):
        proc = run_installed_hook(root, tmp_path, payload, env, agent="claude")
        assert_hook_no_error(proc)
        assert proc.stdout == "", proc.stdout
    threshold = run_installed_hook(root, tmp_path, payload, env, agent="claude")
    assert_hook_no_error(threshold)
    assert "RELAY HANDOFF" in threshold.stdout
def test_claude_binary_corrupt_counter_resets_to_current_prompt(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    hook_temp = tmp_path / "hook-temp"
    hook_temp.mkdir()
    hook_home = tmp_path / "hook-home"
    hook_home.mkdir()
    session = f"corrupt-claude-counter-{tmp_path.resolve()}"
    counter = hook_counter_path(session, hook_home)
    counter.parent.mkdir(parents=True)
    counter.write_text("not-an-int\n", encoding="utf-8")
    payload = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": str(tmp_path)}
    )
    env = hook_environment(hook_home, hook_temp)
    first = run_installed_hook(root, tmp_path, payload, env, agent="claude")
    assert_hook_no_error(first)
    assert first.stdout == "", first.stdout
    assert counter.read_text(encoding="utf-8").strip() == "1"
    for _ in range(8):
        proc = run_installed_hook(root, tmp_path, payload, env, agent="claude")
        assert_hook_no_error(proc)
        assert proc.stdout == "", proc.stdout
    threshold = run_installed_hook(root, tmp_path, payload, env, agent="claude")
    assert_hook_no_error(threshold)
    assert "RELAY HANDOFF" in threshold.stdout

def test_moorage_install_artifacts_are_not_shipped() -> None:
    removed_artifacts = (
        "moorage.toml",
        "hooks/claude/moorage-snippet.json",
        "hooks/codex/moorage-hooks.json",
    )
    for rel in removed_artifacts:
        assert not (REPO_ROOT / rel).exists(), f"{rel} should not be a supported install artifact"

def test_agent_plugin_metadata_exposes_same_conv_verbs_as_shipped_plugin_skills() -> None:
    plugin_root = REPO_ROOT
    claude_manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    source_skills = source_conv_skill_names(plugin_root)
    source_verbs = source_conv_verbs(plugin_root)
    assert BASE_SKILL in source_skills
    assert source_verbs
    assert codex_manifest["skills"] == "./skills/"

    codex_interface = codex_manifest["interface"]
    surfaces = {
        "Codex manifest description": plain_verb_inventory_from_description(codex_manifest, "Codex manifest"),
        "Codex manifest longDescription": conv_verbs_in_text(codex_interface["longDescription"]),
        "Claude manifest description": plain_verb_inventory_from_description(claude_manifest, "Claude manifest"),
        "README plugin inventory": conv_verbs_in_text((REPO_ROOT / "README.md").read_text(encoding="utf-8")),
        "root skill routing": conv_verbs_in_text((REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")),
        "plugin skill routing": conv_verbs_in_text((plugin_root / "SKILL.md").read_text(encoding="utf-8")),
        "plugin base skill routing": conv_verbs_in_text(
            (plugin_root / "skills" / BASE_SKILL / "SKILL.md").read_text(encoding="utf-8")
        ),
    }
    for label, verbs in surfaces.items():
        assert verbs == source_verbs, f"{label} exposes {sorted(verbs)} but source skills are {sorted(source_verbs)}"


def test_static_codex_hook_templates_are_binary_commands() -> None:
    entries = json_hook_entries(REPO_ROOT / "hooks/codex/hooks.json")
    commands = [entry.get(name, "") for entry in entries for name in ("command", "commandWindows")]
    assert commands
    assert all("__RELAY_BINARY" in command for command in commands)
    assert all("hook --agent codex" in command for command in commands)
    assert all("python" not in command.lower() for command in commands)


def test_static_claude_hook_templates_are_binary_exec_commands() -> None:
    entries = json_hook_entries(REPO_ROOT / "hooks/claude/settings-snippet.json")
    owned = [entry for entry in entries if entry.get("x-installed-by") == "relay"]
    assert len(owned) == 1
    assert owned[0]["command"] == "__RELAY_BINARY__"
    assert owned[0]["args"] == ["hook", "--agent", "claude"]


def test_update_prunes_stale_installer_owned_plugin_files_but_preserves_records(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    first = run_install(root)
    assert first.returncode == 0, first.stderr + first.stdout

    record = root / "convs" / "2026-07-04_keep.md"
    before = b"conversation bytes survive plugin refresh\n"
    record.write_bytes(before)

    stale_plugin_file = root / "relay" / "skills" / "obsolete" / "SKILL.md"
    stale_plugin_file.parent.mkdir(parents=True)
    stale_plugin_file.write_text("---\nname: obsolete\n---\n", encoding="utf-8")
    stale_cache = root / "relay" / "hooks" / "codex" / "__pycache__" / "old.pyc"
    stale_cache.parent.mkdir(parents=True)
    stale_cache.write_bytes(b"cache")

    updated = run_install(root, "--update")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    assert record.read_bytes() == before
    assert not stale_plugin_file.exists()
    assert not stale_cache.exists()
