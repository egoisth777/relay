# Agent Guidance

This document provides project-specific guidance for all coding agents operating in the Relay repository.

Companion Schema: [.arca/schema/AGENTS.md](.arca/schema/AGENTS.md)

## Core Invariants

### 1. Source-vs-Runtime Separation
- **Plugin source:** This repository contains the source code for the Rust CLI (`src/`), the Python installer (`scripts/install.py`), and the reference playbooks/skills. It is never used as a runtime root. The root `AGENTS.md` is source-only and MUST NOT be placed inside `~/.relay/` (where pi may load it as a skill).
- **Plugin installation root:** By default, all runtime operations target `~/.relay/` (overridable with `--relay-root PATH`).
- Agents MUST NOT use the repository root or cwd-based discovery for runtime operations or storage.

### 2. Archive Mutation & CLI Ownership
- The compiled Rust CLI binary (`~/.relay/bin/relay` or `relay.exe`) owns all mutations of the Relay archive (`~/.relay/convs/*.md`) and the derived cache (`~/.relay/index.jsonl`).
- Agents MUST NEVER write, edit, or delete handoff records or the index file directly. All changes must go through the CLI.

### 3. Legacy Compatibility & Protection
- The legacy Conversate directory (`~/.conversate/`) is a read-only compatibility surface.
- Agents MUST NOT write to or mutate `~/.conversate/`.
- Carrying legacy records forward is done exclusively via `relay import --from ~/.conversate`.

### 4. Terminology
All agents must use the following ubiquitous terms:
- **Relay:** The project and skill name.
- **Plugin installation root:** The `~/.relay/` runtime directory.
- **Relay archive:** The `convs/` directory inside the installation root.
- **Handoff record:** A Markdown file in the Relay archive with TOML frontmatter and required sections.
- **RELAY HANDOFF:** The reminder text emitted by the hook runtime to trigger a save.

For specific Claude Code instructions, see [CLAUDE.md](CLAUDE.md).

## Verification Commands
To verify the implementation:
- **Rust compilation & tests:** `cargo test`
- **Python integration tests:** `python -m pytest` (e.g., `python -m pytest tests/test_install.py`)

## Knowledge-Base Maintenance
The repository documentation of architectural facts is stored in:
- `.arca/space/relay-sp/what/architecture.md`
- `.arca/space/relay-sp/what/manifest.md`
- `.arca/space/relay-sp/what/flows.md`
- `.arca/index/ubiquitous-language.md`

Any code changes must reconcile drift by updating these documents accordingly.
