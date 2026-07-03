# conversate hooks

Optional per-harness hooks that inject `CONV AUTO-SAVE` reminders so the `conversate` skill
checkpoints long sessions automatically. Hooks are an installer concern; the skill itself
works without them (in a harness with no hooks it self-triggers saves at milestones).

## claude/

- `conv-turn-counter.ps1` — a Claude Code **UserPromptSubmit** hook. It keeps a
  per-session prompt counter in `$env:TEMP` (keyed by `session_id`) and, once the count
  reaches 10 and every 10 after, prints `CONV AUTO-SAVE: threshold reached ...` on stdout.
  Claude Code injects UserPromptSubmit stdout into context, so the skill sees the reminder
  and runs a silent save.
- `settings-snippet.json` — the hook registration block to merge into
  `.claude/settings.json`. It points at `.conversate/hooks/claude/conv-turn-counter.ps1`
  (the canonical path in every harness, since skill dirs symlink into `.conversate`).

## pi

`hooks/pi/conv-turn-counter.ts` is a pi extension (default-export factory receiving
`ExtensionAPI`). It counts user prompts via `before_agent_start` and every 10th turn
injects a reminder to save conversation state. Install with:

    python scripts/install.py --target <project> --hooks pi

which copies it to `<project>/.pi/extensions/conv-turn-counter.ts` (pi auto-discovers
extensions there once the project is trusted).

## oh-my-pi (omp)

The same extension, installed to omp's hook location:

    python scripts/install.py --target <project> --hooks omp

copies it to `<project>/.omp/hooks/pre/conv-turn-counter.ts`.

## Codex

`hooks/codex/conv_turn_counter.py` is a stdlib `UserPromptSubmit` hook. Codex passes
the prompt payload as JSON on stdin; the script keeps a per-session counter in the OS
temp dir and, every 10th turn, prints the save reminder to stdout (Codex adds hook
stdout to the model as developer context). Wire it with:

    python scripts/install.py --target <project> --hooks codex

which writes `<project>/.codex/hooks.json` pointing at
`python .conversate/hooks/codex/conv_turn_counter.py` (with a `commandWindows`
variant). Codex only loads project hooks when `hooks = true` is set under `[features]`
in `~/.codex/config.toml`; the installer prints this reminder but does not edit your
global config.
