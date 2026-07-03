#!/usr/bin/env python3
"""conversate auto-save turn counter (Codex CLI UserPromptSubmit hook).

Codex invokes this on every user prompt, passing the hook payload as JSON on
stdin (fields include session_id, transcript_path, cwd, hook_event_name, model,
permission_mode, turn_id, prompt). We keep a per-session counter in the OS temp
dir and, on every Nth prompt, print a save reminder to stdout. Codex adds a
hook's plain-text stdout to the model as extra developer context.

Contract notes (verified against developers.openai.com/codex/hooks):
  - stdin is a JSON object; plain text on stdout becomes developer context.
  - A hook must never break the harness, so this always exits 0, prints nothing
    on non-threshold turns, and swallows every error.

Stdlib only. Wired via <project>/.codex/hooks.json (requires features.hooks =
true in ~/.codex/config.toml).
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
    "CONV AUTO-SAVE: threshold reached — save conversation state via the "
    "conversate skill (see .conversate/references/save.md), then continue."
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


def main() -> int:
    try:
        data = sys.stdin.read()
        payload = json.loads(data) if data.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    try:
        # Only count in projects that use conversate (have a .conversate store) —
        # when wired user-level this fires everywhere, so no-op fast and silently.
        cwd = str(payload.get("cwd") or "") or os.getcwd()
        if not (Path(cwd) / ".conversate").is_dir():
            return 0
        count = _bump(_counter_path(_session_key(payload)))
        if count % TURN_THRESHOLD == 0:
            # Write UTF-8 bytes directly so the em dash survives regardless of the
            # process locale (a cp1252 text layer would otherwise raise on encode).
            sys.stdout.buffer.write((REMINDER + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    except Exception:
        pass  # a hook must never break the harness

    return 0


if __name__ == "__main__":
    sys.exit(main())
