# Relay hooks

Optional per-harness hooks that inject `RELAY HANDOFF` reminders so the Relay plugin
checkpoints long sessions automatically. Hooks are an installer concern; the plugin
works without them (in a harness with no hooks it self-triggers saves at milestones).

## Installer-managed, not plugin-native

These hooks are **installer-managed and per-host**. There is deliberately no
`hooks/hooks.json` that Claude Code would auto-load via `${CLAUDE_PLUGIN_ROOT}`
at the plugin root, so adding Relay as a native marketplace plugin registers
the **skills only** — it does not activate auto-save. `scripts/install.py` is
what wires each host's hook script into that host's own config surface:

| Host | Hook source | Wired into |
|------|-------------|------------|
| Claude Code | `claude/settings-snippet.json` → `bin/relay hook --agent claude` | `~/.claude/settings.json` |
| Codex | `codex/hooks.json` → `bin/relay hook --agent codex` | `~/.codex/hooks.json` |
| pi / omp | `pi/relay-turn-counter.ts` | `~/.pi/agent/extensions/`, `<target>/.omp/hooks/pre/` |

The installer also creates the installed Rust CLI (`<install-root>/bin/relay`)
and the Relay archive the hooks depend on, which is why a native-plugin install
alone is not enough to enable auto-save. This per-host layout is a deliberate
deviation from the superpowers plugin convention.

## Internal repair mirror

`scripts/install.py` also plants a copy of this hook tree at
`<install-root>/relay/hooks/`, alongside the canonical `<install-root>/hooks/`.
It is an internal, generated mirror — not a second hook surface and not something
any host is wired to — kept as the pristine source so `install.py --doctor-fix`
can self-heal a corrupted or stale canonical `<install-root>/hooks/` when
repairing an installed tree in place (without the original checkout). Both trees
are generated from this single source `hooks/` directory; do not hand-edit the
installed mirror.

## Claude Code

`settings-snippet.json` registers the installed Rust binary as a `UserPromptSubmit`
hook: `<install-root>/bin/relay hook --agent claude`. The binary reads the host JSON
from stdin, keeps a session-keyed counter in the private per-user directory
`~/.relay/.semble/hook-state` (this location prevents cross-user temp-file
interference), and prints the `RELAY HANDOFF` reminder on every tenth
prompt. Claude Code injects that stdout into context, so the skill performs a
silent save.

## pi

`hooks/pi/relay-turn-counter.ts` is a pi extension (default-export factory receiving
`ExtensionAPI`). It counts user prompts via `before_agent_start` and every 10th turn
injects a reminder to run `/relay:save` through the Relay plugin. Install into a Plugin
installation root with:

    python scripts/install.py --target <plugin-root> --hooks pi

which installs the implementation from `<plugin-root>/hooks/pi/relay-turn-counter.ts`
into pi's current user-level extension entrypoint,
`~/.pi/agent/extensions/relay-turn-counter.ts`.

## oh-my-pi (omp)

The same extension, installed to omp's hook location:

    python scripts/install.py --target <plugin-root> --hooks omp

installs the implementation from `<plugin-root>/hooks/pi/relay-turn-counter.ts` into
omp's current hook entrypoint.

## Codex

Codex also invokes the installed Rust binary directly:
`<install-root>/bin/relay hook --agent codex`. The command reads prompt JSON from stdin,
keeps the per-session counter in the private per-user directory
`~/.relay/.semble/hook-state` (preventing cross-user temp-file interference),
and emits the reminder every tenth turn. Wire it against the Plugin installation
root that owns the Relay archive:

    python scripts/install.py --target <plugin-root> --hooks codex

The installer writes `~/.codex/hooks.json` with platform-correct `command` and
`commandWindows` forms pointing to `<plugin-root>/bin/relay`. The source
`hooks/codex/hooks.json` remains an installer template; it is never itself executed.
Codex hooks are enabled by default. Set `hooks = false` under `[features]` in
`~/.codex/config.toml` only when you need to turn them off.
