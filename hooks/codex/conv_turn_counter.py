#!/usr/bin/env python3
"""Conversate auto-save turn counter (Codex CLI UserPromptSubmit hook).

Codex invokes this on every user prompt, passing the hook input as JSON on
stdin (fields include session_id, transcript_path, cwd, hook_event_name, model,
permission_mode, turn_id, prompt). We keep a per-session counter in the OS temp
dir and, on every Nth prompt, print a save reminder to stdout. Codex adds a
hook's plain-text stdout to the model as extra developer context.

Contract notes (verified against developers.openai.com/codex/hooks):
  - stdin is a JSON object; plain text on stdout becomes developer context.
  - A hook must never break the harness, so this always exits 0, prints nothing
    on non-threshold turns, and swallows every error.

Stdlib only. Installed from the Plugin installation root via real
~/.codex/hooks.json, which points at <plugin-root>/hooks/codex/conv_turn_counter.py
(Codex hooks are enabled by default; set features.hooks = false only to disable them).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

# Inject the reminder on this cadence: turn 10, 20, 30, ...
TURN_THRESHOLD = 10

REMINDER = (
    "CONVERSATE AUTO-SAVE: threshold reached - run /conversate:save via the "
    "Conversate plugin, then continue."
)

_COUNTER_PREFIX = "conversate-codex-turns-"


def _session_key(payload: dict) -> str:
    """Stable per-session key. Prefer session_id; fall back to a hash of cwd so
    a missing id degrades to per-project counting rather than crashing."""
    raw = str(payload.get("session_id") or "").strip()
    if not raw:
        raw = str(payload.get("cwd") or os.getcwd())
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _counter_path(key: str) -> Path:
    return Path(tempfile.gettempdir()) / (_COUNTER_PREFIX + key)


def _conversation_database_present() -> bool:
    try:
        plugin_root = Path(__file__).resolve().parents[2]
    except (IndexError, OSError):
        return False
    return (plugin_root / "convs").is_dir()


def _bump(path: Path) -> int:
    try:
        count = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        count = 0
    count += 1
    try:
        path.write_text(str(count), encoding="utf-8")
    except OSError:
        pass  # counting is best-effort; never fail the turn
    return count


def _read_user_prompt_payload() -> dict | None:
    try:
        data = sys.stdin.read()
        if not data.strip():
            return None
        payload = json.loads(data)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None
    return payload


def main() -> int:
    try:
        payload = _read_user_prompt_payload()
        if payload is None:
            return 0
        if not _conversation_database_present():
            return 0
        count = _bump(_counter_path(_session_key(payload)))
        if count % TURN_THRESHOLD == 0:
            sys.stdout.buffer.write((REMINDER + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    except Exception:
        pass  # a hook must never break the harness

    return 0


if __name__ == "__main__":
    sys.exit(main())
