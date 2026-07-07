"""Focused installer artifact contracts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from _util import clean_env


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL = REPO_ROOT / "scripts" / "install.py"
BASE_SKILL = "conversate"


def run_install(target: Path, *extra: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALL), "--target", str(target), *map(str, extra)],
        cwd=str(REPO_ROOT),
        env=clean_env(home=target.parent / f"{target.name}-agent-home"),
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
    return set(re.findall(r"\bconv:([a-z][a-z0-9_-]*)\b", text))


def plain_verb_inventory_from_description(manifest: dict, label: str) -> set[str]:
    description = manifest.get("description")
    assert isinstance(description, str), f"{label} has no description"
    match = re.search(r":\s*(.*?)\s+topic-bound records\b", description)
    assert match, f"{label} description has no structured verb inventory"
    inventory = match.group(1).replace(", and ", ", ").replace(" and ", ", ")
    return {item.strip() for item in inventory.split(",") if item.strip()}


def normalized_template_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix in {".json", ".md", ".ps1", ".py", ".ts"}:
        return data.replace(b"\r\n", b"\n")
    return data


def claude_hook_command(script: Path) -> list[str]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is not available")
    command = [shell, "-NoProfile"]
    if os.name == "nt":
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(["-File", str(script)])
    return command


def claude_counter_path(session: str) -> Path:
    safe = hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"conversate-claude-turns-{safe}.count"


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


def test_codex_hook_counts_without_cwd_local_conversate_marker(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout

    project = tmp_path / "project-without-marker"
    project.mkdir()
    hook = root / "hooks" / "codex" / "conv_turn_counter.py"
    session_id = f"cwd-marker-free-{tmp_path.name}"
    stdout = ""
    for _ in range(10):
        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session_id, "cwd": str(project)}),
            cwd=str(project),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        stdout = proc.stdout

    assert "CONV AUTO-SAVE" in stdout


def test_codex_hook_ignores_malformed_non_object_and_non_prompt_payloads(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root, "--hooks", "codex")
    assert installed.returncode == 0, installed.stderr + installed.stdout

    project = tmp_path / "project"
    project.mkdir()
    hook = root / "hooks" / "codex" / "conv_turn_counter.py"

    def run_hook(payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(hook)],
            input=payload,
            cwd=str(project),
            capture_output=True,
            text=True,
        )

    ignored_payloads = [
        "not-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"hook_event_name": "SessionStart", "cwd": str(project)}),
    ] * 3
    for payload in ignored_payloads:
        proc = run_hook(payload)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert proc.stdout == ""

    valid = json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(project)})
    for _ in range(9):
        proc = run_hook(valid)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert proc.stdout == ""
    threshold = run_hook(valid)
    assert threshold.returncode == 0, threshold.stderr + threshold.stdout
    assert "CONV AUTO-SAVE" in threshold.stdout


def test_claude_hook_ignores_malformed_non_object_missing_session_and_non_prompt_payloads(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root)
    assert installed.returncode == 0, installed.stderr + installed.stdout

    project = tmp_path / "project"
    project.mkdir()
    script = root / "hooks" / "claude" / "conv-turn-counter.ps1"
    command = claude_hook_command(script)
    session = f"ignored-claude-payloads-{tmp_path.name}"
    session_counter = claude_counter_path(session)
    cwd_counter = claude_counter_path(f"cwd-{project}")
    for counter in (session_counter, cwd_counter):
        try:
            counter.unlink()
        except OSError:
            pass

    def run_hook(payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(command, input=payload, cwd=str(project), capture_output=True, text=True)

    try:
        ignored_payloads = [
            "not-json",
            json.dumps(["not", "an", "object"]),
            json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(project)}),
            json.dumps({"hook_event_name": "SessionStart", "session_id": session, "cwd": str(project)}),
        ] * 3
        for payload in ignored_payloads:
            proc = run_hook(payload)
            assert proc.returncode == 0, proc.stderr + proc.stdout
            assert proc.stdout == ""

        assert not session_counter.exists()
        assert not cwd_counter.exists()

        valid = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": str(project)})
        for _ in range(9):
            proc = run_hook(valid)
            assert proc.returncode == 0, proc.stderr + proc.stdout
            assert proc.stdout == ""
        threshold = run_hook(valid)
        assert threshold.returncode == 0, threshold.stderr + threshold.stdout
        assert "CONV AUTO-SAVE" in threshold.stdout
    finally:
        for counter in (session_counter, cwd_counter):
            try:
                counter.unlink()
            except OSError:
                pass


def test_claude_hook_corrupt_counter_resets_to_current_prompt(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    installed = run_install(root)
    assert installed.returncode == 0, installed.stderr + installed.stdout

    script = root / "hooks" / "claude" / "conv-turn-counter.ps1"
    session = f"corrupt-claude-counter-{tmp_path.name}"
    counter = claude_counter_path(session)
    counter.write_text("not-an-int\n", encoding="utf-8")
    command = claude_hook_command(script)

    try:
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": str(tmp_path)})
        first = subprocess.run(command, input=payload, capture_output=True, text=True)
        assert first.returncode == 0, first.stderr + first.stdout
        assert first.stdout == ""
        assert counter.read_text(encoding="utf-8").strip() == "1"

        stdout = ""
        for _ in range(9):
            proc = subprocess.run(command, input=payload, capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr + proc.stdout
            stdout = proc.stdout
        assert "CONV AUTO-SAVE" in stdout
        assert counter.read_text(encoding="utf-8").strip() == "10"
    finally:
        try:
            counter.unlink()
        except OSError:
            pass


def test_hook_scripts_do_not_gate_on_cwd_local_conversate_marker() -> None:
    hook_files = [
        REPO_ROOT / "hooks" / "claude" / "conv-turn-counter.ps1",
        REPO_ROOT / "hooks" / "codex" / "conv_turn_counter.py",
        REPO_ROOT / "hooks" / "pi" / "conv-turn-counter.ts",
    ]
    forbidden = (
        "local compatibility marker",
        "process.cwd(), \".conversate\"",
        "Path(cwd) / \".conversate\"",
        "Join-Path $cwd '.conversate'",
    )
    for path in hook_files:
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{path.relative_to(REPO_ROOT)} still gates on {phrase!r}"


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


def test_static_codex_hook_templates_are_template_only_commands() -> None:
    runtime_interpreter = re.compile(r'^\s*"?(?:python3?|py(?:\s+-3)?)(?:"|\s|$)', re.IGNORECASE)
    for rel in (
        "hooks/codex/hooks.json",
    ):
        path = REPO_ROOT / rel
        entries = json_hook_entries(path)
        commands = [
            entry.get(name, "")
            for entry in entries
            for name in ("command", "commandWindows")
            if "conv_turn_counter" in entry.get(name, "")
        ]
        assert commands, f"{rel} has no Codex counter command"
        for command in commands:
            assert "__CONVERSATE_" in command, f"{rel} has an installable-looking command: {command}"
            assert not runtime_interpreter.match(command), f"{rel} ships a runtime interpreter command: {command}"
        assert any("__CONVERSATE_VERIFIED_INTERPRETER__" in command for command in commands)
        assert any("__CONVERSATE_VERIFIED_INTERPRETER_WINDOWS__" in command for command in commands)


def test_static_claude_hook_templates_keep_no_profile() -> None:
    for rel in (
        "hooks/claude/settings-snippet.json",
    ):
        commands = [
            entry.get("command", "")
            for entry in json_hook_entries(REPO_ROOT / rel)
            if "conv-turn-counter" in entry.get("command", "")
        ]
        assert commands, f"{rel} has no Claude counter command"
        assert all(" -NoProfile " in f" {command} " for command in commands)


def test_update_prunes_stale_installer_owned_plugin_files_but_preserves_records(tmp_path: Path) -> None:
    root = tmp_path / "plugin-root"
    first = run_install(root)
    assert first.returncode == 0, first.stderr + first.stdout

    record = root / "convs" / "2026-07-04_keep.md"
    before = b"conversation bytes survive plugin refresh\n"
    record.write_bytes(before)

    stale_plugin_file = root / "conv" / "skills" / "obsolete" / "SKILL.md"
    stale_plugin_file.parent.mkdir(parents=True)
    stale_plugin_file.write_text("---\nname: obsolete\n---\n", encoding="utf-8")
    stale_cache = root / "conv" / "hooks" / "codex" / "__pycache__" / "old.pyc"
    stale_cache.parent.mkdir(parents=True)
    stale_cache.write_bytes(b"cache")

    updated = run_install(root, "--update")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    assert record.read_bytes() == before
    assert not stale_plugin_file.exists()
    assert not stale_cache.exists()
