#!/usr/bin/env python3
"""Relay cross-agent installer.

Installs Relay runtime files into the universal installation root, creates the
Relay archive, installs the Relay plugin skill group at the canonical
installed plugin root, and writes scan entrypoints in real agent config surfaces:

  ~/.claude/skills/relay (Claude Code)
  ~/.codex/skills/relay  (Codex)

Stdlib only. Relay records under `convs/` are never deleted or overwritten
by any flag.

Usage:
  python scripts/install.py [--target DIR] [--source DIR]
        [--agents claude,pi,omp,codex|all] [--hooks claude,pi,omp,codex|all|none]
        [--update] [--repair|--doctor-fix] [--force] [--uninstall] [--status] [--dry-run]
"""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RELAY_DIRNAME = ".relay"
COPY_MARKER = ".relay-installed-copy"

# Runtime files copied into the universal installation root. The Rust binary is
# the only hook runtime; Python remains only the installer bootstrap.
PAYLOAD_FILES = ("LICENSE", "scripts/install.py")
PAYLOAD_DIRS = ("references",)
BINARY_BASENAME = "relay"
HOOK_PAYLOAD_FILES = (
    "codex/hooks.json",
    "claude/settings-snippet.json",
    "pi/relay-turn-counter.ts",
)
# Universal-root SKILL.md keeps frontmatter `name: relay`, so it is sourced
# from the base skill, NOT the root plugin entrypoint SKILL.md (which is name: relay
# and is consumed by the plugin walk -> <T>/relay/SKILL.md). src relpath -> dst relpath.
PAYLOAD_FILE_MAP = {("skills", "relay", "SKILL.md"): ("SKILL.md",)}
HOOK_SOURCE_DIR = "hooks"
IGNORE_DIR_NAMES = {"__pycache__", ".git", ".semble", "convs", ".arca"}
IGNORE_SUFFIXES = {".pyc", ".pyo"}

ALL_AGENTS = ("claude", "pi", "omp", "codex")

# Agents that historically resolved shared skills/plugins via .agents/skills/.
AGENTS_DIR_CONSUMERS = {"pi", "omp", "codex"}

# Legacy surfaces are inspection-only compatibility evidence. Relay never creates,
# migrates, deletes, or rewrites them; the explicit `relay import --from` command is
# the sole path that may copy legacy records into a Relay archive.
LEGACY_CLAUDE_LINK = (".claude", "skills", "conversate")
LEGACY_AGENTS_LINK = (".agents", "skills", "conversate")
CANONICAL_PLUGIN_DEST = ("relay",)
LEGACY_CANONICAL_PLUGIN_DEST = ("conversate",)
OLDER_CANONICAL_PLUGIN_DEST = ("conv",)
LEGACY_CLAUDE_PLUGIN_DEST = (".claude", "skills", "conv")
LEGACY_AGENTS_PLUGIN_DEST = (".agents", "skills", "conv")
AGENT_SKILL_ENTRYPOINT = ("skills", "relay")
# Prior-namespace real-home scan entrypoints (skills/conv under each agent's own
# config surface). They remain untouched for compatibility while Relay creates its
# separate skills/relay entrypoints.
LEGACY_AGENT_SKILL_ENTRYPOINT = ("skills", "conv")

# Hook install destinations (relative to target).
# pi's current user-level extension surface is ~/.pi/agent/extensions/.
PI_HOOK_DEST = (".pi", "agent", "extensions", "relay-turn-counter.ts")
OMP_HOOK_DEST = (".omp", "hooks", "pre", "relay-turn-counter.ts")
CODEX_HOOKS_JSON = (".codex", "hooks.json")
CLAUDE_SETTINGS = (".claude", "settings.json")

# Shared Relay plugin skill group. The plugin root IS the repo root
# (repo root == plugin root), so the source is ctx.source itself. Only these named
# components are copied into the installed plugin root — never an rglob of the whole
# checkout (which would sweep scripts/tests/tools/references/README/LICENSE). `hooks`
# is included deliberately: it plants a pristine <T>/relay/hooks mirror alongside the
# canonical <T>/hooks, both generated from the single source hooks/ tree. Same-source
# repair (doctor --fix on an installed root) refreshes a corrupted <T>/hooks from
# that mirror, since <T>/hooks is otherwise its own source and dest (a no-op copy).
PLUGIN_COMPONENTS = ("SKILL.md", "skills", ".claude-plugin", ".codex-plugin", "hooks")
CLAUDE_PLUGIN_MANIFEST = (".claude-plugin", "plugin.json")
CODEX_PLUGIN_MANIFEST = (".codex-plugin", "plugin.json")

# Substrings that identify the Relay turn-counter hook entry.
OUR_HOOK_MARKERS = ("relay_turn_counter", "relay-turn-counter")
OUR_HOOK_STATUS = "relay session handoff reminder"
REQUIRED_PLUGIN_SOURCE_FILES = (
    "SKILL.md",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
)
REQUIRED_HOOK_SOURCE_FILES = HOOK_PAYLOAD_FILES


class InstallError(Exception):
    """A refusal or hard error; main() prints it and exits non-zero."""


@dataclass
class Ctx:
    source: Path
    target: Path
    codex_home: Path | None = None
    claude_home: Path | None = None
    dry_run: bool = False
    force: bool = False
    update: bool = False
    repair: bool = False

    @property
    def universal_root(self) -> Path:
        return self.target

    @property
    def conv_dir(self) -> Path:
        return self.universal_root

    @property
    def canonical_plugin(self) -> Path:
        return self.universal_root.joinpath(*CANONICAL_PLUGIN_DEST)

    @property
    def canonical_hooks(self) -> Path:
        return self.universal_root / HOOK_SOURCE_DIR

    @property
    def relay_archive(self) -> Path:
        return self.universal_root / "convs"

    @property
    def conversation_database(self) -> Path:
        """Deprecated compatibility alias for relay_archive."""
        return self.relay_archive

    @property
    def codex_config_surface(self) -> Path:
        return self.codex_home or (Path.home() / ".codex")

    @property
    def claude_config_surface(self) -> Path:
        return self.claude_home or (Path.home() / ".claude")

    @property
    def codex_scan_entrypoint(self) -> Path:
        return self.codex_config_surface.joinpath(*AGENT_SKILL_ENTRYPOINT)

    @property
    def claude_scan_entrypoint(self) -> Path:
        return self.claude_config_surface.joinpath(*AGENT_SKILL_ENTRYPOINT)

    @property
    def codex_hooks_json(self) -> Path:
        return self.codex_config_surface / "hooks.json"

    @property
    def claude_settings_json(self) -> Path:
        return self.claude_config_surface / "settings.json"

    @property
    def pi_hook_file(self) -> Path:
        return Path.home().joinpath(*PI_HOOK_DEST)

    @property
    def omp_hook_file(self) -> Path:
        return Path.home().joinpath(*OMP_HOOK_DEST)

    def disp(self, path: Path) -> str:
        """Path relative to target when possible, else absolute - for readable output."""
        path = Path(path)
        try:
            return str(path.relative_to(self.target))
        except ValueError:
            return str(path)


def plugin_source(ctx: Ctx) -> Path:
    # Plugin components (SKILL.md/skills/manifests) live at the repo root normally,
    # but under <root>/relay when repairing an installed tree in place.
    if ctx.repair and _same_path(ctx.source, ctx.target) and ctx.canonical_plugin.is_dir():
        return ctx.canonical_plugin
    return ctx.source


def emit(msg: str) -> None:
    print(msg)


def _binary_filename(*, is_windows: bool | None = None) -> str:
    if is_windows is None:
        is_windows = os.name == "nt"
    return BINARY_BASENAME + (".exe" if is_windows else "")


def _binary_relpath(*, is_windows: bool | None = None) -> Path:
    return Path("bin") / _binary_filename(is_windows=is_windows)


def _build_release_binary(source: Path) -> Path:
    manifest = source / "Cargo.toml"
    if not manifest.is_file():
        raise InstallError(f"source does not contain Cargo.toml: {source}")
    try:
        subprocess.run(
            ["cargo", "build", "--release", "--manifest-path", str(manifest)],
            cwd=str(source),
            check=True,
        )
    except FileNotFoundError as exc:
        raise InstallError("cargo is required to build the Relay release binary") from exc
    except subprocess.CalledProcessError as exc:
        raise InstallError(f"cargo build --release failed with exit code {exc.returncode}") from exc
    binary = source / "target" / "release" / _binary_filename()
    if not binary.is_file():
        raise InstallError(f"cargo build completed but release binary is missing: {binary}")
    return binary


