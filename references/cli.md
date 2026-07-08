# Conversate CLI

The shared helper is:

`python ~/.conversate/scripts/conv_cli.py <command>`

Plugin source is this repo. Installed plugin files live under the Plugin installation
root, `~/.conversate/` by default. Every harness runs the installed CLI from that Plugin
installation root.

## Runtime Layout

The default Plugin installation root is `~/.conversate/`. The Conversation database is
`~/.conversate/convs/` and is the source of truth:

```
~/.conversate/
├── .gitignore      # ignores .semble/, index.jsonl, __pycache__/ (records stay trackable)
├── conv/           # canonical installed Conversate plugin root (legacy runtime dir name)
├── convs/          # Conversation database: *.md records, source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl     # derived cache, one conversation record per line
├── .semble/        # semantic search cache directory
├── references/     # installed reference playbooks
├── hooks/          # canonical hook implementations
└── scripts/
    └── conv_cli.py # installed CLI
```

## Runtime Path Resolution

Every command resolves the Plugin installation root in this order:

1. `--conv-root PATH` (accepted before *or* after the subcommand) is an explicit
   compatibility override. It is non-default and should only be used when the user asks
   to operate on an older or alternate root.
2. Without that override, the Plugin installation root is `~/.conversate/`.

Normal operation does not use cwd marker search. Historical environment variables are
ignored by default resolution. `doctor` prints the resolved Plugin installation root, the
Conversation database, and whether an explicit compatibility override was used.

## Commands

- `init`: create the Plugin installation root, Conversation database, `.semble/`,
  `.gitignore`, and rebuild `index.jsonl`. With no override, targets `~/.conversate/`.
- `upsert --stdin` / `--json PATH` `[--status ...]`: create or replace a conversation from JSON.
- `rebuild-index`: rebuild `index.jsonl` from `~/.conversate/convs/*.md`.
- `regen-refs`: repair missing reverse refs, then rebuild the index.
- `list [--status active|parked|closed] [--json] [--limit N]`: index-only listing.
- `search "<query>" [--limit N]`: tiered filename/index/body search.
- `show <id-or-query> [--markdown]`: print one conversation.
- `set-status <id> active|parked|closed`: update status and timestamp.
- `sidekick <parent> <topic> [--id ID] [--keep-parent-active]`: create an active side
  branch with `spawned-from` refs. By default the parent is parked after the child is
  created.
- `continue <parent> [--topic TOPIC] [--id ID]`: create an active continuation record
  with `continued-from` refs, then park the parent.
- `return <branch> --digest TEXT [--parent ID]`: write the branch `## digest`, close the
  branch, repair refs, and rebuild the index.
- `doctor [--fix]`: report the resolved root + resolution layer, validate layout and
  optional tools, list parse errors, and WARN about records missing the resumption
  sections. With `--fix`, repair layout, `.gitignore`, refs, index, missing recovery
  sections, and canonical record rendering; malformed records remain report-only.

## Turn Counter Hook

`~/.conversate/hooks/claude/conv-turn-counter.ps1` is the Claude Code turn counter. It
reads the hook's stdin JSON, keeps a per-session counter file in `$env:TEMP` keyed by
`session_id`, and emits the auto-save reminder once the count reaches 10 (and every 10
after). It is registered as a `UserPromptSubmit` hook; see `hooks/README.md` and
`hooks/claude/settings-snippet.json`.

pi and oh-my-pi use `~/.conversate/hooks/pi/conv-turn-counter.ts`; Codex uses
`~/.conversate/hooks/codex/conv_turn_counter.py` through the real Codex config surface
at `~/.codex/hooks.json`. Harnesses without an installed hook still save at natural
milestones. Index rebuilds and ref regeneration remain persistent CLI responsibilities
regardless of the counter backend.

There is no timer for ref regeneration. `upsert` runs the eager write plus a byte-stable
regen sweep, and `regen-refs` is the manual full reconciliation command.

The semantic search layer runs `semble search -k <N> <query> ~/.conversate/convs --content
docs` when `semble` is installed. To allow transient `uvx semble`, set
`CONV_USE_UVX_SEMBLE=1`; it is opt-in because first-run indexing can be slow. If neither
path is available, the CLI falls back to local body scoring and labels those hits
`semble-body-fallback`.

## Conversation JSON for upsert

```json
{
  "id": "conv_260616_optional-slug",
  "topic": "required topic",
  "status": "active",
  "tags": ["optional"],
  "refs": [{"id": "conv_260615_parent", "rel": "spawned-from"}],
  "sections": {
    "summary": "required",
    "dict": "- **term** - meaning",
    "qa": "- **Q:** question? **A:** answer.",
    "sources": "optional", "insights": "optional", "decisions": "optional"
  },
  "resume": {
    "goal": "one-line goal",
    "next_steps": ["..."],
    "open_questions": ["..."],
    "suggested_skills": ["conversate:resume"]
  },
  "user_instructions": ["standing directive", "..."],
  "condensed_transcript": [
    {"u": "user turn", "a": "agent turn"},
    "or a plain string bullet"
  ]
}
```

- `summary`, `dict`, and `qa` are mandatory; upsert fails without them.
- `resume` (object), `user_instructions` (list or string), and `condensed_transcript`
  (list of `{u, a}` objects and/or strings) are structured JSON keys rendered into the
  always-present `## resume`, `## user-instructions`, and `## condensed-transcript`
  sections. When empty they render `(none)`.
- Section render order is fixed: mandatory sections `summary, dict, qa`, then optional
  informational sections `sources, insights, decisions, digest`, then always-present
  recovery sections `resume, user-instructions, condensed-transcript`, then any extra
  sections alphabetically. The same order is used for structured `sections` input and
  raw `body` input.
- If `id` is omitted, the CLI generates `conv_<YYMMDD>_<topic-slug>` and writes
  `~/.conversate/convs/<YYYY-MM-DD>_<topic-slug>.md`.
- A raw pre-rendered `body` may be passed instead of `sections`; the CLI still enforces
  the mandatory sections and renders any missing recovery sections as `(none)`.

## Branch primitives

The branch commands are deterministic wrappers around the same record write path as
`upsert`:

- `sidekick` creates an active child with `spawned-from`; `regen-refs` adds the parent's
  `spawned-to` reverse ref. After successful child creation, the parent is parked unless
  `--keep-parent-active` is set.
- `continue` creates an active child with `continued-from`, parks the parent after
  successful child creation, and carries forward the parent's dict, resume, qa, sources,
  insights, and decisions when present.
- `return` requires an explicit digest string, renders it as `## digest`, closes the
  branch, repairs bidirectional refs, and rebuilds `index.jsonl`.

Use `--id` on `sidekick` or `continue` when a caller needs a stable id for a scripted
flow or a test. The id must be unused; a collision fails without overwriting the existing
record or parking/linking the parent.
