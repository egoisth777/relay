#!/usr/bin/env python3
"""conversate cross-agent installer.

Installs the conversate skill into a project as `<target>/.conversate/`, initializes
the conversation store, and links the skill into each agent harness's discovery path.
Two symlinks (deduped) cover all four supported agents:

  .claude/skills/conversate  -> .conversate   (Claude Code; oh-my-pi also reads this)
  .agents/skills/conversate  -> .conversate   (pi, oh-my-pi, Codex)

Windows link strategy: os.symlink (needs Developer Mode / admin) -> NTFS junction
(`mklink /J`, no privilege needed) -> copy of the minimal skill payload (loud warning).

Stdlib only. Conversation data under `.conversate/convs/` is never deleted or
overwritten by any flag.

Usage:
  python scripts/install.py [--target DIR] [--source DIR]
        [--agents claude,pi,omp,codex|all] [--hooks claude,pi,omp,codex|all|none]
        [--update] [--force] [--uninstall] [--status] [--dry-run]
"""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CONVERSATE_DIRNAME = ".conversate"
COPY_MARKER = ".conversate-installed-copy"

# Payload copied into <target>/.conversate/. Explicit allow-list (not copy-all-minus)
# so tests, .git, .arca, README, convs data, etc. are never picked up.
PAYLOAD_FILES = ("SKILL.md", "LICENSE", "scripts/conv_cli.py")
PAYLOAD_DIRS = ("references", "hooks")
IGNORE_DIR_NAMES = {"__pycache__", ".git", ".semble", "convs", ".arca"}
IGNORE_SUFFIXES = {".pyc", ".pyo"}

ALL_AGENTS = ("claude", "pi", "omp", "codex")

# Agents that resolve the skill via .agents/skills/ (all but Claude Code).
AGENTS_DIR_CONSUMERS = {"pi", "omp", "codex"}

CLAUDE_LINK = (".claude", "skills", "conversate")
AGENTS_LINK = (".agents", "skills", "conversate")

# Hook install destinations (relative to target).
PI_HOOK_DEST = (".pi", "extensions", "conv-turn-counter.ts")
OMP_HOOK_DEST = (".omp", "hooks", "pre", "conv-turn-counter.ts")
CODEX_HOOKS_JSON = (".codex", "hooks.json")
CLAUDE_SETTINGS = (".claude", "settings.json")

# Substrings that identify a hook entry as installed by conversate (for uninstall).
OUR_HOOK_MARKERS = ("conv_turn_counter", "conv-turn-counter", ".conversate")


class InstallError(Exception):
    """A refusal or hard error; main() prints it and exits non-zero."""


@dataclass
class Ctx:
    source: Path
    target: Path
    dry_run: bool = False
    force: bool = False
    update: bool = False

    @property
    def conv_dir(self) -> Path:
        return self.target / CONVERSATE_DIRNAME

    def disp(self, path: Path) -> str:
        """Path relative to target when possible, else absolute - for readable output."""
        path = Path(path)
        try:
            return str(path.relative_to(self.target))
        except ValueError:
            return str(path)


def emit(msg: str) -> None:
    print(msg)


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


def _copy_skill_payload(conv_dir: Path, link_path: Path) -> None:
    """Copy-fallback: a minimal, readable skill payload so discovery still works."""
    link_path.mkdir(parents=True, exist_ok=True)
    skill = conv_dir / "SKILL.md"
    if skill.is_file():
        shutil.copy2(skill, link_path / "SKILL.md")
    refs = conv_dir / "references"
    if refs.is_dir():
        shutil.copytree(refs, link_path / "references", dirs_exist_ok=True)
    (link_path / COPY_MARKER).write_text(
        "Installed by conversate scripts/install.py as a copy fallback (not a live link).\n"
        "Enable Windows Developer Mode or run as admin, then re-run install.py --update --force.\n",
        encoding="utf-8",
    )


