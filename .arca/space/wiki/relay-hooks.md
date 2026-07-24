# Relay hook integration guidance

This page records explanatory Claude and hook workflow facts. It cannot override the sole Arca process/routing/lifecycle authority in [`../../index.md`](../../index.md) or delivered product authority in [`../../current/`](../../current/). The complete host matrix and installer behavior remain in [`hooks/README.md`](../../../hooks/README.md).

## Claude Code integration

Claude Code is wired by the installer, not by a native plugin auto-load. The source snippet [`settings-snippet.json`](../../../hooks/claude/settings-snippet.json) registers a `UserPromptSubmit` command invoking the installed binary as `relay hook --agent claude`; `__RELAY_BINARY__` is replaced for the host installation. The installer and repair behavior are implemented in [`scripts/install.py`](../../../scripts/install.py).

The installed Rust hook reads the host JSON from stdin, accepts the Claude `UserPromptSubmit` event, and keeps a session-keyed counter under `~/.relay/.semble/hook-state` in `relay-hook-<hash>.count` (with its matching lock file). Every tenth prompt emits the `RELAY HANDOFF` reminder, which Claude injects into context so the skill can perform a silent save. The production implementation is [`src/hook_runtime.rs`](../../../src/hook_runtime.rs); do not infer a different threshold, path, or event contract from this summary.

Hook files are installer-managed per host. The source tree and generated installed mirror are not independent hook surfaces; do not hand-edit the installed mirror. See [`hooks/README.md`](../../../hooks/README.md) for Claude, Codex, pi, and omp wiring, install commands, and repair details.

## Contributor workflow

- Route skill verbs through [`SKILL.md`](../../../SKILL.md) and the playbooks in [`references/`](../../../references/).
- Use `lsp` for symbol-aware navigation or refactoring of Rust sources.
- Never mutate the Relay archive manually; follow the CLI ownership guidance in [`relay-development.md`](relay-development.md) and the product authorities it links.
- Use the Arca process and precedence rules in [`../../index.md`](../../index.md), not this explanatory page.

## Source and knowledge links

- Hook overview and host wiring: [`hooks/README.md`](../../../hooks/README.md)
- Claude registration snippet: [`settings-snippet.json`](../../../hooks/claude/settings-snippet.json)
- Hook runtime source: [`hook_runtime.rs`](../../../src/hook_runtime.rs)
- Installer source: [`install.py`](../../../scripts/install.py)
- Arca routing and lifecycle: [`../../index.md`](../../index.md)
- Product hook behavior: [`../../current/`](../../current/)
