# Relay CLI

The shared helper is:

`~/.relay/bin/relay <command>`

Plugin source is this repo. Installed plugin files live under the Plugin installation
root, `~/.relay/` by default. Every harness runs the installed CLI from that Plugin
installation root.

## Runtime Layout

The default Plugin installation root is `~/.relay/`. The Relay archive is
`~/.relay/convs/` and is the source of truth:

```
~/.relay/
├── .gitignore      # ignores .semble/, index.jsonl, __pycache__/ (records stay trackable)
├── relay/     # canonical installed Relay plugin root
├── convs/          # Relay archive: *.md records, source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl     # compatible derived export, one record per line
├── .semble/        # index-v2 generations, postings, write lock, transaction journal
├── references/     # installed reference playbooks
├── hooks/          # canonical hook implementations
└── bin/
    └── relay # installed Rust CLI
```

## Runtime Path Resolution

Every command resolves the Plugin installation root in this order:

1. `--relay-root PATH` (accepted before *or* after the subcommand) is an explicit
   override for an alternate Relay installation.
2. Without that override, the Plugin installation root is `~/.relay/`.

`--conv-root PATH` remains a legacy compatibility alias. It is not part of normal
Relay flows and exists only to inspect or recover an older archive.

Normal operation does not use cwd marker search. Historical environment variables are
ignored by default resolution. `doctor` prints the resolved Plugin installation root, the
Relay archive, and whether an explicit compatibility override was used.

## Commands

- `init`: create the Plugin installation root, Relay archive, `.semble/`,
  `.gitignore`, and rebuild `index.jsonl`. With no override, targets `~/.relay/`.
- `upsert --stdin` / `--json PATH` `[--status ...]`: create or replace a distilled Relay record from JSON.
- `rebuild-index [--full]`: freshen index-v2 and `index.jsonl`; `--full` reparses every record.
- `regen-refs`: repair missing reverse refs, then rebuild the index.
- `list [--status active|parked|closed] [--json] [--limit N]`: list Relay records. After a shared-lock-safe recovery, this snapshots the Relay archive, reuses metadata-matching index-v2 rows, reparses changed/new records, and repairs derived cache state (`index.jsonl` remains a compatibility export).
- `search "<query>" [--limit N] [--no-semble]`: exact-id, random-access posting,
  optional Semble, then parallel body search.
- `show <id-or-query> [--markdown]`: print one Relay record.
- `context <id-or-query> [--budget-tokens N] [--json] [--no-refs]`: emit a
  reconstruction-ordered, budget-aware pack with one-hop linked digests.
- `set-status <id> active|parked|closed`: update status and timestamp.
- `sidekick <parent> <topic> [--id ID] [--keep-parent-active]`: create an active side
  branch with `spawned-from` refs. By default the parent is parked after the child is
  created.
- `continue <parent> [--topic TOPIC] [--id ID]`: create an active continuation record
  with `continued-from` refs, then park the parent.
- `return <branch> --digest TEXT [--parent ID]`: write the branch `## digest`, close the
  branch, repair refs, and rebuild the index.
- `import --from PATH`: copy missing Markdown records from an explicitly selected
  legacy archive. The source is never modified; same-name conflicts are reported in
  `collisions` and are never overwritten.
- `doctor [--fix]`: report the resolved root + resolution layer, validate layout and
  optional tools, list parse errors, and WARN about records missing the resumption
  sections. With `--fix`, repair layout, `.gitignore`, refs, index, missing recovery
  sections, and canonical record rendering; malformed records remain report-only.
  Stored records with differing `## dict` + `## glossary` content produce a per-record
  doctor warning; `doctor --fix` refuses to rewrite that record. Context, branch, and
  return fail with `conflicting sections: dict and glossary`.

## Context Pack

`context` text begins with the `relay context pack v2` banner. Its fixed sections are
`summary, glossary, user-instructions, resume, qa`; optional sections are
`decisions, environment, artifacts, sources, insights` when present and nonempty. These
are followed by transcript, linked-context, action argv, and the truncated flag. JSON
output uses `schema_version: 2` and lists the delivered sections.

## Turn Counter Hook