def _create_link(link_path: Path, conv_dir: Path, ctx: Ctx) -> str:
    # 1) real symlink (POSIX always; Windows with Developer Mode / admin)
    try:
        if os.name == "nt":
            os.symlink(str(conv_dir), str(link_path), target_is_directory=True)
        else:
            rel = os.path.relpath(conv_dir, link_path.parent)
            os.symlink(rel, str(link_path), target_is_directory=True)
        emit(f"linked (symlink): {ctx.disp(link_path)} -> {CONVERSATE_DIRNAME}")
        return "symlink"
    except OSError:
        pass
    # 2) NTFS junction (Windows, no privilege required)
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(conv_dir)],
                capture_output=True, text=True,
            )
            if proc.returncode == 0 and link_kind(link_path) == "junction":
                emit(f"linked (junction): {ctx.disp(link_path)} -> {CONVERSATE_DIRNAME}")
                return "junction"
        except OSError:
            pass
    # 3) copy fallback (loud)
    _copy_skill_payload(conv_dir, link_path)
    emit(
        f"WARNING: could not create a live link at {ctx.disp(link_path)}; copied a minimal "
        f"skill payload (SKILL.md + references/) instead. This is a COPY, not a live link - "
        f"enable Windows Developer Mode (or run as admin) and re-run "
        f"'install.py --update --force' to replace it with a real link."
    )
    return "copy"


def make_link(link_path: Path, conv_dir: Path, ctx: Ctx) -> str:
    conv_dir = conv_dir.resolve()
    kind = link_kind(link_path)
    disp = ctx.disp(link_path)

    if kind in ("symlink", "junction") and resolves_to(link_path, conv_dir):
        emit(f"link ok ({kind}): {disp} -> {CONVERSATE_DIRNAME}")
        return kind
    if kind == "copy" and not ctx.force:
        emit(f"link present (copy, not a live link): {disp} - enable Developer Mode and re-run with --force for a real link")
        return "copy"
    if kind != "missing":
        # Occupied by something we must move aside to replace.
        if not ctx.force:
            raise InstallError(
                f"{disp} exists ({kind}) and does not resolve to {CONVERSATE_DIRNAME}; "
                f"use --force to replace it (the existing entry is renamed to <name>.bak-N)"
            )
        if ctx.dry_run:
            emit(f"would back up and replace existing {kind}: {disp}")
        else:
            backup = _next_bak(link_path)
            os.rename(link_path, backup)
            emit(f"backed up existing {kind}: {disp} -> {backup.name}")

    if ctx.dry_run:
        emit(f"would link {disp} -> {CONVERSATE_DIRNAME}")
        return "planned"
    link_path.parent.mkdir(parents=True, exist_ok=True)
    return _create_link(link_path, conv_dir, ctx)


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


# ------------------------------------------------------------------------- payload

def iter_payload(source: Path):
    for rel in PAYLOAD_FILES:
        p = source / rel
        if p.is_file():
            yield p, rel
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


