# Claude Code Guidance

This document provides project-specific guidance for Claude Code operating in the Relay repository.

Companion Schema: [.arca/schema/CLAUDE.md](.arca/schema/CLAUDE.md)

## Core Invariants
All core invariants regarding source-vs-runtime separation, CLI archive ownership, legacy compatibility, verification commands, and knowledge-base maintenance are defined in [AGENTS.md](AGENTS.md). Claude Code MUST adhere to those guidelines.

## Hook Integration
Claude Code integrates with Relay via the hook settings snippet:
- **Snippet source:** `hooks/claude/settings-snippet.json`
- **Hook Command:** Registers `~/.relay/bin/relay hook --agent claude`.
- **Auto-Save Mechanics:** The hook reads context from stdin, increments a turn counter stored at `~/.relay/.semble/hook-state/relay-hook-<hash>.count`, and emits a `RELAY HANDOFF` reminder every 10 user prompts.

## Interaction Workflow
- Rely on `SKILL.md` and playbooks under `references/` for routing skill verbs.
- Use `lsp` commands for symbol-aware code intelligence when navigating or refactoring Rust sources.
- Never mutate the Relay archive manually; always invoke the Rust CLI.
