"""Black-box tests for scripts/install.py.

Mirrors tests/_util.py: drives the real installer via subprocess in a cleaned env
against pytest tmp_path targets, so it exercises exactly what a user runs. Asserts
on installer-owned artifacts (payload files, skill links, hook files) which stay
stable even while the conv_cli.py engine is refactored in parallel; the store
(convs/, index.jsonl, .conv-root) is checked too but the installer treats a
failing `init` tolerantly, so these tests never depend on engine internals.

Run: python -m pytest tests/test_install.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Reuse the shared cleaned-env helper without importing engine specifics. Insert the
# tests dir on sys.path so `_util` resolves regardless of pytest's import mode.
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _util import clean_env  # noqa: E402  (do not modify _util.py)

REPO_ROOT = TESTS_DIR.parent
INSTALL = REPO_ROOT / "scripts" / "install.py"

CLAUDE_LINK = (".claude", "skills", "conversate")
AGENTS_LINK = (".agents", "skills", "conversate")


def run_install(args, cwd=None, env=None) -> subprocess.CompletedProcess:
    if env is None:
        env = clean_env()
    if cwd is None:
        cwd = REPO_ROOT
    return subprocess.run(
        [sys.executable, str(INSTALL), *map(str, args)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def install_into(target: Path, *extra) -> subprocess.CompletedProcess:
    return run_install(["--target", str(target), *extra])


def bak_files(root: Path) -> list[str]:
    # os.walk does not follow symlinked skill links, so it will not descend into
    # .conversate via a link; the real .conversate is walked directly (no .bak there).
    found: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        found += [os.path.join(dirpath, f) for f in files if ".bak" in f]
    return found


def assert_installed(target: Path) -> None:
    conv = target / ".conversate"
    # payload (installer-owned)
    assert (conv / "SKILL.md").is_file()
    assert (conv / "scripts" / "conv_cli.py").is_file()
    assert (conv / "references").is_dir()
    assert (conv / "hooks").is_dir()
    # store (engine-created, but present even if init exits nonzero mid-refactor)
    assert (conv / "convs").is_dir()
    assert (conv / "index.jsonl").exists()
    assert (conv / ".conv-root").exists()
    # both skill links, platform-tolerant: symlink / junction / copy all expose SKILL.md
    for parts in (CLAUDE_LINK, AGENTS_LINK):
        link = target.joinpath(*parts)
        assert os.path.lexists(link), f"missing link {link}"
        assert (link / "SKILL.md").is_file(), f"SKILL.md not reachable via {link}"


def test_fresh_install_creates_payload_store_and_links(tmp_path):
    proc = install_into(tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert_installed(tmp_path)


def test_rerun_is_idempotent_no_bak_spam(tmp_path):
    first = install_into(tmp_path)
    assert first.returncode == 0, first.stderr
    second = install_into(tmp_path)
    assert second.returncode == 0, second.stderr
    # a plain re-run has no payload conflicts and links already resolve, so it must
    # not create any backup files.
    assert bak_files(tmp_path) == []
    # payload line reports everything already in place on the second run
    assert "0 created" in second.stdout
    assert_installed(tmp_path)


def test_status_reports_present_on_installed(tmp_path):
    assert install_into(tmp_path).returncode == 0
    proc = install_into(tmp_path, "--status")
    assert proc.returncode == 0, proc.stderr
    assert "payload: present" in proc.stdout
    assert "store: present" in proc.stdout
    # nothing on an installed target should be reported missing (links resolve;
    # unwired hooks read "not wired", not "missing")
    assert proc.stdout.count(": missing") == 0


def test_status_reports_missing_on_empty(tmp_path):
    proc = install_into(tmp_path, "--status")
    assert proc.returncode == 0, proc.stderr
    assert "payload: missing" in proc.stdout
    assert "store: missing" in proc.stdout
    assert "missing" in proc.stdout  # links reported missing too


def test_dry_run_changes_nothing(tmp_path):
    proc = install_into(tmp_path, "--hooks", "all", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "would" in proc.stdout
    # nothing at all should have been created under the target
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path / ".conversate").exists()


def test_uninstall_removes_links_but_preserves_convs(tmp_path):
    assert install_into(tmp_path, "--hooks", "all").returncode == 0
    # plant a conversation record that must survive uninstall
    planted = tmp_path / ".conversate" / "convs" / "2026-01-01_keepme.md"
    planted.write_text("PRECIOUS DATA\n", encoding="utf-8")

    proc = install_into(tmp_path, "--uninstall")
    assert proc.returncode == 0, proc.stderr

    # links removed
    for parts in (CLAUDE_LINK, AGENTS_LINK):
        assert not os.path.lexists(tmp_path.joinpath(*parts))
    # hook files removed
    assert not (tmp_path / ".pi" / "extensions" / "conv-turn-counter.ts").exists()
    assert not (tmp_path / ".omp" / "hooks" / "pre" / "conv-turn-counter.ts").exists()
    # data sacred: .conversate and the planted record remain
    assert (tmp_path / ".conversate" / "SKILL.md").is_file()
    assert planted.is_file()
    assert planted.read_text(encoding="utf-8") == "PRECIOUS DATA\n"


def test_uninstall_dry_run_keeps_links(tmp_path):
    assert install_into(tmp_path).returncode == 0
    proc = install_into(tmp_path, "--uninstall", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    for parts in (CLAUDE_LINK, AGENTS_LINK):
        assert os.path.lexists(tmp_path.joinpath(*parts))


def test_payload_conflict_refused_then_update_recovers(tmp_path):
    assert install_into(tmp_path).returncode == 0
    save_md = tmp_path / ".conversate" / "references" / "save.md"
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
    pi_hook = tmp_path / ".pi" / "extensions" / "conv-turn-counter.ts"
    codex_hooks = tmp_path / ".codex" / "hooks.json"
    assert pi_hook.is_file()
    assert codex_hooks.is_file()
    assert "conv_turn_counter" in codex_hooks.read_text(encoding="utf-8")
    # codex hook was the only entry, so uninstall removes the file entirely
    assert install_into(tmp_path, "--uninstall").returncode == 0
    assert not codex_hooks.exists()


def test_refuses_self_install_without_target():
    # cwd == source and no --target: refuse to nest a copy inside the checkout
    proc = run_install([], cwd=REPO_ROOT)
    assert proc.returncode == 2
    assert "refusing" in proc.stderr.lower()
