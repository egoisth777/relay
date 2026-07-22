# Relay

**Relay preserves momentum across sessions.** It captures the runtime artifact of a
working session, distills it into a durable record, and gives the next session enough
context to continue without replaying the chat.

A Relay record stores the working model: agreed language, decisions, standing user
instructions, next steps, open questions, and a compressed exchange log. It is a
handoff, not a transcript.

## Runtime contract

The plugin source is this repository. The universal installation root (the Plugin
installation root) is `~/.relay/` by default; its Relay archive is
`~/.relay/convs/`. The canonical installed Relay plugin lives at
`~/.relay/relay/`, and each real agent config surface points back to that canonical
plugin and the canonical hook root, `~/.relay/hooks/`.

```text
~/.relay/
├── .gitignore      # ignores derived artifacts; records stay trackable
├── relay/          # canonical installed Relay plugin
│   ├── SKILL.md
│   ├── .claude-plugin/
│   ├── .codex-plugin/
│   └── skills/
├── convs/          # Relay archive: Markdown handoff records, source of truth
├── index.jsonl     # compatible derived export (rebuildable)
├── .semble/        # index-v2 generations, postings, journal, lock, hook state
├── references/     # installed Relay playbooks
├── hooks/          # canonical hook implementations
└── bin/
    └── relay       # installed Rust CLI
```

`convs/` and existing `conv_*` record IDs are retained for schema compatibility.
They are data-format details, not the Relay command namespace.

## Install

From this plugin source:

```bash
python scripts/install.py
```

Platform shims locate Python 3.10+ and forward all arguments:

```powershell
# Windows
.\scripts\install.ps1 --hooks all
```

```bash
# Linux, macOS, or WSL
./scripts/install.sh --hooks all
```

Set `RELAY_PYTHON` to pin an interpreter. `CONVERSATE_PYTHON` is accepted only as a
legacy compatibility fallback.

The installer creates `~/.relay/`, copies the Relay runtime, and exposes the Relay
skill group through the real host configuration surfaces:

| Runtime surface | Role |
|---|---|
| `~/.relay/relay/` | Canonical installed Relay plugin |
| `~/.relay/hooks/` | Canonical hook implementations |
| `~/.relay/bin/relay` | Shared runtime CLI |
| `~/.codex/skills/relay` | Codex entrypoint to the canonical plugin |
| `~/.claude/skills/relay` | Claude Code entrypoint to the canonical plugin |
| `~/.codex/hooks.json` | Optional Codex handoff reminder |
| `~/.claude/settings.json` | Optional Claude handoff reminder |
| `~/.pi/agent/extensions/relay-turn-counter.ts` | Optional pi handoff reminder |

Useful installer options:

```text
python scripts/install.py [--target DIR] [--source DIR]
  [--agents claude,pi,omp,codex|all] [--hooks claude,pi,omp,codex|all|none]
  [--update] [--repair|--doctor-fix] [--force] [--uninstall] [--status] [--dry-run]
```

- `--target` sets the Relay installation root; the default is `~/.relay/`. It refuses
  `~/.conversate/` (and its children) so legacy data cannot become an install target.
- `--update` refreshes Relay-owned runtime files and preserves the Relay archive.
- `--repair` restores Relay-owned runtime files and selected hooks.
- `--uninstall` removes Relay-owned plugin and hook wiring only; it never removes
  `convs/` records.
- `--dry-run` reports changes without modifying files.

Native Claude marketplace installation can load the skills, but `install.py` owns the
runtime CLI, archive, and optional per-host hooks.

## Legacy Conversate data

Relay does **not** modify, delete, or automatically merge `~/.conversate/`. Import
legacy records deliberately after installing Relay:

```bash
~/.relay/bin/relay import --from ~/.conversate
```

The import copies only missing Markdown records into `~/.relay/convs/`, leaves the
legacy source byte-for-byte untouched, rebuilds Relay's index, and reports collisions
without overwriting a Relay record. Use `--relay-root PATH` to operate on another
Relay installation root; `--conv-root PATH` remains a legacy compatibility alias and
is not advertised in normal flows.

## Skills and handoffs

Relay exposes the base `relay` skill plus these verbs:

```text
/relay:save       capture and distill the current runtime artifact
/relay:resume     reconstruct a selected handoff in a fresh session
/relay:list       show active, parked, and recent handoffs
/relay:park       checkpoint a handoff without continuing it
/relay:sidekick   branch focused exploration from a parent handoff
/relay:return     return a branch digest to its parent
/relay:continue   create a fresh linked continuation
/relay:regen      repair derived refs and indexes
```

Auto-save hooks emit `RELAY HANDOFF` reminders every ten prompts. When a reminder is
injected, run the `/relay:save` flow silently and report only the saved record ID.

## CLI quickstart

```bash
# initialize the Relay installation root and archive
~/.relay/bin/relay init

# save a distilled handoff
~/.relay/bin/relay upsert --stdin < handoff.json

# find and resume a handoff
~/.relay/bin/relay search "auth redesign"
~/.relay/bin/relay context <id> --budget-tokens 8000

# list, repair, and diagnose
~/.relay/bin/relay list --limit 10
~/.relay/bin/relay regen-refs
~/.relay/bin/relay doctor
```

## Record format

Records are Markdown with thin TOML frontmatter (`id`, `topic`, `status`, `tags`,
`refs`, `created`, `updated`) followed by ordered sections:

- `## summary` — one-line orientation
- `## glossary` — agreed or coined language
- `## qa` — the question-and-answer spine; `Q (open)` marks live threads
- `## resume` — goal, completed checkpoints, next steps, open questions, suggested skills
- `## user-instructions` — standing directives for the next session
- `## environment` — reference-only execution environment
- `## artifacts` — reference-only touched files, commits, PRs, and their state
- `## condensed-transcript` — compressed chronology with durable weights 1–3
- `## sources`, `## insights`, `## decisions`, `## digest` — optional evidence

`summary`, `glossary`, and `qa` are required. The three handoff sections always exist;
empty sections render as `(none)`.

## Performance and recovery

Relay recursively snapshots the Relay archive once per archive-consuming command and
parses changed records across a bounded worker pool. Set `RELAY_SCAN_THREADS=1..64` to
override the default (available parallelism, capped at eight). The fingerprinted cache
under `.semble/index-v2/` stores generation-named record rows and random-access search
postings; `index.jsonl` remains the stable, greppable compatibility export. Both are
derived and self-heal from Markdown records. `RELAY_NO_CACHE=1` bypasses index-v2 for a
single invocation, while `rebuild-index --full` deliberately reparses every record.

Mutations publish a durable `.semble/txn.pending` journal before record after-images,
then commit cache generations manifest-last. The next archive command rolls an
interrupted journal forward before reading. Do not delete this journal manually.

`relay context` emits frontmatter, language, standing instructions, checkpoints,
questions, decisions, environment/artifacts references, weight-trimmed transcript,
one-hop linked digests, and a structured activation argv. `--budget-tokens` applies an
exact UTF-8 byte cap; `--json` exposes the versioned pack and `--no-refs` suppresses
linked context.

## Requirements

- Python 3.10+ for the installer; no third-party Python dependencies.
- Rust/Cargo when building a runtime binary from source.
- Optional search tools: `rg`, `fff`, and `semble` (or `uvx semble`).

Do not place an `AGENTS.md` inside the Relay installation root: pi may treat it as a
skill instead of project instruction.
