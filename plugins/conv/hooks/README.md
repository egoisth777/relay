# conversate hooks

Optional per-harness hooks that inject `CONV AUTO-SAVE` reminders so the `conv` plugin
checkpoints long sessions automatically. Hooks are an installer concern; the plugin
works without them (in a harness with no hooks it self-triggers saves at milestones).

## claude/

- `conv-turn-counter.ps1` — a Claude Code **UserPromptSubmit** hook. It keeps a
  per-session prompt counter in the OS temp directory (keyed by `session_id`) and, once
  the count reaches 10 and every 10 after, prints a `CONV AUTO-SAVE` reminder to run
  `conv:save` through the conv plugin.
  Claude Code injects UserPromptSubmit stdout into context, so the skill sees the reminder
  and runs a silent save.
- `settings-snippet.json` — the hook registration block for the real Claude config
  surface, `~/.claude/settings.json`. The installer rewrites it to point at the
  selected Plugin installation root's canonical hook file,
  `hooks/claude/conv-turn-counter.ps1`.

## pi

`hooks/pi/conv-turn-counter.ts` is a pi extension (default-export factory receiving
`ExtensionAPI`). It counts user prompts via `before_agent_start` and every 10th turn
injects a reminder to run `conv:save` through the conv plugin. Install into a Plugin
installation root with:

    python scripts/install.py --target <plugin-root> --hooks pi

which installs the implementation from `<plugin-root>/hooks/pi/conv-turn-counter.ts`
into pi's current user-level extension entrypoint,
`~/.pi/agent/extensions/conv-turn-counter.ts`.

## oh-my-pi (omp)

The same extension, installed to omp's hook location:

    python scripts/install.py --target <plugin-root> --hooks omp

installs the implementation from `<plugin-root>/hooks/pi/conv-turn-counter.ts` into
omp's current hook entrypoint.

## Codex

`hooks/codex/conv_turn_counter.py` is a stdlib `UserPromptSubmit` hook. Codex passes
the prompt JSON on stdin; the script keeps a per-session counter in the OS
temp dir and, every 10th turn, prints the save reminder to stdout (Codex adds hook
stdout to the model as developer context). Wire it against the Plugin installation root
that owns the Conversation database:

    python scripts/install.py --target <plugin-root> --hooks codex

which writes `~/.codex/hooks.json` in the real Codex config surface, pointing at
`<plugin-root>/hooks/codex/conv_turn_counter.py` (with a `commandWindows` variant).
The source `hooks/codex/hooks.json` file is a template: its command fields contain
`__CONVERSATE_*__` placeholders, and the installer rewrites them to a verified Python 3
interpreter before writing the installed hook manifest.
Codex hooks are enabled by default. Set `hooks = false` under `[features]` in
`~/.codex/config.toml` only when you need to turn them off.
