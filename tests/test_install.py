"""Black-box tests for scripts/install.py.

Mirrors tests/_util.py: drives the real installer via subprocess in a cleaned env
against pytest tmp_path targets, so it exercises exactly what a user runs. Asserts
on installer-owned artifacts (plugin files, plugin skill groups, hook files) which stay
stable while the Rust CLI implementation evolves. The Relay archive layout is
checked without shelling through engine internals.

Run: python -m pytest tests/test_install.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Reuse the shared cleaned-env helper without importing engine specifics. Insert the
# tests dir on sys.path so `_util` resolves regardless of pytest's import mode.
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _util import clean_env  # noqa: E402  (do not modify _util.py)

REPO_ROOT = TESTS_DIR.parent
INSTALL = REPO_ROOT / "scripts" / "install.py"
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import install as install_mod  # noqa: E402

LEGACY_CLAUDE_LINK = (".claude", "skills", "conversate")
LEGACY_AGENTS_LINK = (".agents", "skills", "conversate")
CANONICAL_PLUGIN = ("relay",)
# Prior canonical installed plugin root (<root>/conv); retained as a preserved legacy alias.
LEGACY_CANONICAL_PLUGIN = ("conv",)
LEGACY_CLAUDE_PLUGIN = (".claude", "skills", "conv")
LEGACY_AGENTS_PLUGIN = (".agents", "skills", "conv")
PI_HOOK = (".pi", "agent", "extensions", "relay-turn-counter.ts")
OMP_HOOK = (".omp", "hooks", "pre", "relay-turn-counter.ts")
# The shared conv plugin skill group.
RELAY_SKILLS = ("relay", "save", "resume", "list", "park", "sidekick", "return", "continue", "regen")


def run_install(args, cwd=None, env=None) -> subprocess.CompletedProcess:
    if env is None:
        env = clean_env()
    env["RUSTUP_HOME"] = str(Path.home() / ".rustup")
    env["CARGO_HOME"] = str(Path.home() / ".cargo")
    if cwd is None:
        cwd = REPO_ROOT
    return subprocess.run(
        [sys.executable, str(INSTALL), *map(str, args)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def run_generated_hook(command: str, *, input_text: str, cwd: Path) -> subprocess.CompletedProcess:
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            pytest.skip("PowerShell is not available to replay a generated Windows hook command")
        invocation: str | list[str] = [
            powershell, "-NoProfile", "-NonInteractive", "-Command", command
        ]
        shell = False
    else:
        invocation = command
        shell = True
    return subprocess.run(
        invocation,
        shell=shell,
        input=input_text,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def run_generated_exec(
    command: str, args: list[str], *, input_text: str, cwd: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [command, *args],
        input=input_text,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def install_into(target: Path, *extra, home: Path | None = None) -> subprocess.CompletedProcess:
    if home is None:
        home = agent_home_for(target)
    return run_install(["--target", str(target), *extra], env=clean_env(home=home))


def install_default(home: Path, *extra) -> subprocess.CompletedProcess:
    return run_install([*extra], env=clean_env(home=home))


def json_hook_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for groups in data["hooks"].values():
        for group in groups:
            out.extend(group.get("hooks", []))
    return out


def agent_home_for(root: Path) -> Path:
    return root.parent / f"{root.name}-agent-home"


def codex_home_for(root: Path) -> Path:
    return agent_home_for(root) / ".codex"


def claude_home_for(root: Path) -> Path:
    return agent_home_for(root) / ".claude"


def codex_hooks_path(root: Path) -> Path:
    return codex_home_for(root) / "hooks.json"


def claude_settings_path(root: Path) -> Path:
    return claude_home_for(root) / "settings.json"


def pi_hooks_path(root: Path, *, home: Path | None = None) -> Path:
    return (home if home is not None else agent_home_for(root)).joinpath(*PI_HOOK)


def omp_hooks_path(root: Path, *, home: Path | None = None) -> Path:
    return (home if home is not None else agent_home_for(root)).joinpath(*OMP_HOOK)


def raw_hook_path(root: Path, hook_name: str, parts: tuple[str, ...]) -> Path:
    if hook_name == "pi":
        return pi_hooks_path(root)
    if hook_name == "omp":
        return omp_hooks_path(root)
    return root.joinpath(*parts)


def codex_hook_entries(root: Path, *, home: Path | None = None) -> list[dict]:
    codex_home = (home / ".codex") if home is not None else codex_home_for(root)
    return json_hook_entries(codex_home / "hooks.json")


def relay_codex_hook(root: Path, *, home: Path | None = None) -> dict:
    return next(
        entry for entry in codex_hook_entries(root, home=home)
        if entry.get("x-installed-by") == "relay"
    )


def claude_hook_entries(root: Path, *, home: Path | None = None) -> list[dict]:
    claude_home = (home / ".claude") if home is not None else claude_home_for(root)
    return json_hook_entries(claude_home / "settings.json")


def bak_files(root: Path) -> list[str]:
    # os.walk does not follow any old symlinked skill links, so it will not descend into
    # the Plugin installation root via a legacy link; the root is walked directly.
    found: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        found += [os.path.join(dirpath, f) for f in files if ".bak" in f]
    return found


def stale_codex_hook(root: Path, *, home: Path | None = None) -> None:
    dest = (home / ".codex" / "hooks.json") if home is not None else codex_hooks_path(root)
    data = json.loads(dest.read_text(encoding="utf-8"))
    hook = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    hook["command"] = "relay-stale/bin/relay hook --agent codex"
    hook["commandWindows"] = "& 'relay-stale/bin/relay.exe' 'hook' '--agent' 'codex'"
    dest.write_text(json.dumps(data), encoding="utf-8")


def stale_claude_hook(root: Path, *, home: Path | None = None) -> None:
    dest = (home / ".claude" / "settings.json") if home is not None else claude_settings_path(root)
    data = json.loads(dest.read_text(encoding="utf-8"))
    hook = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    hook["command"] = "relay-stale/bin/relay.exe"
    hook["args"] = ["hook", "--agent", "claude"]
    dest.write_text(json.dumps(data), encoding="utf-8")


def write_stale_owned_raw_hook(path: Path) -> None:
    path.write_text("relay stale relay-turn-counter hook\n", encoding="utf-8")


def assert_stale_hook_targets_removed(command: str) -> None:
    for target in (
        "stale/relay_turn_counter.py",
        "stale\\relay_turn_counter.py",
        "stale/relay-turn-counter.ps1",
        "stale\\relay-turn-counter.ps1",
        "relay-stale/bin/relay",
        "relay-stale/bin/relay.exe",
    ):
        assert target not in command


def replace_file_with_directory(path: Path) -> None:
    path.unlink()
    path.mkdir()
    (path / "stray.txt").write_text("directory where installer expects a file\n", encoding="utf-8")


def assert_installed(target: Path) -> None:
    conv = target
    # plugin files (installer-owned)
    assert (conv / "SKILL.md").is_file()
    binary = conv / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert binary.is_file()
    assert (conv / "scripts" / "install.py").is_file()
    assert (conv / "references").is_dir()
    assert (conv / "hooks").is_dir()
    assert not (conv / ".relay-repair-source").exists()
    # Relay archive
    assert (conv / "convs").is_dir()
    assert (conv / "index.jsonl").exists()
    assert (conv / ".semble").is_dir()
    assert (conv / ".gitignore").is_file()
    assert not (conv / ".conv-root").exists()
    # shared plugin skill group
    plugin = target.joinpath(*CANONICAL_PLUGIN)
    assert plugin.is_dir(), f"missing plugin {plugin}"
    assert (plugin / "SKILL.md").is_file()
    assert (plugin / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin / ".codex-plugin" / "plugin.json").is_file()
    for skill in RELAY_SKILLS:
        assert (plugin / "skills" / skill / "SKILL.md").is_file()
    # legacy nested plugin/link paths must not be created on fresh install
    for parts in (LEGACY_CLAUDE_LINK, LEGACY_AGENTS_LINK):
        assert not os.path.lexists(target.joinpath(*parts))
    for parts in (LEGACY_CANONICAL_PLUGIN, LEGACY_CLAUDE_PLUGIN, LEGACY_AGENTS_PLUGIN):
        assert not os.path.lexists(target.joinpath(*parts))
    assert not os.path.lexists(target / ".codex")
    assert not os.path.lexists(target / ".claude")


def test_fresh_install_creates_plugin_root_database_and_plugins(tmp_path):
    proc = install_into(tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert_installed(tmp_path)


def test_default_install_uses_home_plugin_installation_root(tmp_path):
    home = tmp_path / "home"
    root = home / ".relay"
    proc = install_default(home)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert f"plugin_installation_root = {root.resolve()}" in proc.stdout.splitlines()
    assert f"conversation_database = {(root / 'convs').resolve()}" in proc.stdout.splitlines()
    assert_installed(root)
    assert not (tmp_path / ".relay").exists()


def test_target_is_plugin_installation_root_itself_not_parent(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "chosen-plugin-root"
    proc = install_into(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert_installed(root)
    assert not (root / ".relay").exists()
    assert not (parent / ".relay").exists()


def test_installer_never_touches_legacy_conversate_data_plugins_or_conv_aliases(tmp_path):
    home = tmp_path / "home"
    legacy_root = home / ".conversate"
    legacy_record = legacy_root / "convs" / "2026-07-04_legacy.md"
    legacy_record.parent.mkdir(parents=True)
    legacy_record.write_bytes(b"legacy record bytes\r\n")
    legacy_plugin = home / ".claude" / "skills" / "conversate" / "SKILL.md"
    legacy_plugin.parent.mkdir(parents=True)
    legacy_plugin.write_bytes(b"legacy Conversate plugin\n")
    legacy_alias = home / ".codex" / "skills" / "conv" / "SKILL.md"
    legacy_alias.parent.mkdir(parents=True)
    legacy_alias.write_bytes(b"legacy conv alias\n")
    legacy_hook_config = home / ".codex" / "hooks.json"
    legacy_hook_config.write_bytes(
        b'{"hooks":{"UserPromptSubmit":[{"hooks":[{"command":"conversate hook"}]}]}}\n'
    )
    legacy_claude_config = home / ".claude" / "settings.json"
    legacy_claude_config.write_bytes(
        b'{"hooks":{"UserPromptSubmit":[{"hooks":[{"command":"conversate hook"}]}]}}\n'
    )
    expected = {
        legacy_record: legacy_record.read_bytes(),
        legacy_plugin: legacy_plugin.read_bytes(),
        legacy_alias: legacy_alias.read_bytes(),
        legacy_hook_config: legacy_hook_config.read_bytes(),
        legacy_claude_config: legacy_claude_config.read_bytes(),
    }

    for args in ((), ("--update",), ("--repair", "--hooks", "none"), ("--uninstall",)):
        proc = install_default(home, *args)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        for path, contents in expected.items():
            assert path.read_bytes() == contents


def test_installer_refuses_the_legacy_conversate_archive_as_target(tmp_path):
    home = tmp_path / "home"
    legacy_root = home / ".conversate"
    legacy_root.mkdir(parents=True)
    sentinel = legacy_root / "convs" / "2026-07-04_legacy.md"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"legacy archive must remain untouched\n")

    proc = run_install(["--target", str(legacy_root)], env=clean_env(home=home))
    assert proc.returncode == 2
    assert "refusing to use the legacy Conversate archive" in proc.stderr
    assert sentinel.read_bytes() == b"legacy archive must remain untouched\n"


def test_rerun_is_idempotent_no_bak_spam(tmp_path):
    first = install_into(tmp_path)
    assert first.returncode == 0, first.stderr
    second = install_into(tmp_path)
    assert second.returncode == 0, second.stderr
    # a plain re-run has no plugin-file conflicts, so it must
    # not create any backup files.
    assert bak_files(tmp_path) == []
    assert "plugin files: 0 created" in second.stdout
    assert_installed(tmp_path)


def test_status_reports_present_on_installed(tmp_path):
    assert install_into(tmp_path).returncode == 0
    proc = install_into(tmp_path, "--status")
    assert proc.returncode == 0, proc.stderr
    assert f"universal_installation_root = {tmp_path}" in proc.stdout.splitlines()
    assert f"plugin_installation_root = {tmp_path}" in proc.stdout.splitlines()
    assert f"canonical_plugin_root = {tmp_path / 'relay'}" in proc.stdout.splitlines()
    assert f"canonical_hook_root = {tmp_path / 'hooks'}" in proc.stdout.splitlines()
    assert f"conversation_database = {tmp_path / 'convs'}" in proc.stdout.splitlines()
    assert "plugin files: present" in proc.stdout
    assert "Relay archive: present" in proc.stdout
    assert "canonical plugin: present (9 skills)" in proc.stdout
    assert "legacy claude plugin .claude/skills/conv: absent" in proc.stdout
    assert "legacy agents plugin .agents/skills/conv: absent" in proc.stdout
    assert "older canonical plugin conv: absent" in proc.stdout


def test_status_reports_missing_on_empty(tmp_path):
    proc = install_into(tmp_path, "--status")
    assert proc.returncode == 0, proc.stderr
    assert "plugin files: missing" in proc.stdout
    assert "Relay archive: missing" in proc.stdout
    assert "missing" in proc.stdout  # links reported missing too


def test_real_codex_and_claude_homes_get_entrypoints_and_hook_config(tmp_path):
    root = tmp_path / "universal"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    claude_home = home / ".claude"
    canonical_plugin = root / "relay"
    canonical_hooks = root / "hooks"

    proc = install_into(root, "--agents", "codex,claude", "--hooks", "codex,claude", home=home)
    assert proc.returncode == 0, proc.stderr + proc.stdout

    codex_entrypoint = codex_home / "skills" / "relay"
    claude_entrypoint = claude_home / "skills" / "relay"
    assert os.path.realpath(codex_entrypoint) == os.path.realpath(canonical_plugin)
    assert os.path.realpath(claude_entrypoint) == os.path.realpath(canonical_plugin)
    assert (codex_home / "hooks.json").is_file()
    assert (claude_home / "settings.json").is_file()
    assert not os.path.lexists(root / ".codex")
    assert not os.path.lexists(root / ".claude")

    codex_hook = relay_codex_hook(root, home=home)
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    codex_command = codex_hook["command"] + codex_hook["commandWindows"]
    assert str(binary) in codex_command
    assert "hook" in codex_command and "--agent" in codex_command and "codex" in codex_command
    claude_hook = next(
        entry for entry in claude_hook_entries(root, home=home)
        if entry.get("x-installed-by") == "relay"
    )
    assert claude_hook["command"] == str(binary)
    assert claude_hook["args"] == ["hook", "--agent", "claude"]


def test_status_and_dry_run_report_real_agent_surfaces(tmp_path):
    root = tmp_path / "universal"
    home = tmp_path / "home"
    dry_run = install_into(root, "--agents", "codex,claude", "--hooks", "codex,claude", "--dry-run", home=home)
    assert dry_run.returncode == 0, dry_run.stderr + dry_run.stdout
    assert f"codex_config_surface = {home / '.codex'}" in dry_run.stdout.splitlines()
    assert f"claude_config_surface = {home / '.claude'}" in dry_run.stdout.splitlines()
    assert not root.exists()

    installed = install_into(root, "--agents", "codex,claude", "--hooks", "codex,claude", home=home)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    status = install_into(root, "--status", home=home)
    assert status.returncode == 0, status.stderr + status.stdout
    lines = status.stdout.splitlines()
    assert f"codex_config_surface = {home / '.codex'}" in lines
    assert f"claude_config_surface = {home / '.claude'}" in lines
    assert any(line.startswith("codex entrypoint:") and "canonical plugin" in line for line in lines)
    assert any(line.startswith("claude entrypoint:") and "canonical plugin" in line for line in lines)


def test_status_reports_incomplete_canonical_payload_plugin_and_hooks(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root)
    assert first.returncode == 0, first.stderr + first.stdout

    (root / "bin" / ("relay.exe" if os.name == "nt" else "relay")).write_bytes(b"STALE BINARY")
    (root / "relay" / "skills" / "save" / "SKILL.md").unlink()
    (root / "hooks" / "codex" / "hooks.json").unlink()

    status = install_into(root, "--status")
    assert status.returncode == 0, status.stderr + status.stdout
    assert "runtime files: stale" in status.stdout
    assert "runtime files: stale 1 required artifact(s): bin/relay" in status.stdout
    assert "plugin files: missing" in status.stdout
    assert "canonical plugin artifacts: missing 1 required artifact(s): skills/save/SKILL.md" in status.stdout
    assert "canonical hooks: missing" in status.stdout
    assert "canonical hook artifacts: missing 1 required artifact(s): codex/hooks.json" in status.stdout


def make_stale_nested_install_surfaces(root: Path, home: Path) -> None:
    stale_agents = root.joinpath(*LEGACY_AGENTS_PLUGIN)
    stale_claude = root.joinpath(*LEGACY_CLAUDE_PLUGIN)
    shutil.copytree(root.joinpath(*CANONICAL_PLUGIN), stale_agents)
    shutil.copytree(root.joinpath(*CANONICAL_PLUGIN), stale_claude)

    codex_entrypoint = home / ".codex" / "skills" / "relay"
    claude_entrypoint = home / ".claude" / "skills" / "relay"
    install_mod._remove_dir_or_link(codex_entrypoint)
    install_mod._remove_dir_or_link(claude_entrypoint)
    install_mod._create_directory_entrypoint(codex_entrypoint, stale_agents)
    install_mod._create_directory_entrypoint(claude_entrypoint, stale_claude)

    stale_codex = root / ".codex" / "hooks.json"
    stale_codex.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(home / ".codex" / "hooks.json", stale_codex)
    stale_claude_settings = root / ".claude" / "settings.json"
    stale_claude_settings.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(home / ".claude" / "settings.json", stale_claude_settings)


def test_status_reports_wrong_entrypoints_and_stale_nested_surfaces(tmp_path):
    root = tmp_path / "plugin-root"
    home = tmp_path / "home"
    first = install_into(root, "--agents", "codex,claude", "--hooks", "codex,claude", home=home)
    assert first.returncode == 0, first.stderr + first.stdout
    make_stale_nested_install_surfaces(root, home)
    codex_hooks = home / ".codex" / "hooks.json"
    codex_data = json.loads(codex_hooks.read_text(encoding="utf-8"))
    codex_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] = "python stale/relay_turn_counter.py"
    codex_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"] = "py -3 stale\\relay_turn_counter.py"
    codex_hooks.write_text(json.dumps(codex_data), encoding="utf-8")
    claude_settings = home / ".claude" / "settings.json"
    claude_data = json.loads(claude_settings.read_text(encoding="utf-8"))
    claude_hook = claude_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    claude_hook["command"] = "pwsh -File stale/relay-turn-counter.ps1"
    claude_hook.pop("args", None)
    claude_settings.write_text(json.dumps(claude_data), encoding="utf-8")

    status = install_into(root, "--status", home=home)
    assert status.returncode == 0, status.stderr + status.stdout
    assert any(line.startswith("codex entrypoint:") and "wrong target" in line for line in status.stdout.splitlines())
    assert any(line.startswith("claude entrypoint:") and "wrong target" in line for line in status.stdout.splitlines())
    assert "legacy agents plugin .agents/skills/conv: stale installer-owned copy" in status.stdout
    assert "legacy claude plugin .claude/skills/conv: stale installer-owned copy" in status.stdout
    assert "legacy nested codex hook .codex/hooks.json: stale installer-owned hook config" in status.stdout
    assert "legacy nested claude hook .claude/settings.json: stale installer-owned hook config" in status.stdout
    assert f"hook codex: wired outside canonical hook root ({codex_hooks})" in status.stdout
    assert f"hook claude: wired outside canonical hook root ({claude_settings})" in status.stdout


def test_status_distinguishes_canonical_targets_with_stale_invocation_shapes(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "codex,claude")
    assert first.returncode == 0, first.stderr + first.stdout

    codex_path = codex_hooks_path(root)
    codex_data = json.loads(codex_path.read_text(encoding="utf-8"))
    codex_hook = codex_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    command_windows = codex_hook["commandWindows"]
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    stale_binary = root / "stale" / binary.name
    codex_hook["commandWindows"] = command_windows.replace(str(binary), str(stale_binary))
    codex_path.write_text(json.dumps(codex_data), encoding="utf-8")

    claude_path = claude_settings_path(root)
    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    claude_hook = claude_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    claude_hook["command"] = claude_hook["command"] + " stale"
    claude_path.write_text(json.dumps(claude_data), encoding="utf-8")

    status = install_into(root, "--status")
    assert status.returncode == 0, status.stderr + status.stdout
    assert "hook codex: wired -> canonical hooks (stale invocation)" in status.stdout
    assert "hook claude: wired -> canonical hooks (stale invocation)" in status.stdout

    codex_hook["commandWindows"] = command_windows.removeprefix("& ")
    if os.name == "nt":
        codex_hook["command"] = codex_hook["command"].removeprefix("& ")
    codex_path.write_text(json.dumps(codex_data), encoding="utf-8")
    unprefixed_status = install_into(root, "--status")
    assert unprefixed_status.returncode == 0, unprefixed_status.stderr + unprefixed_status.stdout
    assert "hook codex: wired -> canonical hooks (stale invocation)" in unprefixed_status.stdout

    repaired = install_into(root, "--repair")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    repaired_status = install_into(root, "--status")
    assert repaired_status.returncode == 0, repaired_status.stderr + repaired_status.stdout
    assert f"hook codex: wired -> canonical hooks ({codex_path})" in repaired_status.stdout
    assert f"hook claude: wired -> canonical hooks ({claude_path})" in repaired_status.stdout
    assert "stale invocation" not in repaired_status.stdout


def test_repair_preserves_legacy_nested_surfaces_while_repairing_relay(tmp_path):
    root = tmp_path / "plugin-root"
    home = tmp_path / "home"
    first = install_into(root, "--agents", "codex,claude", "--hooks", "codex,claude", home=home)
    assert first.returncode == 0, first.stderr + first.stdout
    make_stale_nested_install_surfaces(root, home)

    record = root / "convs" / "2026-07-04_keep.md"
    record_bytes = b"conversation bytes survive nested install migration\r\n"
    record.write_bytes(record_bytes)
    nested_record = root / "convs" / "nested" / "2026-07-04_nested.md"
    nested_record.parent.mkdir()
    nested_record_bytes = b"nested conversation bytes survive too\n"
    nested_record.write_bytes(nested_record_bytes)

    stale_codex_hook(root, home=home)
    stale_claude_hook(root, home=home)

    repaired = install_into(root, "--repair", home=home)
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert f"plugin_source = {REPO_ROOT.resolve()}" in repaired.stdout.splitlines()
    assert f"universal_installation_root = {root.resolve()}" in repaired.stdout.splitlines()
    assert f"canonical_plugin_root = {(root / 'relay').resolve()}" in repaired.stdout.splitlines()
    assert f"canonical_hook_root = {(root / 'hooks').resolve()}" in repaired.stdout.splitlines()
    assert "codex: hook command changed; Codex may require hook reapproval or retrust" in repaired.stdout

    assert (root / "relay" / "SKILL.md").is_file()
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert binary.is_file()
    assert (root / "hooks" / "codex" / "hooks.json").is_file()
    assert (root / "hooks" / "claude" / "settings-snippet.json").is_file()
    assert os.path.realpath(home / ".codex" / "skills" / "relay") == os.path.realpath(root / "relay")
    assert os.path.realpath(home / ".claude" / "skills" / "relay") == os.path.realpath(root / "relay")
    codex_hook = relay_codex_hook(root, home=home)
    codex_command = codex_hook["command"] + codex_hook["commandWindows"]
    assert str(binary) in codex_command
    assert_stale_hook_targets_removed(codex_command)
    claude_hook = next(
        entry for entry in claude_hook_entries(root, home=home)
        if entry.get("x-installed-by") == "relay"
    )
    assert claude_hook["command"] == str(binary)
    assert claude_hook["args"] == ["hook", "--agent", "claude"]
    assert record.read_bytes() == record_bytes
    assert nested_record.read_bytes() == nested_record_bytes
    # Legacy aliases and legacy nested configuration are status-only evidence:
    # repair must never remove or rewrite them.
    assert root.joinpath(*LEGACY_AGENTS_PLUGIN).is_dir()
    assert root.joinpath(*LEGACY_CLAUDE_PLUGIN).is_dir()
    assert (root / ".codex" / "hooks.json").is_file()
    assert (root / ".claude" / "settings.json").is_file()

    status = install_into(root, "--status", home=home)
    assert status.returncode == 0, status.stderr + status.stdout
    assert any(line.startswith("codex entrypoint:") and "canonical plugin" in line for line in status.stdout.splitlines())
    assert any(line.startswith("claude entrypoint:") and "canonical plugin" in line for line in status.stdout.splitlines())
    assert f"hook codex: wired -> canonical hooks ({home / '.codex' / 'hooks.json'})" in status.stdout
    assert f"hook claude: wired -> canonical hooks ({home / '.claude' / 'settings.json'})" in status.stdout
    assert "legacy agents plugin .agents/skills/conv: stale installer-owned copy" in status.stdout
    assert "legacy claude plugin .claude/skills/conv: stale installer-owned copy" in status.stdout
    assert "legacy nested codex hook .codex/hooks.json: stale installer-owned hook config" in status.stdout
    assert "legacy nested claude hook .claude/settings.json: stale installer-owned hook config" in status.stdout
    assert "wrong target" not in status.stdout


def test_help_and_status_use_plugin_root_and_database_terms(tmp_path):
    help_proc = run_install(["--help"])
    assert help_proc.returncode == 0, help_proc.stderr + help_proc.stdout
    assert "Plugin installation root" in help_proc.stdout
    assert "Relay archive" in help_proc.stdout

    status = install_into(tmp_path, "--status")
    assert status.returncode == 0, status.stderr + status.stdout
    assert f"universal_installation_root = {tmp_path}" in status.stdout.splitlines()
    assert f"plugin_installation_root = {tmp_path}" in status.stdout.splitlines()
    assert f"canonical_plugin_root = {tmp_path / 'relay'}" in status.stdout.splitlines()
    assert f"canonical_hook_root = {tmp_path / 'hooks'}" in status.stdout.splitlines()
    assert f"conversation_database = {tmp_path / 'convs'}" in status.stdout.splitlines()
    assert "Relay archive:" in status.stdout
    assert "store root" not in status.stdout.lower()
    assert "conversation store" not in status.stdout.lower()


def test_dry_run_changes_nothing(tmp_path):
    proc = install_into(tmp_path, "--hooks", "all", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "would" in proc.stdout
    # nothing at all should have been created under the target
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path / "convs").exists()


def test_uninstall_removes_plugins_but_preserves_convs(tmp_path):
    assert install_into(tmp_path, "--hooks", "all").returncode == 0
    # plant a conversation record that must survive uninstall
    planted = tmp_path / "convs" / "2026-01-01_keepme.md"
    planted.write_text("PRECIOUS DATA\n", encoding="utf-8")

    proc = install_into(tmp_path, "--uninstall")
    assert proc.returncode == 0, proc.stderr

    # plugin skill groups removed
    for parts in (CANONICAL_PLUGIN, LEGACY_CLAUDE_PLUGIN, LEGACY_AGENTS_PLUGIN, LEGACY_CLAUDE_LINK, LEGACY_AGENTS_LINK):
        assert not os.path.lexists(tmp_path.joinpath(*parts))
    # hook files removed
    assert not pi_hooks_path(tmp_path).exists()
    assert not omp_hooks_path(tmp_path).exists()
    assert not os.path.lexists(tmp_path / ".omp")
    # Relay archive and the planted record remain
    assert (tmp_path / "SKILL.md").is_file()
    assert planted.is_file()
    assert planted.read_text(encoding="utf-8") == "PRECIOUS DATA\n"


def test_uninstall_dry_run_keeps_links(tmp_path):
    assert install_into(tmp_path).returncode == 0
    proc = install_into(tmp_path, "--uninstall", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert os.path.lexists(tmp_path.joinpath(*CANONICAL_PLUGIN))


def test_uninstall_dry_run_with_hooks_installed_changes_nothing(tmp_path):
    root = tmp_path / "plugin-root"
    assert install_into(root, "--hooks", "all").returncode == 0
    watched = [
        root.joinpath(*CANONICAL_PLUGIN) / "SKILL.md",
        pi_hooks_path(root),
        omp_hooks_path(root),
        codex_hooks_path(root),
        claude_settings_path(root),
    ]
    snapshots = {path: path.read_bytes() for path in watched}

    proc = install_into(root, "--uninstall", "--dry-run")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "would remove" in proc.stdout
    for path, before in snapshots.items():
        assert path.read_bytes() == before, path
    assert root.joinpath(*CANONICAL_PLUGIN).is_dir()
    assert bak_files(root) == []


def test_plugin_file_conflict_refused_then_update_recovers(tmp_path):
    assert install_into(tmp_path).returncode == 0
    save_md = tmp_path / "references" / "save.md"
    original = save_md.read_text(encoding="utf-8")
    save_md.write_text("LOCAL EDIT\n", encoding="utf-8")

    refused = install_into(tmp_path)
    assert refused.returncode == 2
    assert "save.md" in refused.stderr

    updated = install_into(tmp_path, "--update")
    assert updated.returncode == 0, updated.stderr
    assert save_md.read_text(encoding="utf-8") == original


def test_hooks_wiring_pi_and_codex(tmp_path):
    proc = install_into(tmp_path, "--hooks", "pi,codex")
    assert proc.returncode == 0, proc.stderr
    pi_hook = pi_hooks_path(tmp_path)
    codex_hooks = codex_hooks_path(tmp_path)
    assert pi_hook.is_file()
    assert codex_hooks.is_file()
    assert not (tmp_path / ".codex").exists()
    owned = relay_codex_hook(tmp_path)
    binary = tmp_path / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert owned["command"] == install_mod._codex_hook_command(binary, powershell=os.name == "nt")
    assert owned["commandWindows"] == install_mod._codex_hook_command(binary, powershell=True)
    assert owned.get("x-installed-by") == "relay"
    assert "args" not in owned and "shell" not in owned
    # codex hook was the only entry, so uninstall removes the file entirely
    assert install_into(tmp_path, "--uninstall").returncode == 0
    assert not codex_hooks.exists()


@pytest.mark.parametrize(("hook_name", "parts"), [("pi", PI_HOOK), ("omp", OMP_HOOK)])
def test_raw_hook_install_refuses_foreign_file_then_force_backs_up(tmp_path, hook_name, parts):
    dest = raw_hook_path(tmp_path, hook_name, parts)
    dest.parent.mkdir(parents=True)
    dest.write_text("foreign hook\n", encoding="utf-8")

    refused = install_into(tmp_path, "--hooks", hook_name)
    assert refused.returncode == 2
    assert "differs from the relay hook" in refused.stderr
    assert dest.read_text(encoding="utf-8") == "foreign hook\n"

    forced = install_into(tmp_path, "--hooks", hook_name, "--force")
    assert forced.returncode == 0, forced.stderr + forced.stdout
    assert dest.read_text(encoding="utf-8") != "foreign hook\n"
    assert dest.with_name(dest.name + ".bak-1").read_text(encoding="utf-8") == "foreign hook\n"


@pytest.mark.parametrize(("hook_name", "parts"), [("pi", PI_HOOK), ("omp", OMP_HOOK)])
def test_uninstall_preserves_foreign_raw_hook_file(tmp_path, hook_name, parts):
    dest = raw_hook_path(tmp_path, hook_name, parts)
    dest.parent.mkdir(parents=True)
    dest.write_text("foreign hook\n", encoding="utf-8")

    proc = install_into(tmp_path, "--uninstall")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert dest.read_text(encoding="utf-8") == "foreign hook\n"


@pytest.mark.parametrize(("hook_name", "parts"), [("pi", PI_HOOK), ("omp", OMP_HOOK)])
def test_uninstall_removes_stale_owned_raw_hook_file(tmp_path, hook_name, parts):
    installed = install_into(tmp_path, "--hooks", hook_name)
    assert installed.returncode == 0, installed.stderr + installed.stdout

    dest = raw_hook_path(tmp_path, hook_name, parts)
    write_stale_owned_raw_hook(dest)

    proc = install_into(tmp_path, "--uninstall")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not dest.exists()


def test_codex_hook_command_quotes_binary_path_with_spaces(tmp_path):
    root = tmp_path / "Plugin installation root with spaces"
    proc = install_into(root, "--hooks", "codex")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    hook = relay_codex_hook(root)
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert str(binary) in hook["command"]
    assert str(binary) in hook["commandWindows"]
    assert "__RELAY_" not in hook["command"] + hook["commandWindows"]
    assert hook["commandWindows"].startswith("& ")
    assert install_mod._quote_powershell_arg(str(binary)) in hook["commandWindows"]
    assert "hook" in hook["command"] and "--agent" in hook["command"]



def test_generated_binary_hooks_execute_with_metacharacter_path(tmp_path):
    root = tmp_path / "Plugin&Root O'Brien $HOME`literal"
    proc = install_into(root, "--hooks", "codex,claude")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    codex_hook = relay_codex_hook(root)
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    if os.name == "nt":
        assert codex_hook["commandWindows"].startswith("& ")
        assert install_mod._quote_powershell_arg(str(binary)) in codex_hook["commandWindows"]
        codex_commands = {"command": codex_hook["command"], "commandWindows": codex_hook["commandWindows"]}
    else:
        codex_commands = {"command": codex_hook["command"]}
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": f"codex-{tmp_path.name}"})
    for label, command in codex_commands.items():
        run = run_generated_hook(command, input_text=payload, cwd=tmp_path)
        assert run.returncode == 0, f"{label}: {run.stderr}{run.stdout}"

    claude_hook = next(entry for entry in claude_hook_entries(root) if entry.get("x-installed-by") == "relay")
    assert claude_hook["command"] == str(binary)
    assert claude_hook["args"] == ["hook", "--agent", "claude"]
    run = run_generated_exec(
        claude_hook["command"],
        claude_hook["args"],
        input_text=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": f"claude-{tmp_path.name}",
            "cwd": str(tmp_path),
        }),
        cwd=tmp_path,
    )
    assert run.returncode == 0, run.stderr + run.stdout

    status = install_into(root, "--status")
    assert status.returncode == 0, status.stderr + status.stdout
    assert "hook codex: wired -> canonical hooks" in status.stdout
    assert "hook claude: wired -> canonical hooks" in status.stdout


def test_repair_restores_payload_plugins_and_missing_codex_hook_without_touching_convs(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "codex")
    assert first.returncode == 0, first.stderr + first.stdout

    record = root / "convs" / "2026-07-04_keep.md"
    before = b"conversation bytes survive installer repair\n"
    record.write_bytes(before)

    stale_payload = root / "hooks" / "codex" / "__pycache__" / "old.pyc"
    stale_payload.parent.mkdir(parents=True)
    stale_payload.write_bytes(b"cache")
    stale_reference = root / "references" / "obsolete.md"
    stale_reference.write_text("old reference\n", encoding="utf-8")

    stale_plugin_file = root.joinpath(*CANONICAL_PLUGIN) / "skills" / "obsolete" / "SKILL.md"
    stale_plugin_file.parent.mkdir(parents=True)
    stale_plugin_file.write_text("---\nname: obsolete\n---\n", encoding="utf-8")
    plugin_skill = root.joinpath(*CANONICAL_PLUGIN) / "skills" / "save" / "SKILL.md"
    plugin_skill.unlink()

    codex_hooks = codex_hooks_path(root)
    codex_hooks.unlink()

    repaired = install_into(root, "--repair", "--hooks", "codex")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "repair universal installation root" in repaired.stdout
    assert record.read_bytes() == before
    assert (root / "hooks" / "codex" / "hooks.json").is_file()
    assert not stale_payload.exists()
    assert not stale_payload.parent.exists()
    assert not stale_reference.exists()
    assert not stale_plugin_file.exists()
    assert plugin_skill.is_file()
    hook = relay_codex_hook(root)
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert str(binary) in hook["command"]
    assert str(binary) in hook["commandWindows"]
    assert "__RELAY_" not in hook["command"] + hook["commandWindows"]


def test_repair_rewrites_already_wired_codex_hook_when_hooks_omitted(tmp_path):
    first = install_into(tmp_path, "--hooks", "codex")
    assert first.returncode == 0, first.stderr + first.stdout
    dest = codex_hooks_path(tmp_path)
    data = json.loads(dest.read_text(encoding="utf-8"))
    data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] = "python3 stale/relay_turn_counter.py"
    data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"] = "py -3 stale\\relay_turn_counter.py"
    dest.write_text(json.dumps(data), encoding="utf-8")

    repaired = install_into(tmp_path, "--doctor-fix")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "repair hooks:" in repaired.stdout
    assert "codex" in repaired.stdout
    text = dest.read_text(encoding="utf-8")
    assert "stale/relay_turn_counter.py" not in text
    assert "stale\\relay_turn_counter.py" not in text
    hook = relay_codex_hook(tmp_path)
    assert "__RELAY_" not in hook["command"] + hook["commandWindows"]


def test_update_rewrites_already_wired_codex_hook_when_hooks_omitted(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "codex")
    assert first.returncode == 0, first.stderr + first.stdout

    installed_binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    installed_binary.write_bytes(b"STALE BINARY")
    stale_codex_hook(root)

    dest = codex_hooks_path(root)
    data = json.loads(dest.read_text(encoding="utf-8"))
    data["hooks"]["UserPromptSubmit"][0]["hooks"].append(
        {"type": "command", "command": "echo foreign prompt hook"}
    )
    dest.write_text(json.dumps(data), encoding="utf-8")

    updated = install_into(root, "--update")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    assert installed_binary.read_bytes() != b"STALE BINARY"

    text = dest.read_text(encoding="utf-8")
    hook = relay_codex_hook(root)
    assert "stale/relay_turn_counter.py" not in text
    assert "stale\\relay_turn_counter.py" not in text
    assert "echo foreign prompt hook" in text
    assert sum(1 for entry in codex_hook_entries(root) if entry.get("x-installed-by") == "relay") == 1
    assert str(root / "bin") in hook["command"]
    assert "hook --agent codex" in hook["command"] or "'hook' '--agent' 'codex'" in hook["command"]

    status = install_into(root, "--status")
    assert status.returncode == 0, status.stderr + status.stdout
    assert "hook codex: wired -> canonical hooks" in status.stdout
    assert f"hook codex command: {hook['command']}" in status.stdout


def test_repair_restores_missing_hook_wiring_when_hooks_omitted(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "codex")
    assert first.returncode == 0, first.stderr + first.stdout
    record = root / "convs" / "2026-07-04_keep.md"
    before = b"conversation bytes survive missing hook repair\n"
    record.write_bytes(before)
    codex_hooks_path(root).unlink()

    repaired = install_into(root, "--repair")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "repair hooks:" in repaired.stdout
    assert "codex" in repaired.stdout
    assert record.read_bytes() == before
    hook = relay_codex_hook(root)
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert str(binary) in hook["command"]
    assert str(binary) in hook["commandWindows"]


def test_repair_dry_run_does_not_mutate_stale_payload_plugins_hooks_or_convs(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "all")
    assert first.returncode == 0, first.stderr + first.stdout

    record = root / "convs" / "nested" / "keep.md"
    record.parent.mkdir()
    record_bytes = b"nested conversation survives dry-run repair\n"
    record.write_bytes(record_bytes)
    root_hook = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")

    stale_payload = root / "references" / "obsolete.md"
    stale_payload.write_text("stale reference\n", encoding="utf-8")
    plugin_skill = root.joinpath(*CANONICAL_PLUGIN) / "skills" / "save" / "SKILL.md"
    plugin_skill.unlink()

    stale_codex_hook(root)
    stale_claude_hook(root)
    pi_hook = pi_hooks_path(root)
    omp_hook = omp_hooks_path(root)
    write_stale_owned_raw_hook(pi_hook)
    write_stale_owned_raw_hook(omp_hook)

    snapshots = {
        path: path.read_bytes()
        for path in (
            root_hook,
            stale_payload,
            codex_hooks_path(root),
            claude_settings_path(root),
            pi_hook,
            omp_hook,
            record,
        )
    }

    repaired = install_into(root, "--repair", "--dry-run")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "dry-run: no changes will be made" in repaired.stdout
    assert "would" in repaired.stdout
    for path, before in snapshots.items():
        assert path.read_bytes() == before, path
    assert not plugin_skill.exists()
    assert bak_files(root) == []


def test_repair_without_hooks_rewires_all_stale_owned_hooks(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "all")
    assert first.returncode == 0, first.stderr + first.stdout

    stale_codex_hook(root)
    stale_claude_hook(root)
    pi_hook = pi_hooks_path(root)
    omp_hook = omp_hooks_path(root)
    write_stale_owned_raw_hook(pi_hook)
    write_stale_owned_raw_hook(omp_hook)

    repaired = install_into(root, "--repair")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "repair hooks:" in repaired.stdout
    for hook_name in ("claude", "codex", "pi", "omp"):
        assert hook_name in repaired.stdout

    codex = relay_codex_hook(root)
    assert_stale_hook_targets_removed(codex["command"] + codex["commandWindows"])
    claude_hook = next(
        entry for entry in claude_hook_entries(root) if entry.get("x-installed-by") == "relay"
    )
    assert_stale_hook_targets_removed(install_mod._command_text(claude_hook))
    assert "stale" not in pi_hook.read_text(encoding="utf-8")
    assert "stale" not in omp_hook.read_text(encoding="utf-8")


def test_repair_hooks_none_opts_out_of_hook_rewrites(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root, "--hooks", "all")
    assert first.returncode == 0, first.stderr + first.stdout

    stale_codex_hook(root)
    stale_claude_hook(root)
    pi_hook = pi_hooks_path(root)
    write_stale_owned_raw_hook(pi_hook)

    repaired = install_into(root, "--repair", "--hooks", "none")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "repair hooks: none selected" in repaired.stdout
    assert "relay-stale/bin/relay" in codex_hooks_path(root).read_text(encoding="utf-8")
    assert "relay-stale/bin/relay.exe" in claude_settings_path(root).read_text(encoding="utf-8")
    assert "stale" in pi_hook.read_text(encoding="utf-8")


def test_repair_preserves_and_reports_invalid_hook_json(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root)
    assert first.returncode == 0, first.stderr + first.stdout

    codex_hooks = codex_hooks_path(root)
    codex_hooks.parent.mkdir(exist_ok=True)
    codex_before = "{not-json}\n"
    codex_hooks.write_text(codex_before, encoding="utf-8")

    claude_settings = claude_settings_path(root)
    claude_settings.parent.mkdir(exist_ok=True)
    claude_before = "[]\n"
    claude_settings.write_text(claude_before, encoding="utf-8")

    repaired = install_into(root, "--repair")
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "codex: existing" in repaired.stdout and "hooks.json is not valid JSON" in repaired.stdout
    assert "claude: existing" in repaired.stdout and "settings.json is JSON but not an object" in repaired.stdout
    assert codex_hooks.read_text(encoding="utf-8") == codex_before
    assert claude_settings.read_text(encoding="utf-8") == claude_before


def test_codex_hook_replaces_old_relay_hook_and_preserves_foreign_hook(tmp_path):
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 .relay/hooks/codex/relay_turn_counter.py",
                            "commandWindows": "python .relay/hooks/codex/relay_turn_counter.py",
                        },
                        {
                            "type": "command",
                            "command": "echo foreign",
                        },
                    ]
                }
            ]
        }
    }
    dest = codex_hooks_path(tmp_path)
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(existing), encoding="utf-8")

    proc = install_into(tmp_path, "--hooks", "codex")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    text = dest.read_text(encoding="utf-8")
    entries = codex_hook_entries(tmp_path)
    assert "echo foreign" in text
    assert "python3 .relay/hooks/codex/relay_turn_counter.py" not in text
    assert sum(1 for entry in entries if entry.get("x-installed-by") == "relay") == 1


def test_codex_hook_preserves_foreign_events_groups_and_metadata(tmp_path):
    existing = {
        "version": 1,
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [{"type": "command", "command": "echo session"}],
                }
            ],
            "UserPromptSubmit": [
                {"matcher": "keep-group-without-hooks"},
                {
                    "matcher": "mixed",
                    "hooks": [
                        {"type": "command", "command": "echo foreign prompt"},
                        {"type": "command", "command": "python ~/.relay/hooks/codex/relay_turn_counter.py"},
                    ],
                },
            ],
        },
    }
    dest = codex_hooks_path(tmp_path)
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(existing), encoding="utf-8")

    proc = install_into(tmp_path, "--hooks", "codex")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    data = json.loads(dest.read_text(encoding="utf-8"))
    text = json.dumps(data)
    assert data["version"] == 1
    assert {"matcher": "keep-group-without-hooks"} in data["hooks"]["UserPromptSubmit"]
    assert existing["hooks"]["SessionStart"] == data["hooks"]["SessionStart"]
    assert "echo foreign prompt" in text
    assert "python ~/.relay/hooks/codex/relay_turn_counter.py" not in text
    assert sum(1 for entry in codex_hook_entries(tmp_path) if entry.get("x-installed-by") == "relay") == 1


def test_codex_hook_preserves_unrelated_command_under_relay_root(tmp_path):
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {"type": "command", "command": "python ~/.relay/scripts/not_the_counter.py"}
                    ]
                }
            ]
        }
    }
    dest = codex_hooks_path(tmp_path)
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(existing), encoding="utf-8")

    proc = install_into(tmp_path, "--hooks", "codex")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    text = dest.read_text(encoding="utf-8")
    assert "not_the_counter.py" in text
    assert sum(1 for entry in codex_hook_entries(tmp_path) if entry.get("x-installed-by") == "relay") == 1

    removed = install_into(tmp_path, "--uninstall")
    assert removed.returncode == 0, removed.stderr + removed.stdout
    assert "not_the_counter.py" in dest.read_text(encoding="utf-8")


def test_codex_hook_rerun_is_idempotent_no_bak_spam(tmp_path):
    first = install_into(tmp_path, "--hooks", "codex")
    assert first.returncode == 0, first.stderr + first.stdout
    second = install_into(tmp_path, "--hooks", "codex")
    assert second.returncode == 0, second.stderr + second.stdout
    assert "codex: hook already present" in second.stdout
    assert bak_files(codex_home_for(tmp_path)) == []


def test_claude_hook_uses_custom_target_and_replaces_owned_hook(tmp_path):
    root = tmp_path / "Plugin installation root with spaces"
    settings = claude_settings_path(root)
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "powershell -NoProfile -File \"~/.relay/hooks/claude/relay-turn-counter.ps1\"",
                                },
                                {"type": "command", "command": "echo foreign"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    proc = install_into(root, "--hooks", "claude")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    text = settings.read_text(encoding="utf-8")
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    entries = claude_hook_entries(root)
    hook = next(entry for entry in entries if entry.get("x-installed-by") == "relay")
    assert hook["command"] == str(binary)
    assert hook["args"] == ["hook", "--agent", "claude"]
    assert "relay-turn-counter.ps1" not in text
    assert "echo foreign" in text
    assert sum(1 for entry in entries if entry.get("x-installed-by") == "relay") == 1


def test_claude_hook_command_text_inspects_args_for_ownership():
    # Regression: a command+args hook entry (the real-world stale form, e.g.
    # {"command": "pwsh", "args": ["-File", "<worktree>/.claude/hooks/relay-turn-counter.ps1"]})
    # must be recognized as Relay-owned so repair/uninstall can replace or strip it.
    args_entry = {
        "type": "command",
        "command": "pwsh",
        "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 "${CLAUDE_PROJECT_DIR}/.claude/hooks/relay-turn-counter.ps1"],
    }
    assert install_mod._is_our_hook(args_entry)
    assert "relay-turn-counter.ps1" in install_mod._command_text(args_entry)
    # Non-relay args-form entry is NOT claimed as ours.
    foreign = {"type": "command", "command": "pwsh", "args": ["-File", "other.ps1"]}
    assert not install_mod._is_our_hook(foreign)


def test_canonical_claude_exec_hook_is_owned_and_canonical(tmp_path):
    script = tmp_path / "O'Brien" / "hooks" / "claude" / "relay-turn-counter.ps1"
    entry = {
        "type": "command",
        "command": r"C:\Program Files\PowerShell\7\pwsh.exe",
        "args": ["-NoProfile", "-File", str(script)],
    }

    assert install_mod._is_our_hook(entry)
    assert install_mod._hook_entry_points_to(entry, script)


def test_powershell_call_operator_hook_quotes_binary_path(tmp_path):
    binary = tmp_path / "O'Brien" / "bin" / "relay.exe"
    command = install_mod._codex_hook_command(binary, powershell=True)
    entry = {"type": "command", "commandWindows": command, "x-installed-by": "relay"}
    assert command.startswith("& '")
    assert install_mod._is_our_hook(entry)
    assert install_mod._hook_entry_points_to(entry, binary)


def test_claude_hook_repair_without_hooks_selects_and_rewrites_stale_args_form_owned_hook(tmp_path):
    # Regression for the reported bug: a project settings.json carrying a stale
    # command+args Relay hook (the real-world {command: "pwsh", args: [..., "-File",
    # "<worktree>/.claude/hooks/relay-turn-counter.ps1"]} form) must be (1) recognized as
    # ours by _hook_wired_claude so --repair with NO --hooks selects Claude for repair,
    # and (2) rewritten to the canonical installed script path, preserving foreign hooks.
    root = tmp_path / "Plugin installation root"
    home = agent_home_for(root)
    # Full install first so the canonical hook root + plugin exist for repair.
    first = install_into(root, "--hooks", "claude", home=home)
    assert first.returncode == 0, first.stderr + first.stdout

    settings = claude_settings_path(root)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "pwsh",
                                    "commandWindows": "pwsh -File stale/relay-turn-counter.ps1",
                                    "shell": "powershell",
                                    "args": [
                                        "-NoProfile",
                                        "-ExecutionPolicy",
                                        "Bypass",
                                        "-File",
                                        "${CLAUDE_PROJECT_DIR}/.claude/hooks/relay-turn-counter.ps1",
                                    ],
                                    "timeout": 5,
                                },
                                {"type": "command", "command": "echo foreign"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    # The args-form hook must be recognized as ours by _hook_wired_claude (the
    # exact check _installed_hook_set delegates to for claude) so --repair with no
    # --hooks selects Claude independent of the always-available source snippet.
    # claude_home pins this to the test settings, not the real user surface.
    ctx = install_mod.Ctx(source=REPO_ROOT, target=root, claude_home=home / ".claude")
    assert install_mod._hook_wired_claude(ctx)

    repaired = install_into(root, "--repair", home=home)
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert "repair hooks:" in repaired.stdout
    # The decisive guard is below: the stale args-form entry must be gone
    # post-repair (proves _replace_our_hook_events matched the args form).

    text = settings.read_text(encoding="utf-8")
    # The stale worktree-local path is gone entirely.
    assert "${CLAUDE_PROJECT_DIR}" not in text
    assert ".claude/hooks/relay-turn-counter.ps1" not in text
    # A foreign hook in the same group is preserved.
    assert "echo foreign" in text
    entries = claude_hook_entries(root, home=home)
    # Exactly one Relay entry remains, now in canonical shell-free exec form.
    owned = [entry for entry in entries if entry.get("x-installed-by") == "relay"]
    assert len(owned) == 1
    binary = root / "bin" / ("relay.exe" if os.name == "nt" else "relay")
    assert owned[0]["command"] == str(binary)
    assert owned[0]["args"] == ["hook", "--agent", "claude"]
    assert "commandWindows" not in owned[0]
    assert "shell" not in owned[0]



def test_powershell_quoting_is_literal_and_escapes_apostrophes():
    executable = "C:\\Tools & O'Brien\\$Python` (3)\\python.exe\\"
    command = install_mod._quote_command(
        (executable, "-X", "", "$HOME"), powershell=True
    )

    assert command == "& 'C:\\Tools & O''Brien\\$Python` (3)\\python.exe\\' '-X' '' '$HOME'"




def test_refuses_installing_into_plugin_source():
    proc = install_into(REPO_ROOT)
    assert proc.returncode == 2
    assert "Plugin source" in proc.stderr


def test_update_preserves_existing_conversation_database_bytes(tmp_path):
    assert install_into(tmp_path).returncode == 0
    record = tmp_path / "convs" / "2026-07-04_binary.md"
    before = b"\x00conversation bytes\r\nnot installer-owned\xff"
    record.write_bytes(before)
    nested = tmp_path / "convs" / "nested"
    nested.mkdir()
    nested_record = nested / "keep.md"
    nested_before = b"nested bytes\n"
    nested_record.write_bytes(nested_before)

    proc = install_into(tmp_path, "--update")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert record.read_bytes() == before
    assert nested_record.read_bytes() == nested_before


@pytest.mark.parametrize(
    "repair_args",
    [
        ("--update",),
        ("--repair", "--hooks", "none"),
    ],
)
def test_update_and_repair_recover_expected_file_directory_collisions(tmp_path, repair_args):
    root = tmp_path / "plugin-root"
    first = install_into(root)
    assert first.returncode == 0, first.stderr + first.stdout

    payload_file = root / "references" / "save.md"
    plugin_skill_file = root.joinpath(*CANONICAL_PLUGIN) / "skills" / "save" / "SKILL.md"
    expected = {
        payload_file: (REPO_ROOT / "references" / "save.md").read_bytes(),
        plugin_skill_file: (REPO_ROOT / "skills" / "save" / "SKILL.md").read_bytes(),
    }
    for path in expected:
        replace_file_with_directory(path)

    repaired = install_into(root, *repair_args)
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    for path, expected_bytes in expected.items():
        assert path.is_file(), path
        assert path.read_bytes() == expected_bytes


def test_update_pruning_preserves_top_level_user_files_and_nested_convs(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root)
    assert first.returncode == 0, first.stderr + first.stdout

    top_file = root / "user-notes.md"
    top_file.write_text("keep top-level user file\n", encoding="utf-8")
    top_nested = root / "user-dir" / "note.txt"
    top_nested.parent.mkdir()
    top_nested.write_text("keep top-level user dir\n", encoding="utf-8")
    nested_conv = root / "convs" / "deep" / "keep.md"
    nested_conv.parent.mkdir()
    nested_conv_bytes = b"nested conversation remains\n"
    nested_conv.write_bytes(nested_conv_bytes)

    stale_reference = root / "references" / "obsolete.md"
    stale_hook = root / "hooks" / "codex" / "obsolete.py"
    stale_script = root / "scripts" / "obsolete.py"
    for stale in (stale_reference, stale_hook, stale_script):
        stale.write_text("stale installer-owned payload\n", encoding="utf-8")

    updated = install_into(root, "--update")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    assert top_file.read_text(encoding="utf-8") == "keep top-level user file\n"
    assert top_nested.read_text(encoding="utf-8") == "keep top-level user dir\n"
    assert nested_conv.read_bytes() == nested_conv_bytes
    assert not stale_reference.exists()
    assert not stale_hook.exists()
    assert not stale_script.exists()


def test_installed_repair_ignores_stale_hidden_repair_source_without_touching_convs(tmp_path):
    root = tmp_path / "plugin-root"
    first = install_into(root)
    assert first.returncode == 0, first.stderr + first.stdout

    record = root / "convs" / "2026-07-04_keep.md"
    before = b"conversation bytes survive stale hidden source\n"
    record.write_bytes(before)

    live_reference = root / "references" / "save.md"
    live_plugin_skill = root.joinpath(*CANONICAL_PLUGIN) / "skills" / "save" / "SKILL.md"
    live_reference_bytes = b"live installed reference must not be overwritten\n"
    live_plugin_bytes = b"live installed plugin skill must not be overwritten\n"
    live_reference.write_bytes(live_reference_bytes)
    live_plugin_skill.write_bytes(live_plugin_bytes)

    hidden = root / ".relay-repair-source"
    assert not hidden.exists()
    (hidden / "scripts").mkdir(parents=True)
    (hidden / "bin").mkdir(parents=True)
    (hidden / "references").mkdir(parents=True)
    (hidden / "plugins" / "conv" / ".claude-plugin").mkdir(parents=True)
    (hidden / "plugins" / "conv" / "skills" / "save").mkdir(parents=True)
    (hidden / "SKILL.md").write_text("stale hidden root skill\n", encoding="utf-8")
    (hidden / "bin" / ("relay.exe" if os.name == "nt" else "relay")).write_text(
        "stale hidden binary\n", encoding="utf-8"
    )
    (hidden / "references" / "save.md").write_bytes(b"STALE HIDDEN REFERENCE\n")
    (hidden / "plugins" / "conv" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"x-installed-by": "relay"}),
        encoding="utf-8",
    )
    (hidden / "plugins" / "conv" / "skills" / "save" / "SKILL.md").write_bytes(b"STALE HIDDEN PLUGIN\n")

    repaired = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "install.py"),
            "--source",
            str(root),
            "--target",
            str(root),
            "--doctor-fix",
            "--hooks",
            "none",
        ],
        cwd=str(root),
        env=clean_env(home=agent_home_for(root)),
        capture_output=True,
        text=True,
    )
    assert repaired.returncode == 0, repaired.stderr + repaired.stdout
    assert record.read_bytes() == before
    assert live_reference.read_bytes() == live_reference_bytes
    assert live_plugin_skill.read_bytes() == live_plugin_bytes


# --- conv plugin skill group -------------------------------------------------


def test_fresh_install_registers_conv_plugin_with_nine_skills(tmp_path):
    # Contract 1: default install plants the shared conv plugin manifest and
    # ships one base skill plus one SKILL.md per verb.
    proc = install_into(tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    plugin_root = tmp_path.joinpath(*CANONICAL_PLUGIN)
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    codex_manifest = plugin_root / ".codex-plugin" / "plugin.json"
    assert (plugin_root / "SKILL.md").is_file()
    assert manifest.is_file(), f"conv plugin manifest not at {manifest}"
    assert codex_manifest.is_file(), f"Codex plugin manifest not at {codex_manifest}"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["name"] == "relay"
    assert data.get("x-installed-by") == "relay"
    codex_data = json.loads(codex_manifest.read_text(encoding="utf-8"))
    assert codex_data["name"] == "relay"
    assert codex_data["skills"] == "./skills/"
    codex_text = json.dumps(codex_data)
    for skill in RELAY_SKILLS:
        if skill != "relay":
            assert f"relay:{skill}" in codex_text
    for skill in RELAY_SKILLS:
        skill_md = plugin_root / "skills" / skill / "SKILL.md"
        assert skill_md.is_file(), f"missing skill {skill!r} at {skill_md}"


def test_non_claude_agent_still_gets_single_canonical_conv_plugin(tmp_path):
    # Contract 2: agent selection no longer creates nested plugin copies under
    # the universal installation root.
    proc = install_into(tmp_path, "--agents", "pi")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (tmp_path.joinpath(*CANONICAL_PLUGIN) / "skills" / "save" / "SKILL.md").is_file()
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_CLAUDE_PLUGIN))
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_AGENTS_PLUGIN))


def test_status_reports_conv_plugin_present_then_not_installed(tmp_path):
    # Contract 3: --status emits exact lines for the conv plugin in both states.
    assert install_into(tmp_path).returncode == 0
    present = install_into(tmp_path, "--status")
    assert present.returncode == 0, present.stdout + present.stderr
    assert "canonical plugin: present (9 skills)" in present.stdout.splitlines()
    assert "legacy claude plugin .claude/skills/conv: absent" in present.stdout.splitlines()
    assert "legacy agents plugin .agents/skills/conv: absent" in present.stdout.splitlines()

    # A sibling target that was never installed into reports "not installed".
    empty = tmp_path / "empty"
    empty.mkdir()
    absent = install_into(empty, "--status")
    assert absent.returncode == 0, absent.stdout + absent.stderr
    assert "canonical plugin: not installed" in absent.stdout.splitlines()


def test_rerun_idempotent_leaves_no_bak_in_conv_plugin(tmp_path):
    # Contract 4: reinstalling must not treat the conv plugin's own files as
    # conflicts and back them up; the plugin copy step is a separate code path
    # from the skill-link logic and has its own conflict handling.
    assert install_into(tmp_path).returncode == 0
    second = install_into(tmp_path)
    assert second.returncode == 0, second.stderr + second.stdout
    plugin_root = tmp_path.joinpath(*CANONICAL_PLUGIN)
    assert plugin_root.is_dir()
    assert bak_files(plugin_root) == []


def test_uninstall_removes_conv_plugin_but_preserves_plugin_installation_root(tmp_path):
    # Contract 5: --uninstall tears down the plugin skill groups, yet the
    # Plugin installation root survives so recorded conversations remain.
    assert install_into(tmp_path).returncode == 0
    plugin = tmp_path.joinpath(*CANONICAL_PLUGIN)
    assert plugin.is_dir()
    proc = install_into(tmp_path, "--uninstall")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not os.path.lexists(plugin)
    assert (tmp_path / "SKILL.md").is_file()


def test_foreign_conv_plugin_refused_then_force_recovers(tmp_path):
    # Contract 6: a pre-existing conv dir we didn't install must be refused
    # (exit 2) rather than clobbered; --force backs it up to conv.bak-1 and
    # installs the real relay-owned plugin in its place.
    foreign_root = tmp_path.joinpath(*CANONICAL_PLUGIN)
    foreign_manifest = foreign_root / ".claude-plugin" / "plugin.json"
    foreign_manifest.parent.mkdir(parents=True)
    foreign_manifest.write_text(json.dumps({"name": "conv"}), encoding="utf-8")

    refused = install_into(tmp_path)
    assert refused.returncode == 2
    assert "not a relay-owned plugin" in refused.stderr

    forced = install_into(tmp_path, "--force")
    assert forced.returncode == 0, forced.stderr + forced.stdout
    backups = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith("relay.bak")
    ]
    assert "relay.bak-1" in backups
    data = json.loads(foreign_manifest.read_text(encoding="utf-8"))
    assert data.get("x-installed-by") == "relay"


def test_dry_run_leaves_no_conv_plugin(tmp_path):
    # Contract 7: --dry-run must not write the plugin.
    proc = install_into(tmp_path, "--dry-run")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not os.path.lexists(tmp_path.joinpath(*CANONICAL_PLUGIN))
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_CLAUDE_PLUGIN))
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_AGENTS_PLUGIN))

# --- legacy alias coexistence (conv -> relay) ---------------------------


def test_reinstall_preserves_legacy_canonical_conv_root_alongside_relay(tmp_path):
    # An old install may still have <root>/conv. Reinstalling Relay creates the
    # separate <root>/relay plugin but must leave the old alias byte-for-byte intact.
    assert install_into(tmp_path).returncode == 0
    relay = tmp_path.joinpath(*CANONICAL_PLUGIN)
    legacy = tmp_path.joinpath(*LEGACY_CANONICAL_PLUGIN)
    shutil.copytree(relay, legacy)
    sentinel = legacy / "legacy-sentinel.txt"
    sentinel.write_bytes(b"old conv alias must survive\r\n")
    legacy_manifest = (legacy / ".claude-plugin" / "plugin.json").read_bytes()
    shutil.rmtree(relay)

    reinstalled = install_into(tmp_path)
    assert reinstalled.returncode == 0, reinstalled.stderr + reinstalled.stdout
    assert relay.is_dir()
    assert sentinel.read_bytes() == b"old conv alias must survive\r\n"
    assert (legacy / ".claude-plugin" / "plugin.json").read_bytes() == legacy_manifest
    assert (tmp_path / "convs").is_dir()
    assert (relay / "skills" / "relay" / "SKILL.md").is_file()


def test_reinstall_leaves_foreign_legacy_conv_root_alongside_relay(tmp_path):
    # A foreign <root>/conv is also separate legacy state and remains untouched.
    assert install_into(tmp_path).returncode == 0
    legacy = tmp_path.joinpath(*LEGACY_CANONICAL_PLUGIN)
    legacy.mkdir()
    (legacy / "foreign.txt").write_text("not ours\n", encoding="utf-8")

    second = install_into(tmp_path)
    assert second.returncode == 0, second.stderr + second.stdout
    assert (legacy / "foreign.txt").read_text(encoding="utf-8") == "not ours\n"
    assert tmp_path.joinpath(*CANONICAL_PLUGIN).is_dir()


def test_uninstall_preserves_legacy_canonical_conv_root(tmp_path):
    assert install_into(tmp_path).returncode == 0
    legacy = tmp_path.joinpath(*LEGACY_CANONICAL_PLUGIN)
    shutil.copytree(tmp_path.joinpath(*CANONICAL_PLUGIN), legacy)
    sentinel = legacy / "legacy-sentinel.txt"
    sentinel.write_bytes(b"do not remove legacy alias\n")

    un = install_into(tmp_path, "--uninstall")
    assert un.returncode == 0, un.stderr + un.stdout
    assert legacy.is_dir()
    assert sentinel.read_bytes() == b"do not remove legacy alias\n"
    assert not os.path.lexists(tmp_path.joinpath(*CANONICAL_PLUGIN))
    # Relay archive survives uninstall.
    assert (tmp_path / "convs").is_dir()


def test_status_reports_legacy_canonical_conv_root_as_stale_copy(tmp_path):
    assert install_into(tmp_path).returncode == 0
    legacy = tmp_path.joinpath(*LEGACY_CANONICAL_PLUGIN)
    shutil.copytree(tmp_path.joinpath(*CANONICAL_PLUGIN), legacy)

    status = install_into(tmp_path, "--status")
    assert status.returncode == 0, status.stderr + status.stdout
    assert "older canonical plugin conv: stale installer-owned copy (9 skills)" in status.stdout


def test_inplace_repair_refuses_to_use_legacy_conv_root_as_a_relay_source(tmp_path):
    # Repair must not treat a legacy alias as source material for a new Relay plugin.
    assert install_into(tmp_path).returncode == 0
    relay = tmp_path.joinpath(*CANONICAL_PLUGIN)
    legacy = tmp_path.joinpath(*LEGACY_CANONICAL_PLUGIN)
    shutil.copytree(relay, legacy)
    sentinel = legacy / "legacy-sentinel.txt"
    sentinel.write_bytes(b"legacy source remains untouched\n")
    shutil.rmtree(relay)

    home = agent_home_for(tmp_path)
    repaired = run_install(
        ["--source", str(tmp_path), "--target", str(tmp_path), "--repair"],
        env=clean_env(home=home),
    )
    assert repaired.returncode == 2
    assert not relay.exists()
    assert sentinel.read_bytes() == b"legacy source remains untouched\n"

# --- claude-plugin-only mode --------------------------------------------------


def test_claude_plugin_only_installs_plugin_without_store_or_skill_link(tmp_path):
    # Contract: --claude-plugin-only installs ONLY the conv plugin skill group
    # and skips the full install entirely: no root plugin files, no Relay archive, no
    # legacy relay skill link.
    proc = install_into(tmp_path, "--claude-plugin-only")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    plugin_root = tmp_path.joinpath(*CANONICAL_PLUGIN)
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    codex_manifest = plugin_root / ".codex-plugin" / "plugin.json"
    assert manifest.is_file(), f"conv plugin manifest not at {manifest}"
    assert codex_manifest.is_file(), f"Codex plugin manifest not at {codex_manifest}"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["name"] == "relay"
    assert data.get("x-installed-by") == "relay"
    for skill in RELAY_SKILLS:
        skill_md = plugin_root / "skills" / skill / "SKILL.md"
        assert skill_md.is_file(), f"missing skill {skill!r} at {skill_md}"
    # The mode must NOT have run the full install path.
    assert not os.path.lexists(tmp_path / "SKILL.md")
    assert not os.path.lexists(tmp_path / "convs")
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_CLAUDE_LINK))
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_CLAUDE_PLUGIN))
    assert not os.path.lexists(tmp_path.joinpath(*LEGACY_AGENTS_PLUGIN))


def test_claude_plugin_only_uninstall_removes_plugin(tmp_path):
    # Contract: --claude-plugin-only --uninstall removes only the conv plugin.
    assert install_into(tmp_path, "--claude-plugin-only").returncode == 0
    plugin_root = tmp_path.joinpath(*CANONICAL_PLUGIN)
    assert plugin_root.is_dir()
    proc = install_into(tmp_path, "--claude-plugin-only", "--uninstall")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert not os.path.lexists(plugin_root)


def test_claude_plugin_only_dry_run_writes_nothing(tmp_path):
    # Contract: --claude-plugin-only --dry-run only plans and writes nothing.
    proc = install_into(tmp_path, "--claude-plugin-only", "--dry-run")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "would" in proc.stdout
    assert not os.path.lexists(tmp_path.joinpath(*CANONICAL_PLUGIN))
    assert not os.path.lexists(tmp_path / "convs")


def test_claude_plugin_only_defaults_to_home_plugin_installation_root(tmp_path):
    # Uses --dry-run so nothing is written; asserts only on the emitted path line.
    home = tmp_path / "home"
    root = (home / ".relay").resolve()
    proc = install_default(home, "--claude-plugin-only", "--dry-run")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert f"plugin_installation_root = {root}" in proc.stdout.splitlines()


def test_claude_plugin_only_update_refreshes_owned_file(tmp_path):
    # Contract: --claude-plugin-only --update refreshes owned plugin files in place.
    assert install_into(tmp_path, "--claude-plugin-only").returncode == 0
    save_md = tmp_path.joinpath(*CANONICAL_PLUGIN) / "skills" / "save" / "SKILL.md"
    assert save_md.is_file()
    save_md.write_bytes(b"STALE\n")
    proc = install_into(tmp_path, "--claude-plugin-only", "--update")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert save_md.read_bytes() != b"STALE\n"