def _resolve_binary_source(ctx: Ctx, *, build: bool = True) -> Path:
    rel = _binary_relpath()
    if ctx.repair and _same_path(ctx.source, ctx.target):
        installed = ctx.target / rel
        if installed.is_file():
            return installed
        raise InstallError(f"repair source is missing installed binary: {installed}")
    installed = ctx.source / rel
    if installed.is_file():
        return installed
    if not build:
        release = ctx.source / "target" / "release" / _binary_filename()
        return release if release.is_file() else installed
    return _build_release_binary(ctx.source)


def _quote_powershell_arg(arg: str) -> str:
    # Single-quoted PowerShell strings are literal; an embedded apostrophe is
    # represented by two apostrophes.
    return "'" + arg.replace("'", "''") + "'"


def _quote_command(args: tuple[str, ...], *, powershell: bool) -> str:
    if powershell:
        # Codex evaluates its Windows hook command strings as PowerShell
        # source. A quoted executable is only a string expression unless the
        # call operator explicitly invokes it.
        return "& " + " ".join(_quote_powershell_arg(str(arg)) for arg in args)
    return shlex.join(args)


def _codex_hook_command(binary_path: Path, *, powershell: bool) -> str:
    return _quote_command((str(binary_path), "hook", "--agent", "codex"), powershell=powershell)


# --------------------------------------------------------------------------- links