def copy_payload(ctx: Ctx) -> None:
    conv_dir = ctx.conv_dir
    plans: list[tuple[Path, Path, str]] = []
    conflicts: list[str] = []
    for src, rel in iter_payload(ctx.source):
        dest = conv_dir / Path(rel)
        if not dest.exists():
            plans.append((src, dest, "create"))
        elif filecmp.cmp(src, dest, shallow=False):
            plans.append((src, dest, "skip"))
        elif ctx.update or ctx.force:
            plans.append((src, dest, "update"))
        else:
            conflicts.append(rel)

    if conflicts:
        raise InstallError(
            "payload would overwrite differing file(s); re-run with --update to refresh the "
            "skill (never touches convs/) or --force. Differing: " + ", ".join(sorted(conflicts))
        )

    created = updated = skipped = 0
    for src, dest, action in plans:
        if action == "skip":
            skipped += 1
            continue
        if ctx.dry_run:
            emit(f"would {action} {ctx.disp(dest)}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if action == "create":
            created += 1
        else:
            updated += 1
    verb = "would install" if ctx.dry_run else "payload"
    if ctx.dry_run:
        emit(f"{verb}: {sum(1 for _, _, a in plans if a != 'skip')} file(s) into {ctx.disp(conv_dir)} ({skipped} unchanged)")
    else:
        emit(f"payload: {created} created, {updated} updated, {skipped} unchanged in {ctx.disp(conv_dir)}")


# ------------------------------------------------------------------------ init store

def run_init(ctx: Ctx) -> None:
    cli = ctx.conv_dir / "scripts" / "conv_cli.py"
    if ctx.dry_run:
        emit(f"would run: python {ctx.disp(cli)} init --conv-root {ctx.disp(ctx.conv_dir)}")
        return
    if not cli.is_file():
        emit("WARN: conv_cli.py missing from payload; skipping store init")
        return
    proc = subprocess.run(
        [sys.executable, str(cli), "init", "--conv-root", str(ctx.conv_dir)],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        emit("store initialized")
    else:
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = first[-1] if first else f"exit {proc.returncode}"
        emit(f"WARN: 'init' exited {proc.returncode} (engine may be mid-refactor): {detail}; verifying store on disk")
    # Verify against the filesystem regardless of init's exit code (tolerant of a
    # partially-refactored engine that still creates the layout before failing).
    if (ctx.conv_dir / "convs").is_dir() and (ctx.conv_dir / "index.jsonl").exists():
        emit("store present: convs/, index.jsonl")
    else:
        emit("WARN: store artifacts missing after init (convs/ and/or index.jsonl) - inspect manually")


# ------------------------------------------------------------------------- link plan

def planned_links(agents: set[str], target: Path) -> list[Path]:
    links: list[Path] = []
    if "claude" in agents:
        links.append(target.joinpath(*CLAUDE_LINK))
    if agents & AGENTS_DIR_CONSUMERS:
        links.append(target.joinpath(*AGENTS_LINK))
    return links


# ----------------------------------------------------------------------------- hooks

def _hook_source(ctx: Ctx, *rel: str) -> Path | None:
    """Prefer the installed payload copy; fall back to the source checkout."""
    for base in (ctx.conv_dir, ctx.source):
        p = base.joinpath(*rel)
        if p.is_file():
            return p
    return None


def _copy_hook_file(src: Path | None, dest: Path, ctx: Ctx, label: str) -> None:
    if src is None:
        emit(f"{label}: adapter source not found; skipping")
        return
    if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
        emit(f"{label}: already installed at {ctx.disp(dest)}")
        return
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

    settings_path = ctx.target.joinpath(*CLAUDE_SETTINGS)
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = _load_json(settings_path)
        except Exception:
            emit(f"claude: existing {ctx.disp(settings_path)} is not valid JSON; skipping to avoid clobbering")
            return
    existing_hooks = settings.get("hooks")
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}
    added = _merge_hook_events(existing_hooks, incoming)
    if added == 0:
        emit(f"claude: hooks already present in {ctx.disp(settings_path)}; no change")
        return
    if ctx.dry_run:
        emit(f"claude: would merge {added} hook entr{'y' if added == 1 else 'ies'} into {ctx.disp(settings_path)}")
        return
    settings["hooks"] = existing_hooks
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backed_up = settings_path.is_file()
    if backed_up:
        shutil.copy2(settings_path, settings_path.with_name(settings_path.name + ".bak"))
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    suffix = " (.bak saved)" if backed_up else ""
    emit(f"claude: merged {added} hook entr{'y' if added == 1 else 'ies'} into {ctx.disp(settings_path)}{suffix}")


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

    dest = ctx.target.joinpath(*CODEX_HOOKS_JSON)
    data: dict = {}
    if dest.is_file():
        try:
            data = _load_json(dest)
        except Exception:
            emit(f"codex: existing {ctx.disp(dest)} is not valid JSON; skipping to avoid clobbering")
            return
    existing_hooks = data.get("hooks")
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}
    added = _merge_hook_events(existing_hooks, incoming)
    if added and not ctx.dry_run:
        data["hooks"] = existing_hooks
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            shutil.copy2(dest, dest.with_name(dest.name + ".bak"))
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        emit(f"codex: wrote {ctx.disp(dest)}")
    elif added and ctx.dry_run:
        emit(f"codex: would write {ctx.disp(dest)}")
    else:
        emit(f"codex: hook already present in {ctx.disp(dest)}; no change")
    emit("codex: NOTE set `hooks = true` under `[features]` in ~/.codex/config.toml to enable project hooks (this installer does not edit your global config)")


