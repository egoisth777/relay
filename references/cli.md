# conv CLI

The shared helper is:

`python .conversate/scripts/conv_cli.py <command>`

Every harness runs it at this same path: `.claude/skills/conversate`,
`.agents/skills/conversate`, etc. are symlinks into `.conversate`, so the canonical
invocation is identical everywhere.

## Store layout

The conv root **is** the `.conversate/` directory:

```
.conversate/
├── .conv-root      # sentinel written by init; makes the store rediscoverable
├── .gitignore      # ignores .semble/, index.jsonl, __pycache__/ (records stay tracked)
├── convs/          # *.md conversation records — source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl     # derived cache, one conversation record per line
└── .semble/        # semantic search cache directory
```

## Store-root resolution

Every command resolves the store root in this order:

1. `--conv-root PATH` (accepted before *or* after the subcommand) — layer `flag`.
2. `$CONVERSATE_ROOT` — layer `env-conversate`.
3. `$BRAIN_CONV` (legacy) — layer `env-brain`.
4. **Marker search** — nearest ancestor of the cwd, then of the script dir. Within each
   dir: a dir *named* `.conversate` is the root; a `.conv-root` sentinel makes that dir
   the root; a `.conversate/` subdir means `<dir>/.conversate`; a legacy `conv/` subdir
   means `<dir>/conv`. The walk stops at a `.git` boundary but still checks the repo-root
   dir itself (so `.conversate` next to `.git` resolves). Layer `marker`.
5. Otherwise read commands exit 2 with a clear error — never a path guess. `init` is the
   sole exception: with nothing to resolve it creates `<cwd>/.conversate/`.

`init` writes a `.conv-root` sentinel and a `.gitignore` into the root, so a bootstrapped
store is found automatically by later commands run from anywhere beneath it. `doctor`
prints the resolved root plus `resolution: {"layer": flag|env-conversate|env-brain|marker|none, "marker": <path>|null}`.

## Commands

- `init`: create `.conversate/`, `convs/`, `.semble/`, write the `.conv-root` sentinel and
  `.gitignore`, and rebuild `index.jsonl`. With no override, targets `<cwd>/.conversate/`.
- `upsert --stdin` / `--json PATH` `[--status ...]`: create or replace a conversation from JSON.
- `rebuild-index`: rebuild `index.jsonl` from `.conversate/convs/*.md`.
- `regen-refs`: repair missing reverse refs, then rebuild the index.
- `list [--status active|parked|closed] [--json] [--limit N]`: index-only listing.
- `search "<query>" [--limit N]`: tiered filename/index/body search.
- `show <id-or-query> [--markdown]`: print one conversation.
- `set-status <id> active|parked|closed`: update status and timestamp.
- `doctor`: report the resolved root + resolution layer, validate layout and optional
  tools, list parse errors, and WARN about records missing the resumption sections.

## Turn Counter Hook

`.conversate/hooks/claude/conv-turn-counter.ps1` is the Claude Code turn counter. It reads
the hook's stdin JSON, keeps a per-session counter file in `$env:TEMP` keyed by
`session_id`, and emits the auto-save reminder once the count reaches 10 (and every 10
after). It is registered as a `UserPromptSubmit` hook; see `hooks/README.md` and
`hooks/claude/settings-snippet.json`. Other harnesses self-trigger the save instead of
relying on hooks. Index rebuilds and ref regeneration remain persistent CLI
responsibilities regardless of the counter backend.

There is no timer for ref regeneration. `upsert` runs the eager write plus a byte-stable
regen sweep, and `regen-refs` is the manual full reconciliation command.

The semantic search layer runs `semble search -k <N> <query> .conversate/convs --content
docs` when `semble` is installed. To allow transient `uvx semble`, set
`CONV_USE_UVX_SEMBLE=1`; it is opt-in because first-run indexing can be slow. If neither
path is available, the CLI falls back to local body scoring and labels those hits
`semble-body-fallback`.

## JSON shape for upsert

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
    "suggested_skills": ["conv:resume"]
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
  (list of `{u, a}` objects and/or strings) are structured keys rendered into the
  always-present `## resume`, `## user-instructions`, and `## condensed-transcript`
  sections. When empty they render `(none)`.
- Section render order is fixed: `summary, dict, qa, resume, user-instructions,
  condensed-transcript, sources, insights, decisions, digest`, then any extra sections
  alphabetically.
- If `id` is omitted, the CLI generates `conv_<YYMMDD>_<topic-slug>` and writes
  `.conversate/convs/<YYYY-MM-DD>_<topic-slug>.md`.
- A raw pre-rendered `body` may be passed instead of `sections`; the CLI still enforces
  the mandatory sections and appends any missing resumption sections as `(none)`.