def _is_reparse_point(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def link_kind(path: Path) -> str:
    """Classify what currently occupies `path`: missing | symlink | junction | copy | dir | file."""
    if not os.path.lexists(path):
        return "missing"
    if os.path.islink(path):
        return "symlink"
    if os.name == "nt" and _is_reparse_point(path):
        return "junction"
    if os.path.isdir(path):
        if (path / COPY_MARKER).exists():
            return "copy"
        return "dir"
    return "file"


def resolves_to(path: Path, conv_dir: Path) -> bool:
    try:
        a = os.path.normcase(os.path.realpath(path))
        b = os.path.normcase(os.path.realpath(conv_dir))
    except OSError:
        return False
    return a == b


def _next_bak(path: Path) -> Path:
    n = 1
    while True:
        cand = path.with_name(f"{path.name}.bak-{n}")
        if not os.path.lexists(cand):
            return cand
        n += 1


def remove_link(link_path: Path, ctx: Ctx) -> None:
    kind = link_kind(link_path)
    disp = ctx.disp(link_path)
    if kind == "missing":
        emit(f"link absent: {disp}")
        return
    if kind in ("symlink", "junction"):
        if ctx.dry_run:
            emit(f"would remove {kind}: {disp}")
            return
        # os.rmdir removes the reparse point (link) without touching the target on
        # Windows; POSIX symlinks (even to dirs) are removed with unlink.
        if os.name == "nt":
            os.rmdir(link_path)
        else:
            os.unlink(link_path)
        emit(f"removed {kind}: {disp}")
        return
    if kind == "copy":
        if ctx.dry_run:
            emit(f"would remove copy: {disp}")
            return
        shutil.rmtree(link_path)
        emit(f"removed copy: {disp}")
        return
    emit(f"skipped {disp}: not an installer-created link/copy ({kind}); leaving as-is")


# -------------------------------------------------------------------- plugin files

def iter_payload(source: Path, plugin_root: Path, *, binary_source: Path | None = None):
    for rel in PAYLOAD_FILES:
        p = source / rel
        if p.is_file():
            yield p, rel
    if binary_source is not None:
        yield binary_source, _binary_relpath().as_posix()
    for src_parts, dst_parts in PAYLOAD_FILE_MAP.items():
        p = plugin_root.joinpath(*src_parts)
        if p.is_file():
            yield p, "/".join(dst_parts)
    for d in PAYLOAD_DIRS:
        base = source / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_dir():
                continue
            if any(part in IGNORE_DIR_NAMES for part in f.relative_to(source).parts):
                continue
            if f.suffix in IGNORE_SUFFIXES:
                continue
            yield f, f.relative_to(source).as_posix()


def _same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _same_file(src: Path, dest: Path) -> bool:
    return dest.is_file() and filecmp.cmp(src, dest, shallow=False)


def _file_copy_action(src: Path, dest: Path, *, can_replace: bool) -> str:
    if not os.path.lexists(dest):
        return "create"
    if not dest.is_file():
        return "replace" if can_replace else "conflict"
    if filecmp.cmp(src, dest, shallow=False):
        return "skip"
    return "update" if can_replace else "conflict"


def _remove_non_file_at_file_path(path: Path) -> None:
    if not os.path.lexists(path) or path.is_file():
        return
    if path.is_dir() and not path.is_symlink():
        if os.name == "nt" and _is_reparse_point(path):
            os.rmdir(path)
        else:
            shutil.rmtree(path)
        return
    path.unlink()


def prune_stale_payload_files(ctx: Ctx, expected_rels: set[Path]) -> tuple[int, int]:
    """Remove stale files from installer-owned payload directories."""
    if not ctx.update and not ctx.force:
        return (0, 0)

    scan_roots = [ctx.universal_root / name for name in PAYLOAD_DIRS]
    scan_roots.append(ctx.universal_root / "scripts")
    removed_files = removed_dirs = 0
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            rel = path.relative_to(ctx.conv_dir)
            if rel in expected_rels:
                continue
            if path.is_dir() and not path.is_symlink():
                try:
                    next(path.iterdir())
                except StopIteration:
                    if ctx.dry_run:
                        emit(f"plugin files: would remove empty stale dir {ctx.disp(path)}")
                    else:
                        path.rmdir()
                    removed_dirs += 1
                except OSError:
                    pass
                continue
            if ctx.dry_run:
                emit(f"plugin files: would remove stale file {ctx.disp(path)}")
            else:
                path.unlink()
            removed_files += 1
    if removed_files or removed_dirs:
        verb = "would remove" if ctx.dry_run else "removed"
        emit(f"plugin files: {verb} {removed_files} stale file(s), {removed_dirs} empty dir(s)")
    return removed_files, removed_dirs
def copy_payload(ctx: Ctx) -> None:
    conv_dir = ctx.universal_root
    binary_source = _resolve_binary_source(ctx, build=not ctx.dry_run)
    plans: list[tuple[Path, Path, str]] = []
    conflicts: list[str] = []
    expected_rels: set[Path] = set()
    for src, rel in iter_payload(ctx.source, plugin_source(ctx), binary_source=binary_source):
        expected_rels.add(Path(rel))
        dest = conv_dir / Path(rel)
        action = _file_copy_action(src, dest, can_replace=ctx.update or ctx.force)
        if action == "conflict":
            conflicts.append(rel)
        else:
            plans.append((src, dest, action))

    if conflicts:
        raise InstallError(
            "plugin files would overwrite differing file(s); re-run with --update to refresh "
            "plugin files (preserves the Relay archive) or --force. Differing: "
            + ", ".join(sorted(conflicts))
        )

    created = updated = replaced = skipped = 0
    for src, dest, action in plans:
        if action == "skip":
            skipped += 1
            continue
        if ctx.dry_run:
            emit(f"would {action} {ctx.disp(dest)}")
            continue
        if action == "replace":
            _remove_non_file_at_file_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if action == "create":
            created += 1
        elif action == "replace":
            replaced += 1
        else:
            updated += 1
    verb = "would install" if ctx.dry_run else "plugin files"
    if ctx.dry_run:
        emit(f"{verb}: {sum(1 for _, _, a in plans if a != 'skip')} file(s) into {ctx.disp(conv_dir)} ({skipped} unchanged)")
    else:
        emit(
            f"plugin files: {created} created, {updated} updated, {replaced} replaced, "
            f"{skipped} unchanged in {ctx.disp(conv_dir)}"
        )
    prune_stale_payload_files(ctx, expected_rels)


# --------------------------------------------------------------------- Relay archive

def run_init(ctx: Ctx) -> None:
    root = ctx.universal_root
    db = ctx.relay_archive
    if ctx.dry_run:
        emit(f"would ensure universal installation root: {root}")
        emit(f"would ensure canonical installed plugin root: {ctx.canonical_plugin}")
        emit(f"would ensure canonical hook root: {ctx.canonical_hooks}")
        emit(f"would ensure Relay archive: {db}")
        return
    db.mkdir(parents=True, exist_ok=True)
    (root / ".semble").mkdir(parents=True, exist_ok=True)
    (root / "index.jsonl").touch(exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".semble/\nindex.jsonl\n__pycache__/\n", encoding="utf-8", newline="\n")
    emit(f"Relay archive: present at {ctx.disp(db)}")


# ------------------------------------------------------------------------- plugin plan

def planned_plugin_dests(agents: set[str], target: Path) -> list[tuple[str, Path]]:
    return [("canonical plugin", target.joinpath(*CANONICAL_PLUGIN_DEST))]


def planned_scan_entrypoints(agents: set[str], ctx: Ctx) -> list[tuple[str, Path]]:
    dests: list[tuple[str, Path]] = []
    if "claude" in agents:
        dests.append(("claude entrypoint", ctx.claude_scan_entrypoint))
    if "codex" in agents:
        dests.append(("codex entrypoint", ctx.codex_scan_entrypoint))
    return dests


def _create_directory_entrypoint(path: Path, target: Path) -> str:
    try:
        os.symlink(target, path, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        if os.name != "nt":
            raise InstallError(f"could not create symlink {path} -> {target}: {exc}") from exc
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return "junction"
        detail = (proc.stderr or proc.stdout or str(exc)).strip()
        raise InstallError(f"could not create junction {path} -> {target}: {detail}") from exc


def _remove_dir_or_link(path: Path) -> None:
    kind = link_kind(path)
    if kind in ("symlink", "junction"):
        if os.name == "nt":
            os.rmdir(path)
        else:
            os.unlink(path)
    else:
        shutil.rmtree(path)


def install_scan_entrypoint(ctx: Ctx, path: Path, label: str) -> None:
    target = ctx.canonical_plugin
    kind = link_kind(path)
    if kind in ("symlink", "junction") and resolves_to(path, target):
        emit(f"{label}: {kind} already points to {ctx.disp(target)} at {ctx.disp(path)}")
        return
    if kind != "missing":
        installer_owned = _plugin_is_ours(path)
        if not installer_owned and not ctx.force:
            raise InstallError(
                f"{ctx.disp(path)} exists and is not a relay-owned entrypoint; "
                f"use --force to replace it (backed up to <name>.bak-N)"
            )
        if ctx.dry_run:
            action = "replace" if installer_owned else "back up and replace"
            emit(f"{label}: would {action} {ctx.disp(path)}")
        else:
            if installer_owned:
                _remove_dir_or_link(path)
                emit(f"{label}: removed stale installer-owned entrypoint {ctx.disp(path)}")
            else:
                backup = _next_bak(path)
                os.rename(path, backup)
                emit(f"{label}: backed up foreign entrypoint: {ctx.disp(path)} -> {backup.name}")
    if ctx.dry_run:
        emit(f"{label}: would create entrypoint {ctx.disp(path)} -> {ctx.disp(target)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    created = _create_directory_entrypoint(path, target)
    emit(f"{label}: created {created} {ctx.disp(path)} -> {ctx.disp(target)}")


def remove_scan_entrypoint(ctx: Ctx, path: Path, label: str) -> None:
    kind = link_kind(path)
    if kind == "missing":
        emit(f"{label}: {ctx.disp(path)} absent")
        return
    if kind in ("symlink", "junction") and resolves_to(path, ctx.canonical_plugin):
        remove_link(path, ctx)
        return
    if _plugin_is_ours(path):
        if ctx.dry_run:
            emit(f"{label}: would remove {ctx.disp(path)}")
            return
        _remove_dir_or_link(path)
        emit(f"{label}: removed {ctx.disp(path)}")
        return
    emit(f"{label}: {ctx.disp(path)} not relay-owned; leaving as-is")


# ----------------------------------------------------------------------------- plugin

def iter_plugin_files(src: Path):
    for component in PLUGIN_COMPONENTS:
        base = src / component
        if component == "SKILL.md":
            if base.is_file():
                yield base, Path(component)
            continue
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(src)
            if any(part in IGNORE_DIR_NAMES for part in rel.parts):
                continue
            if f.suffix in IGNORE_SUFFIXES:
                continue
            yield f, rel


def prune_stale_plugin_files(ctx: Ctx, dest_root: Path, expected_rels: set[Path], label: str) -> tuple[int, int]:
    """Remove files left behind inside an installer-owned plugin copy."""
    if not dest_root.is_dir() or not (ctx.update or ctx.force):
        return (0, 0)

    removed_files = removed_dirs = 0
    for path in sorted(dest_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(dest_root)
        if rel in expected_rels:
            continue
        if path.is_dir() and not path.is_symlink():
            try:
                next(path.iterdir())
            except StopIteration:
                if ctx.dry_run:
                    emit(f"{label}: would remove empty stale dir {ctx.disp(path)}")
                else:
                    path.rmdir()
                removed_dirs += 1
            except OSError:
                pass
            continue
        if ctx.dry_run:
            emit(f"{label}: would remove stale file {ctx.disp(path)}")
        else:
            path.unlink()
        removed_files += 1
    if removed_files or removed_dirs:
        verb = "would remove" if ctx.dry_run else "removed"
        emit(f"{label}: {verb} {removed_files} stale file(s), {removed_dirs} empty dir(s)")
    return removed_files, removed_dirs


def _hook_tree_source(ctx: Ctx) -> Path | None:
    roots: list[Path] = []
    if ctx.repair and _same_path(ctx.source, ctx.target):
        # Same-source repair: prefer the pristine <T>/relay/hooks mirror
        # (regenerated from the single source hooks/ tree) so a corrupted <T>/hooks
        # self-heals; <T>/hooks would otherwise be its own source and dest (a no-op).
        roots.append(ctx.canonical_plugin / HOOK_SOURCE_DIR)
    roots.append(ctx.source / HOOK_SOURCE_DIR)
    for root in roots:
        if root.is_dir():
            return root
    return None


def iter_hook_files(src: Path):
    for rel_text in HOOK_PAYLOAD_FILES:
        f = src / Path(rel_text)
        if f.is_file():
            yield f, Path(rel_text)


def copy_canonical_hooks(ctx: Ctx) -> None:
    src_root = _hook_tree_source(ctx)
    label = "canonical hooks"
    if src_root is None:
        emit(f"{label}: source scaffold not found (hooks); skipping")
        return

    dest_root = ctx.canonical_hooks
    plans: list[tuple[Path, Path, str]] = []
    conflicts: list[str] = []
    expected_rels: set[Path] = set()
    for src, rel in iter_hook_files(src_root):
        expected_rels.add(rel)
        dest = dest_root / rel
        action = _file_copy_action(src, dest, can_replace=ctx.update or ctx.force)
        if action == "conflict":
            conflicts.append(rel.as_posix())
        else:
            plans.append((src, dest, action))

    if conflicts:
        raise InstallError(
            f"{label} would overwrite differing file(s); re-run with --update to refresh "
            "or --force. Differing: " + ", ".join(sorted(conflicts))
        )

    created = updated = replaced = skipped = 0
    for src, dest, action in plans:
        if action == "skip":
            skipped += 1
            continue
        if ctx.dry_run:
            emit(f"would {action} {ctx.disp(dest)}")
            continue
        if action == "replace":
            _remove_non_file_at_file_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if action == "create":
            created += 1
        elif action == "replace":
            replaced += 1
        else:
            updated += 1

    if ctx.dry_run:
        n = sum(1 for _, _, action in plans if action != "skip")
        prune_stale_plugin_files(ctx, dest_root, expected_rels, label)
        emit(f"{label}: would install {n} file(s) into {ctx.disp(dest_root)} ({skipped} unchanged)")
        return

    prune_stale_plugin_files(ctx, dest_root, expected_rels, label)
    emit(
        f"{label}: {created} created, {updated} updated, {replaced} replaced, "
        f"{skipped} unchanged in {ctx.disp(dest_root)}"
    )


def install_agent_plugin(ctx: Ctx, dest_root: Path, label: str) -> None:
    """Copy the shared Relay plugin skill group into an installed plugin root."""
    src = plugin_source(ctx)
    if not src.joinpath(*CLAUDE_PLUGIN_MANIFEST).is_file():
        emit(f"{label}: source scaffold not found (root .claude-plugin); skipping")
        return
    if dest_root.exists() and not _plugin_is_ours(dest_root):
        if not ctx.force:
            raise InstallError(
                f"{ctx.disp(dest_root)} exists and is not a relay-owned plugin; "
                f"use --force to replace it (backed up to <name>.bak-N)"
            )
        if ctx.dry_run:
            emit(f"would back up and replace foreign {ctx.disp(dest_root)}")
        else:
            backup = _next_bak(dest_root)
            os.rename(dest_root, backup)
            emit(f"backed up foreign plugin dir: {ctx.disp(dest_root)} -> {backup.name}")
    plans: list[tuple[Path, Path, str]] = []
    conflicts: list[str] = []
    expected_rels: set[Path] = set()
    for s, rel in iter_plugin_files(src):
        expected_rels.add(rel)
        dest = dest_root / rel
        action = _file_copy_action(s, dest, can_replace=ctx.update or ctx.force)
        if action == "conflict":
            conflicts.append(rel.as_posix())
        else:
            plans.append((s, dest, action))
    if conflicts:
        raise InstallError(
            f"{label} would overwrite differing file(s); re-run with --update to refresh "
            "or --force. Differing: " + ", ".join(sorted(conflicts))
        )
    created = updated = replaced = skipped = 0
    for s, dest, action in plans:
        if action == "skip":
            skipped += 1
            continue
        if ctx.dry_run:
            emit(f"would {action} {ctx.disp(dest)}")
            continue
        if action == "replace":
            _remove_non_file_at_file_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, dest)
        if action == "create":
            created += 1
        elif action == "replace":
            replaced += 1
        else:
            updated += 1
    if ctx.dry_run:
        n = sum(1 for _, _, a in plans if a != "skip")
        prune_stale_plugin_files(ctx, dest_root, expected_rels, label)
        emit(f"{label}: would install {n} file(s) into {ctx.disp(dest_root)} ({skipped} unchanged)")
        return
    prune_stale_plugin_files(ctx, dest_root, expected_rels, label)
    emit(
        f"{label}: {created} created, {updated} updated, {replaced} replaced, "
        f"{skipped} unchanged in {ctx.disp(dest_root)} (Relay skill group)"
    )
    if created or updated:
        emit(f"{label}: restart or reload the agent so the Relay skills are discovered")


def install_claude_plugin(ctx: Ctx) -> None:
    install_agent_plugin(ctx, ctx.canonical_plugin, "canonical plugin")
    install_scan_entrypoint(ctx, ctx.claude_scan_entrypoint, "claude entrypoint")


def _plugin_is_ours(dest_root: Path) -> bool:
    for parts in (CLAUDE_PLUGIN_MANIFEST, CODEX_PLUGIN_MANIFEST):
        manifest = dest_root.joinpath(*parts)
        if not manifest.is_file():
            continue
        try:
            data = _load_json(manifest)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("x-installed-by") == "relay":
            return True
    return False


def remove_agent_plugin(ctx: Ctx, dest_root: Path, label: str) -> None:
    disp = ctx.disp(dest_root)
    kind = link_kind(dest_root)
    if kind == "missing":
        emit(f"{label}: {disp} absent")
        return
    if not _plugin_is_ours(dest_root):
        emit(f"{label}: {disp} not relay-owned; leaving as-is")
        return
    if ctx.dry_run:
        emit(f"{label}: would remove {disp}")
        return
    if kind in ("symlink", "junction"):
        remove_link(dest_root, ctx)
        return
    shutil.rmtree(dest_root)
    emit(f"{label}: removed {disp}")


def remove_claude_plugin(ctx: Ctx) -> None:
    remove_scan_entrypoint(ctx, ctx.claude_scan_entrypoint, "claude entrypoint")
    remove_agent_plugin(ctx, ctx.canonical_plugin, "canonical plugin")


def legacy_agent_scan_entrypoints(ctx: Ctx) -> list[tuple[str, Path]]:
    """Real-home skills/conv entrypoints retained for status-only compatibility checks."""
    return [
        ("codex entrypoint (legacy)", ctx.codex_config_surface.joinpath(*LEGACY_AGENT_SKILL_ENTRYPOINT)),
        ("claude entrypoint (legacy)", ctx.claude_config_surface.joinpath(*LEGACY_AGENT_SKILL_ENTRYPOINT)),
    ]


# ----------------------------------------------------------------------------- hooks

def _hook_source(ctx: Ctx, *rel: str) -> Path | None:
    """Find a hook template from the current source or installed canonical plugin."""
    if ctx.repair and _same_path(ctx.source, ctx.target):
        bases = (ctx.canonical_plugin, ctx.source)
    elif ctx.repair:
        bases = (ctx.source, ctx.conv_dir)
    else:
        bases = (ctx.conv_dir, ctx.source)
    for base in bases:
        p = base.joinpath(*rel)
        if p.is_file():
            return p
    return None


def _copy_hook_file(src: Path | None, dest: Path, ctx: Ctx, label: str) -> None:
    if src is None:
        emit(f"{label}: adapter source not found; skipping")
        return
    dest_exists = os.path.lexists(dest)
    if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
        emit(f"{label}: already installed at {ctx.disp(dest)}")
        return
    if dest_exists:
        if ctx.update and dest.is_file() and _hook_file_is_ours(dest):
            if ctx.dry_run:
                emit(f"{label}: would update installer-owned hook at {ctx.disp(dest)}")
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            emit(f"{label}: updated installer-owned hook at {ctx.disp(dest)}")
            return
        if not ctx.force:
            raise InstallError(
                f"{label}: {ctx.disp(dest)} exists and differs from the relay hook; "
                "use --force to replace it (backed up to <name>.bak-N)"
            )
        if ctx.dry_run:
            emit(f"{label}: would back up and replace {ctx.disp(dest)}")
            return
        backup = _next_bak(dest)
        os.rename(dest, backup)
        emit(f"{label}: backed up existing hook: {ctx.disp(dest)} -> {backup.name}")
    if ctx.dry_run:
        emit(f"{label}: would install {ctx.disp(dest)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    emit(f"{label}: installed {ctx.disp(dest)}")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_hook_events(existing: dict, incoming: dict) -> int:
    """Append incoming hook event entries into `existing` (dedup by exact equality).
    Returns the number of entries added."""
    added = 0
    for event, entries in incoming.items():
        if not isinstance(entries, list):
            continue
        arr = existing.get(event)
        if not isinstance(arr, list):
            arr = []
        for entry in entries:
            if entry not in arr:
                arr.append(entry)
                added += 1
        existing[event] = arr
    return added


def _replace_our_hook_events(existing: dict, incoming: dict) -> tuple[int, int]:
    """Replace relay-owned hook entries while preserving unrelated entries."""
    removed = added = 0
    for event, entries in incoming.items():
        if not isinstance(entries, list):
            continue
        groups = existing.get(event)
        if not isinstance(groups, list):
            groups = []
        kept_groups = []
        event_removed = event_added = 0
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept_hooks = [hook for hook in inner if not _is_our_hook(hook)]
            event_removed += len(inner) - len(kept_hooks)
            if kept_hooks:
                kept_groups.append({**group, "hooks": kept_hooks})
        for entry in entries:
            if entry not in kept_groups:
                kept_groups.append(entry)
                event_added += 1
        if kept_groups != groups:
            removed += event_removed
            added += event_added
            existing[event] = kept_groups
    return removed, added


def _codex_hook_template_with_command(ctx: Ctx, template_hooks: dict) -> dict:
    binary_path = ctx.universal_root / _binary_relpath()
    incoming = json.loads(json.dumps(template_hooks))
    rewritten = 0
    for groups in incoming.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            hooks = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(hooks, list):
                continue
            for hook in hooks:
                if not _is_our_hook(hook):
                    continue
                hook["command"] = _codex_hook_command(binary_path, powershell=os.name == "nt")
                hook["commandWindows"] = _codex_hook_command(binary_path, powershell=True)
                rewritten += 1
    if rewritten == 0:
        raise InstallError("codex: template contains no relay hook entry to rewrite")
    if _has_template_token(incoming):
        raise InstallError("codex: template placeholder remained after hook command rewrite")
    return incoming


def _claude_hook_template_with_invocation(ctx: Ctx, template_hooks: dict) -> dict:
    incoming = json.loads(json.dumps(template_hooks))
    binary_path = ctx.universal_root / _binary_relpath()
    for groups in incoming.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            hooks = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(hooks, list):
                continue
            for hook in hooks:
                if not _is_our_hook(hook):
                    continue
                hook.pop("commandWindows", None)
                hook.pop("shell", None)
                hook["command"] = str(binary_path)
                hook["args"] = ["hook", "--agent", "claude"]
    return incoming


def wire_claude_hook(ctx: Ctx) -> None:
    snippet_path = _hook_source(ctx, "hooks", "claude", "settings-snippet.json")
    if snippet_path is None:
        emit("claude: hook snippet not found (hooks/claude/settings-snippet.json) - re-run later or wire manually; skipping")
        return
    try:
        snippet = _load_json(snippet_path)
    except Exception as exc:
        emit(f"claude: could not parse {ctx.disp(snippet_path)} ({exc}); skipping")
        return
    incoming = snippet.get("hooks", snippet) if isinstance(snippet, dict) else None
    if not isinstance(incoming, dict):
        emit("claude: settings-snippet.json has no 'hooks' object; skipping")
        return
    incoming = _claude_hook_template_with_invocation(ctx, incoming)

    settings_path = ctx.claude_settings_json
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = _load_json(settings_path)
        except Exception:
            emit(f"claude: existing {ctx.disp(settings_path)} is not valid JSON; skipping to avoid clobbering")
            return
        if not isinstance(settings, dict):
            emit(f"claude: existing {ctx.disp(settings_path)} is JSON but not an object; skipping to avoid clobbering")
            return
    existing_hooks = settings.get("hooks")
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}
    removed, added = _replace_our_hook_events(existing_hooks, incoming)
    changed = bool(removed or added)
    if not changed:
        emit(f"claude: hooks already present in {ctx.disp(settings_path)}; no change")
        return
    if ctx.dry_run:
        emit(f"claude: would write {ctx.disp(settings_path)}")
        return
    settings["hooks"] = existing_hooks
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backed_up = settings_path.is_file()
    if backed_up:
        shutil.copy2(settings_path, settings_path.with_name(settings_path.name + ".bak"))
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    suffix = " (.bak saved)" if backed_up else ""
    emit(f"claude: wrote {ctx.disp(settings_path)}{suffix}")


def wire_codex_hook(ctx: Ctx) -> None:
    template_path = _hook_source(ctx, "hooks", "codex", "hooks.json")
    if template_path is None:
        emit("codex: hooks.json template not found; skipping")
        return
    try:
        template = _load_json(template_path)
    except Exception as exc:
        emit(f"codex: could not parse {ctx.disp(template_path)} ({exc}); skipping")
        return
    incoming = template.get("hooks") if isinstance(template, dict) else None
    if not isinstance(incoming, dict):
        emit("codex: template has no 'hooks' object; skipping")
        return
    incoming = _codex_hook_template_with_command(ctx, incoming)

    dest = ctx.codex_hooks_json
    data: dict = {}
    if dest.is_file():
        try:
            data = _load_json(dest)
        except Exception:
            emit(f"codex: existing {ctx.disp(dest)} is not valid JSON; skipping to avoid clobbering")
            return
        if not isinstance(data, dict):
            emit(f"codex: existing {ctx.disp(dest)} is JSON but not an object; skipping to avoid clobbering")
            return
    existing_hooks = data.get("hooks")
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}
    removed, added = _replace_our_hook_events(existing_hooks, incoming)
    changed = bool(removed or added)
    if changed and not ctx.dry_run:
        data["hooks"] = existing_hooks
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            shutil.copy2(dest, dest.with_name(dest.name + ".bak"))
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        emit(f"codex: wrote {ctx.disp(dest)}")
        emit("codex: hook command changed; Codex may require hook reapproval or retrust")
    elif changed and ctx.dry_run:
        emit(f"codex: would write {ctx.disp(dest)}")
        emit("codex: hook command would change; Codex may require hook reapproval or retrust")
    else:
        emit(f"codex: hook already present in {ctx.disp(dest)}; no change")
    emit("codex: NOTE hooks are enabled by default; set `hooks = false` under `[features]` in ~/.codex/config.toml only to disable them")


def wire_hooks(ctx: Ctx, hooks: set[str]) -> None:
    if "claude" in hooks:
        wire_claude_hook(ctx)
    if "pi" in hooks:
        _copy_hook_file(_hook_source(ctx, "hooks", "pi", "relay-turn-counter.ts"),
                        ctx.pi_hook_file, ctx, "pi")
    if "omp" in hooks:
        _copy_hook_file(_hook_source(ctx, "hooks", "pi", "relay-turn-counter.ts"),
                        ctx.omp_hook_file, ctx, "omp")
    if "codex" in hooks:
        wire_codex_hook(ctx)


def print_hook_instructions(ctx: Ctx) -> None:
    emit("hooks: none wired (default). To enable auto-save reminders every 10 user turns, re-run with --hooks:")
    emit("  python scripts/install.py --hooks claude,pi,omp,codex   (or --hooks all)")
    emit("  codex hooks are enabled by default; use `[features].hooks = false` only to disable them")


# --------------------------------------------------------------------------- uninstall

def _command_text(entry: dict) -> str:
    # `args` is included so command+args hook forms are recognized as
    # Relay-owned by _is_our_hook / _hook_entry_points_to.
    parts = [str(entry.get(k, "")) for k in ("command", "commandWindows")]
    args = entry.get("args")
    if isinstance(args, list):
        parts.extend(str(a) for a in args)
    return " ".join(parts)


def _has_template_token(value) -> bool:
    if isinstance(value, str):
        return "__RELAY_" in value
    if isinstance(value, dict):
        return any(_has_template_token(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_template_token(v) for v in value)
    return False


def _hook_file_is_ours(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lowered = text.lower()
    return "relay" in lowered and any(marker in lowered for marker in OUR_HOOK_MARKERS)


def _is_our_hook(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("x-installed-by") == "relay":
        return True
    if str(entry.get("statusMessage", "")).strip().lower() == OUR_HOOK_STATUS:
        return True
    text = _command_text(entry)
    return any(marker in text for marker in OUR_HOOK_MARKERS)


def _strip_our_hooks(hooks_obj: dict) -> int:
    """Remove relay-owned command entries from a nested hooks object. Returns count removed."""
    removed = 0
    for event in list(hooks_obj.keys()):
        groups = hooks_obj.get(event)
        if not isinstance(groups, list):
            continue
        new_groups = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list):
                kept = [h for h in inner if not _is_our_hook(h)]
                removed += len(inner) - len(kept)
                if kept:
                    new_groups.append({**group, "hooks": kept})
            else:
                new_groups.append(group)
        if new_groups:
            hooks_obj[event] = new_groups
        else:
            del hooks_obj[event]
    return removed


def _unwire_json_hooks(path: Path, ctx: Ctx, label: str, remove_empty_file: bool) -> None:
    if not path.is_file():
        emit(f"{label}: {ctx.disp(path)} absent")
        return
    try:
        data = _load_json(path)
    except Exception:
        emit(f"{label}: {ctx.disp(path)} not valid JSON; leaving as-is")
        return
    if not isinstance(data, dict):
        emit(f"{label}: {ctx.disp(path)} JSON is not an object; leaving as-is")
        return
    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        emit(f"{label}: no relay hook entries in {ctx.disp(path)}")
        return
    removed = _strip_our_hooks(hooks_obj)
    if removed == 0:
        emit(f"{label}: no relay hook entries in {ctx.disp(path)}")
        return
    if ctx.dry_run:
        emit(f"{label}: would remove {removed} hook entr{'y' if removed == 1 else 'ies'} from {ctx.disp(path)}")
        return
    shutil.copy2(path, path.with_name(path.name + ".bak"))
    if not hooks_obj and remove_empty_file and set(data.keys()) <= {"hooks"}:
        path.unlink()
        emit(f"{label}: removed {ctx.disp(path)} (.bak saved)")
        return
    if hooks_obj:
        data["hooks"] = hooks_obj
    else:
        data.pop("hooks", None)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    emit(f"{label}: removed {removed} hook entr{'y' if removed == 1 else 'ies'} from {ctx.disp(path)} (.bak saved)")


def _remove_hook_file(src: Path | None, path: Path, ctx: Ctx, label: str) -> None:
    if not os.path.lexists(path):
        emit(f"{label}: {ctx.disp(path)} absent")
        return
    if not path.is_file() or not (
        _hook_file_is_ours(path) or (src is not None and filecmp.cmp(src, path, shallow=False))
    ):
        emit(f"{label}: {ctx.disp(path)} not a relay-owned hook; leaving as-is")
        return
    if ctx.dry_run:
        emit(f"{label}: would remove {ctx.disp(path)}")
        return
    path.unlink()
    emit(f"{label}: removed {ctx.disp(path)}")


def do_uninstall(ctx: Ctx) -> int:
    emit(f"uninstall from universal installation root: {ctx.target}")
    remove_scan_entrypoint(ctx, ctx.codex_scan_entrypoint, "codex entrypoint")
    remove_claude_plugin(ctx)
    _remove_hook_file(_hook_source(ctx, "hooks", "pi", "relay-turn-counter.ts"),
                      ctx.pi_hook_file, ctx, "pi")
    _remove_hook_file(_hook_source(ctx, "hooks", "pi", "relay-turn-counter.ts"),
                      ctx.omp_hook_file, ctx, "omp")
    _unwire_json_hooks(ctx.codex_hooks_json, ctx, "codex", remove_empty_file=True)
    _unwire_json_hooks(ctx.claude_settings_json, ctx, "claude", remove_empty_file=False)
    emit(f"left Relay archive untouched: {ctx.relay_archive}")
    return 0


# ------------------------------------------------------------------------------ status

def _expected_file_problems(expected_files, dest_root: Path) -> tuple[list[Path], list[Path]]:
    missing: list[Path] = []
    stale: list[Path] = []
    for src, rel in expected_files:
        rel_path = Path(rel)
        dest = dest_root / rel_path
        if not src.is_file() or not dest.is_file():
            missing.append(rel_path)
        elif not _same_file(src, dest):
            stale.append(rel_path)
    return missing, stale


def _payload_artifact_problems(ctx: Ctx) -> tuple[list[Path], list[Path]]:
    binary_source = _resolve_binary_source(ctx, build=False)
    return _expected_file_problems(
        iter_payload(ctx.source, plugin_source(ctx), binary_source=binary_source),
        ctx.universal_root,
    )


def _plugin_artifact_problems(ctx: Ctx) -> tuple[list[Path], list[Path]]:
    src = plugin_source(ctx)
    if not (src / "SKILL.md").is_file():
        return [Path("SKILL.md")], []
    return _expected_file_problems(iter_plugin_files(src), ctx.canonical_plugin)


def _hook_artifact_problems(ctx: Ctx) -> tuple[list[Path], list[Path]]:
    src = _hook_tree_source(ctx)
    if src is None:
        return [Path(HOOK_SOURCE_DIR)], []
    return _expected_file_problems(iter_hook_files(src), ctx.canonical_hooks)


def _artifact_state(missing: list[Path], stale: list[Path]) -> str:
    if missing and stale:
        return "missing/stale"
    if missing:
        return "missing"
    if stale:
        return "stale"
    return "present"


def _fmt_rels(rels: list[Path], limit: int = 8) -> str:
    shown = [rel.as_posix() for rel in rels[:limit]]
    if len(rels) > limit:
        shown.append(f"... +{len(rels) - limit} more")
    return ", ".join(shown)


def _emit_artifact_problems(label: str, missing: list[Path], stale: list[Path]) -> None:
    if missing:
        emit(f"{label}: missing {len(missing)} required artifact(s): {_fmt_rels(missing)}")
    if stale:
        emit(f"{label}: stale {len(stale)} required artifact(s): {_fmt_rels(stale)}")


def _json_hook_entries(path: Path) -> list[dict] | None:
    try:
        data = _load_json(path)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        return []
    entries: list[dict] = []
    for groups in hooks_obj.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list):
                entries.extend(hook for hook in inner if isinstance(hook, dict))
    return entries


def _norm_hook_text(value: object) -> str:
    return str(value).replace("\\", "/").lower()


def _hook_text_points_to(value: object, expected_script: Path) -> bool:
    command = _norm_hook_text(value)
    expected = str(expected_script)
    candidates = (expected, _quote_powershell_arg(expected))
    return any(_norm_hook_text(candidate) in command for candidate in candidates)


def _hook_entry_points_to(entry: dict, expected_script: Path) -> bool:
    return _hook_text_points_to(_command_text(entry), expected_script)


def _hook_invocation_is_current(entry: dict, expected_script: Path, *, agent: str) -> bool:
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    if agent == "codex":
        command_windows = entry.get("commandWindows")
        expected_command = _codex_hook_command(expected_script, powershell=os.name == "nt")
        expected_windows = _codex_hook_command(expected_script, powershell=True)
        return (
            command == expected_command
            and command_windows == expected_windows
            and "args" not in entry
            and "shell" not in entry
        )
    if agent == "claude":
        args = entry.get("args")
        return (
            command == str(expected_script)
            and args == ["hook", "--agent", "claude"]
            and "commandWindows" not in entry
            and "shell" not in entry
        )
    return False


def _json_hook_status(path: Path, expected_script: Path, *, agent: str) -> str:
    if not path.is_file():
        return "not wired"
    entries = _json_hook_entries(path)
    if entries is None:
        return "invalid hook JSON"
    ours = [entry for entry in entries if _is_our_hook(entry)]
    if not ours:
        return "not wired"
    canonical = sum(1 for entry in ours if _hook_entry_points_to(entry, expected_script))
    if canonical == len(ours):
        if not all(
            _hook_invocation_is_current(entry, expected_script, agent=agent) for entry in ours
        ):
            return "wired -> canonical hooks (stale invocation)"
        return "wired -> canonical hooks"
    if canonical:
        return "mixed canonical/stale hook targets"
    return "wired outside canonical hook root"


def _owned_hook_commands(path: Path) -> list[tuple[str, str]]:
    """Return configured primary/Windows commands for installer-owned hooks."""
    entries = _json_hook_entries(path)
    if not entries:
        return []
    commands: list[tuple[str, str]] = []
    for entry in entries:
        if not _is_our_hook(entry):
            continue
        command = entry.get("command")
        command_windows = entry.get("commandWindows")
        commands.append(
            (
                command.strip() if isinstance(command, str) else "<missing>",
                command_windows.strip() if isinstance(command_windows, str) else "",
            )
        )
    return commands


def _legacy_hook_config_status(path: Path) -> str:
    if not os.path.lexists(path):
        return "absent"
    if not path.is_file():
        return f"foreign {link_kind(path)}"
    entries = _json_hook_entries(path)
    if entries is None:
        return "foreign file (invalid hook JSON)"
    if any(_is_our_hook(entry) for entry in entries):
        return "stale installer-owned hook config"
    return "foreign file (no relay hook entries)"


def _hook_wired_claude(ctx: Ctx) -> bool:
    path = ctx.claude_settings_json
    if not path.is_file():
        return False
    try:
        data = _load_json(path)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        return False
    for groups in hooks_obj.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list) and any(_is_our_hook(h) for h in inner):
                return True
    return False


def _hook_wired_codex(ctx: Ctx) -> bool:
    path = ctx.codex_hooks_json
    if not path.is_file():
        return False
    try:
        data = _load_json(path)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        return False
    for groups in hooks_obj.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list) and any(_is_our_hook(h) for h in inner):
                return True
    return False


def _scan_entrypoint_status(ctx: Ctx, path: Path) -> str:
    kind = link_kind(path)
    if kind in ("symlink", "junction"):
        if resolves_to(path, ctx.canonical_plugin):
            return f"{kind} -> canonical plugin"
        return f"{kind} -> wrong target"
    if kind == "missing":
        return "missing"
    if kind == "copy" or _plugin_is_ours(path):
        return "stale installer-owned copy (not a live link)"
    return f"foreign {kind}"


def do_status(ctx: Ctx) -> int:
    root = ctx.universal_root
    payload_missing, payload_stale = _payload_artifact_problems(ctx)
    plugin_missing, plugin_stale = _plugin_artifact_problems(ctx)
    hook_missing, hook_stale = _hook_artifact_problems(ctx)
    runtime_files_present = not payload_missing and not payload_stale
    canonical_plugin_owned = _plugin_is_ours(ctx.canonical_plugin)
    canonical_plugin_present = canonical_plugin_owned and not plugin_missing and not plugin_stale
    canonical_hooks_present = not hook_missing and not hook_stale
    database_present = ctx.relay_archive.is_dir() and (root / "index.jsonl").exists()

    emit(f"plugin_source = {ctx.source}")
    emit(f"universal_installation_root = {ctx.universal_root}")
    emit(f"plugin_installation_root = {ctx.target}")
    emit(f"canonical_plugin_root = {ctx.canonical_plugin}")
    emit(f"canonical_hook_root = {ctx.canonical_hooks}")
    emit(f"codex_config_surface = {ctx.codex_config_surface}")
    emit(f"claude_config_surface = {ctx.claude_config_surface}")
    emit(f"relay_archive = {ctx.relay_archive}")
    emit(f"conversation_database = {ctx.relay_archive}")
    emit(f"runtime files: {'present' if runtime_files_present else _artifact_state(payload_missing, payload_stale)}")
    _emit_artifact_problems("runtime files", payload_missing, payload_stale)
    emit(f"plugin files: {'present' if canonical_plugin_present else _artifact_state(plugin_missing, plugin_stale)}")
    _emit_artifact_problems("canonical plugin artifacts", plugin_missing, plugin_stale)
    emit(f"Relay archive: {'present' if database_present else 'missing'} (convs/, index.jsonl)")

    if canonical_plugin_present:
        skills_dir = ctx.canonical_plugin / "skills"
        n = sum(1 for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else 0
        emit(f"canonical plugin: present ({n} skills)")
    elif canonical_plugin_owned:
        skills_dir = ctx.canonical_plugin / "skills"
        n = sum(1 for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else 0
        emit(f"canonical plugin: incomplete ({n} skills; {len(plugin_missing)} missing, {len(plugin_stale)} stale)")
    elif ctx.canonical_plugin.exists():
        emit("canonical plugin: foreign dir (not relay-owned)")
    else:
        emit("canonical plugin: not installed")
    emit(f"canonical hooks: {'present' if canonical_hooks_present else _artifact_state(hook_missing, hook_stale)}")
    _emit_artifact_problems("canonical hook artifacts", hook_missing, hook_stale)
    emit(f"codex entrypoint: {_scan_entrypoint_status(ctx, ctx.codex_scan_entrypoint)} ({ctx.codex_scan_entrypoint})")
    emit(f"claude entrypoint: {_scan_entrypoint_status(ctx, ctx.claude_scan_entrypoint)} ({ctx.claude_scan_entrypoint})")
    for _label, _path in legacy_agent_scan_entrypoints(ctx):
        emit(f"{_label}: {_scan_entrypoint_status(ctx, _path)} ({_path})")

    for label, parts in (("legacy claude .claude/skills/conversate", LEGACY_CLAUDE_LINK),
                         ("legacy agents .agents/skills/conversate", LEGACY_AGENTS_LINK)):
        link = ctx.target.joinpath(*parts)
        kind = link_kind(link)
        if kind in ("symlink", "junction"):
            ok = "-> .relay" if resolves_to(link, root) else "-> (other target!)"
            emit(f"link {label}: {kind} {ok}")
        elif kind == "copy":
            emit(f"link {label}: copy (not a live link)")
        elif kind == "missing":
            emit(f"link {label}: missing")
        else:
            emit(f"link {label}: foreign {kind} (not installer-created)")

    for label, parts in (("legacy canonical plugin conversate", LEGACY_CANONICAL_PLUGIN_DEST),
                         ("older canonical plugin conv", OLDER_CANONICAL_PLUGIN_DEST),
                         ("legacy claude plugin .claude/skills/conv", LEGACY_CLAUDE_PLUGIN_DEST),
                         ("legacy agents plugin .agents/skills/conv", LEGACY_AGENTS_PLUGIN_DEST)):
        plugin_root = ctx.target.joinpath(*parts)
        if _plugin_is_ours(plugin_root):
            skills_dir = plugin_root / "skills"
            n = sum(1 for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else 0
            emit(f"{label}: stale installer-owned copy ({n} skills)")
        elif plugin_root.exists():
            emit(f"{label}: foreign dir (not relay-owned)")
        else:
            emit(f"{label}: absent")

    for label, parts in (("legacy nested codex hook .codex/hooks.json", CODEX_HOOKS_JSON),
                         ("legacy nested claude hook .claude/settings.json", CLAUDE_SETTINGS)):
        path = ctx.target.joinpath(*parts)
        emit(f"{label}: {_legacy_hook_config_status(path)} ({path})")

    binary_path = ctx.universal_root / _binary_relpath()
    emit(f"hook claude: {_json_hook_status(ctx.claude_settings_json, binary_path, agent='claude')} ({ctx.claude_settings_json})")
    emit(f"hook pi: {'wired' if ctx.pi_hook_file.is_file() else 'not wired'}")
    emit(f"hook omp: {'wired' if ctx.omp_hook_file.is_file() else 'not wired'}")
    emit(f"hook codex: {_json_hook_status(ctx.codex_hooks_json, binary_path, agent='codex')} ({ctx.codex_hooks_json})")
    for command, command_windows in _owned_hook_commands(ctx.codex_hooks_json):
        emit(f"hook codex command: {command}")
        if command_windows and command_windows != command:
            emit(f"hook codex commandWindows: {command_windows}")
    return 0


def _installed_hook_set(ctx: Ctx) -> set[str]:
    hooks: set[str] = set()
    if _hook_wired_claude(ctx):
        hooks.add("claude")
    if ctx.pi_hook_file.is_file() and _hook_file_is_ours(ctx.pi_hook_file):
        hooks.add("pi")
    if ctx.omp_hook_file.is_file() and _hook_file_is_ours(ctx.omp_hook_file):
        hooks.add("omp")
    if _hook_wired_codex(ctx):
        hooks.add("codex")
    return hooks


def _available_repair_hook_set(ctx: Ctx) -> set[str]:
    hooks: set[str] = set()
    if _hook_source(ctx, "hooks", "claude", "settings-snippet.json") is not None:
        hooks.add("claude")
    if _hook_source(ctx, "hooks", "codex", "hooks.json") is not None:
        hooks.add("codex")
    if _hook_source(ctx, "hooks", "pi", "relay-turn-counter.ts") is not None:
        if not os.path.lexists(ctx.pi_hook_file) or (
            ctx.pi_hook_file.is_file() and _hook_file_is_ours(ctx.pi_hook_file)
        ):
            hooks.add("pi")
        if not os.path.lexists(ctx.omp_hook_file) or (
            ctx.omp_hook_file.is_file() and _hook_file_is_ours(ctx.omp_hook_file)
        ):
            hooks.add("omp")
    return hooks


def _repair_hook_set(ctx: Ctx, value: str | None) -> set[str]:
    if value is None:
        return _installed_hook_set(ctx) | _available_repair_hook_set(ctx)
    return _parse_set(value, ALL_AGENTS, "hooks", allow_none=True)


def _update_hook_set(ctx: Ctx, value: str | None) -> set[str]:
    """Refresh hooks this installation already owns without enabling new hooks.

    `--update` refreshes the canonical hook files, so it must also regenerate the
    corresponding live agent configuration.  Unlike repair, though, a routine
    update must not opt an installation into hook hosts it had not selected.
    """
    if value is None:
        return _installed_hook_set(ctx)
    return _parse_set(value, ALL_AGENTS, "hooks", allow_none=True)


def _repair_source_missing(ctx: Ctx) -> list[str]:
    source = ctx.source
    plugin = plugin_source(ctx)
    missing: list[str] = []
    for rel in PAYLOAD_FILES:
        if not (source / rel).is_file():
            missing.append(rel)
    binary = (
        ctx.target / _binary_relpath()
        if (ctx.repair and _same_path(ctx.source, ctx.target))
        else _resolve_binary_source(ctx, build=False)
    )
    if not binary.is_file() and (ctx.repair and _same_path(ctx.source, ctx.target)):
        missing.append(_binary_relpath().as_posix())
    for src_parts in PAYLOAD_FILE_MAP:
        if not plugin.joinpath(*src_parts).is_file():
            missing.append("/".join(src_parts))

    for rel in REQUIRED_PLUGIN_SOURCE_FILES:
        if not (plugin / Path(rel)).is_file():
            missing.append(rel)

    hook_root = _hook_tree_source(ctx)
    if hook_root is None:
        missing.append(HOOK_SOURCE_DIR)
    else:
        for rel in REQUIRED_HOOK_SOURCE_FILES:
            if not (hook_root / Path(rel)).is_file():
                missing.append(f"{HOOK_SOURCE_DIR}/{rel}")
    return missing


def validate_repair_source(ctx: Ctx) -> None:
    missing = _repair_source_missing(ctx)
    if not missing:
        return
    raise InstallError(
        "repair source is missing installer artifact(s): "
        + ", ".join(missing)
        + "; run scripts/install.py from a complete relay checkout to restore installer-owned files"
    )


def do_repair(ctx: Ctx, agents: set[str], hooks: set[str]) -> int:
    emit(f"repair universal installation root: {ctx.target}")
    validate_repair_source(ctx)
    copy_payload(ctx)
    copy_canonical_hooks(ctx)
    run_init(ctx)
    for label, dest in planned_plugin_dests(agents, ctx.target):
        install_agent_plugin(ctx, dest, label)
    for label, dest in planned_scan_entrypoints(agents, ctx):
        install_scan_entrypoint(ctx, dest, label)
    if hooks:
        emit(f"repair hooks: {', '.join(sorted(hooks))}")
        wire_hooks(ctx, hooks)
    else:
        emit("repair hooks: none selected (--hooks none or no available hook sources)")
    emit("done" if not ctx.dry_run else "dry-run complete")
    return 0


# -------------------------------------------------------------------------------- CLI

def _parse_set(value: str, valid: tuple[str, ...], label: str, allow_none: bool) -> set[str]:
    value = (value or "").strip().lower()
    if value in ("", "all"):
        return set() if (value == "" and allow_none) else set(valid)
    if value == "none":
        if allow_none:
            return set()
        raise InstallError(f"--{label} does not accept 'none'")
    out: set[str] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if item not in valid:
            raise InstallError(f"--{label}: unknown value {item!r}; choose from {', '.join(valid)}, all"
                               + (", none" if allow_none else ""))
        out.add(item)
    return out


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="install.py",
        description="Install Relay plugin files into the Relay installation root and create the Relay archive.",
    )
    p.add_argument("--target", help="Plugin installation root (default: ~/.relay)")
    p.add_argument("--source", help="Relay checkout to install from (default: this script's repo root)")
    p.add_argument("--agents", default="all", help="comma list of claude,pi,omp,codex or 'all' (default: all)")
    p.add_argument("--hooks", default=None, help="comma list of claude,pi,omp,codex, 'all', or 'none' (default: none; --update refreshes already-installed hooks; --repair defaults to available installer-owned hooks)")
    p.add_argument("--update", action="store_true", help="refresh plugin files while preserving the Relay archive")
    p.add_argument("--repair", "--doctor-fix", dest="repair", action="store_true", help="repair installer-owned plugin files and selected/already-wired hooks while preserving convs/")
    p.add_argument("--force", action="store_true", help="overwrite differing plugin files and replace foreign plugin dirs (backs up first)")
    p.add_argument("--uninstall", action="store_true", help="remove Relay-owned plugins and hooks; leaves the Relay archive intact")
    p.add_argument("--status", action="store_true", help="report install state for the Plugin installation root and exit")
    p.add_argument("--dry-run", action="store_true", help="print planned actions; change nothing")
    p.add_argument("--claude-plugin-only", action="store_true", help="only (un)install the canonical Relay plugin and ~/.claude/skills/relay entrypoint")
    return p.parse_args(argv)


def resolve_source(args) -> Path:
    if args.source:
        src = Path(args.source).expanduser().resolve()
    else:
        src = Path(__file__).resolve().parent.parent
    if not (src / "SKILL.md").is_file() or not (src / "scripts" / "install.py").is_file():
        raise InstallError(f"source does not look like a relay checkout or installed root (missing SKILL.md or scripts/install.py): {src}")
    return src


def resolve_target(args) -> Path:
    if args.target:
        return Path(args.target).expanduser().resolve()
    return (Path.home() / RELAY_DIRNAME).expanduser().resolve()


def main(argv=None) -> int:
    try:
        args = parse_args(argv)
        source = resolve_source(args)
        target = resolve_target(args)
        legacy_root = (Path.home() / ".conversate").expanduser().resolve()
        try:
            target.relative_to(legacy_root)
        except ValueError:
            pass
        else:
            raise InstallError(
                "refusing to use the legacy Conversate archive as a Relay installation root: "
                f"{legacy_root}; leave it untouched and run `relay import --from {legacy_root}` instead"
            )

        if target == source and not args.repair:
            raise InstallError(
                "refusing to install into the Plugin source itself; pass --target DIR "
                "for a separate Plugin installation root"
            )
        if target.exists() and not target.is_dir():
            raise InstallError(f"--target exists and is not a directory: {target}")

        if args.repair and args.uninstall:
            raise InstallError("--repair/--doctor-fix cannot be combined with --uninstall")

        ctx = Ctx(
            source=source,
            target=target,
            dry_run=args.dry_run,
            force=args.force,
            update=args.update or args.repair,
            repair=args.repair,
        )

        if args.status:
            return do_status(ctx)
        if args.claude_plugin_only:
            emit(f"plugin_source = {source}")
            emit(f"source: {source}")
            emit(f"universal_installation_root = {target}")
            emit(f"plugin_installation_root = {target}")
            emit(f"canonical_plugin_root = {ctx.canonical_plugin}")
            emit(f"claude_config_surface = {ctx.claude_config_surface}")
            if ctx.dry_run:
                emit("dry-run: no changes will be made")
            if args.uninstall:
                remove_claude_plugin(ctx)
            else:
                install_claude_plugin(ctx)
            emit("done" if not ctx.dry_run else "dry-run complete")
            return 0
        if args.uninstall:
            return do_uninstall(ctx)

        agents = _parse_set(args.agents, ALL_AGENTS, "agents", allow_none=False)
        if args.repair:
            hooks = _repair_hook_set(ctx, args.hooks)
        elif args.update:
            hooks = _update_hook_set(ctx, args.hooks)
        else:
            hooks = _parse_set(args.hooks or "none", ALL_AGENTS, "hooks", allow_none=True)

        emit(f"plugin_source = {source}")
        emit(f"source: {source}")
        emit(f"universal_installation_root = {target}")
        emit(f"plugin_installation_root = {target}")
        emit(f"canonical_plugin_root = {ctx.canonical_plugin}")
        emit(f"canonical_hook_root = {ctx.canonical_hooks}")
        emit(f"codex_config_surface = {ctx.codex_config_surface}")
        emit(f"claude_config_surface = {ctx.claude_config_surface}")
        emit(f"relay_archive = {ctx.relay_archive}")
        emit(f"conversation_database = {ctx.relay_archive}")
        if ctx.dry_run:
            emit("dry-run: no changes will be made")
        if args.repair:
            return do_repair(ctx, agents, hooks)

        copy_payload(ctx)
        copy_canonical_hooks(ctx)
        run_init(ctx)
        for label, dest in planned_plugin_dests(agents, target):
            install_agent_plugin(ctx, dest, label)
        for label, dest in planned_scan_entrypoints(agents, ctx):
            install_scan_entrypoint(ctx, dest, label)
        if hooks:
            wire_hooks(ctx, hooks)
        else:
            print_hook_instructions(ctx)

        emit("done" if not ctx.dry_run else "dry-run complete")
        return 0
    except InstallError as exc:
        print(f"install.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