def wire_hooks(ctx: Ctx, hooks: set[str]) -> None:
    if "claude" in hooks:
        wire_claude_hook(ctx)
    if "pi" in hooks:
        _copy_hook_file(_hook_source(ctx, "hooks", "pi", "conv-turn-counter.ts"),
                        ctx.target.joinpath(*PI_HOOK_DEST), ctx, "pi")
    if "omp" in hooks:
        _copy_hook_file(_hook_source(ctx, "hooks", "pi", "conv-turn-counter.ts"),
                        ctx.target.joinpath(*OMP_HOOK_DEST), ctx, "omp")
    if "codex" in hooks:
        wire_codex_hook(ctx)


def print_hook_instructions(ctx: Ctx) -> None:
    emit("hooks: none wired (default). To enable auto-save reminders every 10 user turns, re-run with --hooks:")
    emit("  python scripts/install.py --hooks claude,pi,omp,codex   (or --hooks all)")
    emit("  codex additionally needs `hooks = true` under `[features]` in ~/.codex/config.toml")


# --------------------------------------------------------------------------- uninstall

def _command_text(entry: dict) -> str:
    return " ".join(str(entry.get(k, "")) for k in ("command", "commandWindows"))


def _is_our_hook(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    text = _command_text(entry)
    return any(marker in text for marker in OUR_HOOK_MARKERS)


def _strip_our_hooks(hooks_obj: dict) -> int:
    """Remove conversate-owned command entries from a nested hooks object. Returns count removed."""
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
    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        emit(f"{label}: no conversate hook entries in {ctx.disp(path)}")
        return
    removed = _strip_our_hooks(hooks_obj)
    if removed == 0:
        emit(f"{label}: no conversate hook entries in {ctx.disp(path)}")
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


def _remove_file(path: Path, ctx: Ctx, label: str) -> None:
    if not path.is_file():
        emit(f"{label}: {ctx.disp(path)} absent")
        return
    if ctx.dry_run:
        emit(f"{label}: would remove {ctx.disp(path)}")
        return
    path.unlink()
    emit(f"{label}: removed {ctx.disp(path)}")


def do_uninstall(ctx: Ctx) -> int:
    emit(f"uninstall from {ctx.target}")
    for link in (ctx.target.joinpath(*CLAUDE_LINK), ctx.target.joinpath(*AGENTS_LINK)):
        remove_link(link, ctx)
    _remove_file(ctx.target.joinpath(*PI_HOOK_DEST), ctx, "pi")
    _remove_file(ctx.target.joinpath(*OMP_HOOK_DEST), ctx, "omp")
    _unwire_json_hooks(ctx.target.joinpath(*CODEX_HOOKS_JSON), ctx, "codex", remove_empty_file=True)
    _unwire_json_hooks(ctx.target.joinpath(*CLAUDE_SETTINGS), ctx, "claude", remove_empty_file=False)
    emit(f"left {CONVERSATE_DIRNAME}/ and its convs/ untouched - conversation data is preserved")
    return 0


# ------------------------------------------------------------------------------ status

def _hook_wired_claude(ctx: Ctx) -> bool:
    path = ctx.target.joinpath(*CLAUDE_SETTINGS)
    if not path.is_file():
        return False
    try:
        data = _load_json(path)
    except Exception:
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
    path = ctx.target.joinpath(*CODEX_HOOKS_JSON)
    if not path.is_file():
        return False
    try:
        data = _load_json(path)
    except Exception:
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


def do_status(ctx: Ctx) -> int:
    conv_dir = ctx.conv_dir
    payload_present = (conv_dir / "SKILL.md").is_file() and (conv_dir / "scripts" / "conv_cli.py").is_file()
    store_present = (conv_dir / "convs").is_dir() and (conv_dir / "index.jsonl").exists()
    sentinel = (conv_dir / ".conv-root").exists()

    emit(f"target: {ctx.target}")
    emit(f"conversate dir: {ctx.disp(conv_dir)}")
    emit(f"payload: {'present' if payload_present else 'missing'}")
    emit(f"store: {'present' if store_present else 'missing'} (convs/, index.jsonl){' +.conv-root' if sentinel else ''}")

    for label, parts in (("claude .claude/skills/conversate", CLAUDE_LINK),
                         ("agents .agents/skills/conversate", AGENTS_LINK)):
        link = ctx.target.joinpath(*parts)
        kind = link_kind(link)
        if kind in ("symlink", "junction"):
            ok = "-> .conversate" if resolves_to(link, conv_dir) else "-> (other target!)"
            emit(f"link {label}: {kind} {ok}")
        elif kind == "copy":
            emit(f"link {label}: copy (not a live link)")
        elif kind == "missing":
            emit(f"link {label}: missing")
        else:
            emit(f"link {label}: foreign {kind} (not installer-created)")

    emit(f"hook claude: {'wired' if _hook_wired_claude(ctx) else 'not wired'}")
    emit(f"hook pi: {'wired' if ctx.target.joinpath(*PI_HOOK_DEST).is_file() else 'not wired'}")
    emit(f"hook omp: {'wired' if ctx.target.joinpath(*OMP_HOOK_DEST).is_file() else 'not wired'}")
    emit(f"hook codex: {'wired' if _hook_wired_codex(ctx) else 'not wired'}")
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
        description="Install the conversate skill into a project and link it into agent harnesses.",
    )
    p.add_argument("--target", help="project to install into (default: current directory)")
    p.add_argument("--source", help="conversate checkout to install from (default: this script's repo root)")
    p.add_argument("--agents", default="all", help="comma list of claude,pi,omp,codex or 'all' (default: all)")
    p.add_argument("--hooks", default="none", help="comma list of claude,pi,omp,codex, 'all', or 'none' (default: none)")
    p.add_argument("--update", action="store_true", help="refresh payload files (never touches convs/, index.jsonl, .semble/, .conv-root)")
    p.add_argument("--force", action="store_true", help="overwrite differing payload files and replace foreign links (backs up first)")
    p.add_argument("--uninstall", action="store_true", help="remove installer-created links and hooks; leaves .conversate/ data intact")
    p.add_argument("--status", action="store_true", help="report install state for the target and exit")
    p.add_argument("--dry-run", action="store_true", help="print planned actions; change nothing")
    return p.parse_args(argv)