Claude Code and Codex invoke the installed Rust binary as their turn counter:
`~/.relay/bin/relay hook --agent claude` and
`~/.relay/bin/relay hook --agent codex`. Each reads host JSON from stdin,
maintains a per-session counter in the private per-user directory
`~/.relay/.semble/hook-state` (preventing cross-user temp-file interference),
and emits the Relay handoff reminder every tenth prompt. Their installer-managed config
entries live in `~/.claude/settings.json` and `~/.codex/hooks.json`; see `hooks/README.md`.

pi and oh-my-pi retain `~/.relay/hooks/pi/relay-turn-counter.ts` because their extension
API loads TypeScript directly. Harnesses without an installed hook still save at natural
milestones. Index rebuilds and ref regeneration remain persistent CLI responsibilities
regardless of the counter backend.

There is no timer for ref regeneration. Mutations update only the changed record and
affected forward-ref neighbors in one journaled transaction; `regen-refs` is the
manual full reconciliation command.

The semantic search layer runs `semble search -k <N> <query> ~/.relay/convs --content
docs` when `semble` is installed. To allow transient `uvx semble`, set
`RELAY_USE_UVX_SEMBLE=1`; it is opt-in because first-run indexing can be slow. If neither
path is available, the CLI falls back to local body scoring and labels those hits
`semble-body-fallback`.

## Relay record JSON for upsert

```json
{
  "id": "conv_260616_optional-slug",
  "topic": "required topic",
  "status": "active",
  "tags": ["optional"],
  "refs": [{"id": "conv_260615_parent", "rel": "spawned-from"}],
  "sections": {
    "summary": "required",
    "glossary": "- **term** - meaning",
    "qa": "- **Q:** question? **A:** answer.",
    "sources": "optional", "insights": "optional", "decisions": "optional"
  },
  "resume": {
    "goal": "one-line goal",
    "checkpoints": ["completed milestone"],
    "next_steps": ["..."],
    "open_questions": ["..."],
    "suggested_skills": ["relay:resume"]
  },
  "user_instructions": ["standing directive", "..."],
  "environment": ["platform: Windows", "branch: relay-perf"],
  "artifacts": ["src/main.rs — cache transaction implemented"],
  "condensed_transcript": [
    {"u": "user turn", "a": "agent turn", "w": 3},
    "or a plain string bullet"
  ]
}
```

- `summary`, `glossary`, and `qa` are mandatory; upsert fails without them.
  The `dict` key or heading is a deprecated input alias for `glossary`, accepted forever
  and never emitted. If both `dict` and `glossary` are present, identical trimmed content
  coalesces silently; differing content fails validation with an error naming both as
  conflicting.
- `resume` (object), `user_instructions` (list or string), and `condensed_transcript`
  (list of `{u, a, w}` objects and/or strings) are structured JSON keys rendered into the
  always-present `## resume`, `## user-instructions`, and `## condensed-transcript`
  sections. When empty they render `(none)`.
- Section render order is fixed: mandatory sections `summary, glossary, qa`, then optional
  informational sections `sources, insights, decisions, environment, artifacts, digest`, then always-present
  recovery sections `resume, user-instructions, condensed-transcript`, then any extra
  sections alphabetically. The same order is used for structured `sections` input and
  raw `body` input.
- If `id` is omitted, the CLI generates `conv_<YYMMDD>_<topic-slug>` and writes
  `~/.relay/convs/<YYYY-MM-DD>_<topic-slug>.md`.
- A raw pre-rendered `body` may be passed instead of `sections`; the CLI still enforces
  the mandatory sections and renders any missing recovery sections as `(none)`.

## Branch primitives

The branch commands are deterministic wrappers around the same record write path as
`upsert`:

- `sidekick` creates an active child with `spawned-from` and adds the parent's
  `spawned-to` reverse ref in the same transaction. The parent is parked unless
  `--keep-parent-active` is set.
- `continue` creates an active child with `continued-from`, parks the parent after
  successful child creation, and carries forward the parent's glossary, resume, qa, sources,
  insights, decisions, environment, checkpoints, instructions, and weighted transcript
  markers when present. Artifacts are deliberately branch-local and are not inherited.
- `return` requires an explicit digest string, renders it as `## digest`, closes the
  branch, repairs bidirectional refs, and rebuilds `index.jsonl`.

Use `--id` on `sidekick` or `continue` when a caller needs a stable id for a scripted
flow or a test. The id must be unused; a collision fails without overwriting the existing
record or parking/linking the parent.