def resolve_source(args) -> Path:
    if args.source:
        src = Path(args.source).expanduser().resolve()
    else:
        src = Path(__file__).resolve().parent.parent
    if not (src / "SKILL.md").is_file() or not (src / "scripts" / "conv_cli.py").is_file():
        raise InstallError(f"source does not look like a conversate checkout (missing SKILL.md or scripts/conv_cli.py): {src}")
    return src


def resolve_target(args) -> Path:
    return Path(args.target).expanduser().resolve() if args.target else Path.cwd().resolve()


def main(argv=None) -> int:
    try:
        args = parse_args(argv)
        source = resolve_source(args)
        target = resolve_target(args)

        if not args.target and target == source:
            raise InstallError(
                "refusing to install into the conversate checkout itself; pass --target DIR "
                "(installing here would nest a copy inside the source repo)"
            )

        ctx = Ctx(source=source, target=target, dry_run=args.dry_run, force=args.force, update=args.update)

        if args.status:
            return do_status(ctx)
        if args.uninstall:
            return do_uninstall(ctx)

        agents = _parse_set(args.agents, ALL_AGENTS, "agents", allow_none=False)
        hooks = _parse_set(args.hooks, ALL_AGENTS, "hooks", allow_none=True)

        emit(f"source: {source}")
        emit(f"target: {target}")
        if ctx.dry_run:
            emit("dry-run: no changes will be made")

        copy_payload(ctx)
        run_init(ctx)
        for link in planned_links(agents, target):
            make_link(link, ctx.conv_dir, ctx)
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
