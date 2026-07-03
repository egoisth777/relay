# conversate — Architecture

> This folder (`.arca/space/conversate-sp/what/`) is the natural-language source of
> truth for the **conversate** skill. The files here *reflect and define* the whole
> system. If code and these docs disagree, treat it as a drift to reconcile.

## What conversate is

conversate is an **agent-agnostic conversation memory manager** packaged as a skill
(skill name: `conversate`) that multiple harnesses read (claude, pi, omp, codex). It lets an
agent persist, retrieve, list, park, branch, return, and continue *topic-bound
conversations* so that headspace survives across sessions, context windows, and agents.

The core idea: a conversation is not a transcript. It is a distilled, reconstructable
artifact — a topic plus a dictionary of agreed language, a Q&A spine, a resumption plan,
standing user instructions, and a condensed exchange log — written so a *cold* agent (any
agent) can recover the mental model without replaying the chat.

## Design pillars

1. **Markdown is the source of truth.** Each conversation is one Markdown file under
   `.conversate/convs/*.md` with **TOML frontmatter** delimited by `+++`. The file is the
   durable, human-editable record.
2. **The index is a derived cache.** `.conversate/index.jsonl` is rebuilt from the record
   files on every write. It is never authoritative; it can be deleted and regenerated.
3. **The CLI owns all writes.** A single Python helper (`scripts/conv_cli.py`)
   performs every mutation: creating files, normalizing frontmatter, reconciling
   bidirectional refs, and rebuilding the index. The skill prose never hand-edits the
   store; it shells out to the CLI.
4. **The skill (LLM) owns extraction + judgment.** Deciding *what* to save (topic,
   tags, the high-value `dict`, open questions) and *which* conversation to resume is
   the agent's job, guided by `SKILL.md` + the `references/*.md` playbooks.
5. **Every record is a resumption point.** Every conversation has a `topic` and the
   mandatory body sections `## summary`, `## dict`, `## qa`, plus the always-present
   resumption sections `## resume`, `## user-instructions`, `## condensed-transcript`
   (empty ones render `(none)`). `dict` (the ubiquitous language) is the highest-value
   section and is reconstructed first.
6. **Tiered search, graceful degradation.** Retrieval cascades from cheap exact
   matches to semantic search, degrading to a built-in body scorer when external
   tools (`rg`, `fff`, `semble`) are absent.

## Component map

```
Agent  (claude · pi · omp · codex — via symlinked skill dirs into .conversate)
  │  reads SKILL.md (invariants + routing table),
  │  routes to references/*.md playbooks: save · resume · list · branching · cli,
  ▼
.conversate/scripts/conv_cli.py   —  owns every read and write
  │
  ├─▶ .conversate/convs/*.md     SOURCE OF TRUTH (TOML +++ frontmatter)
  ├─▶ .conversate/index.jsonl    DERIVED CACHE, rebuilt from convs/ (exact / rg search)
  └─▶ .conversate/.semble/       semantic-search cache (semble / rg / fff)
```

## The store layout (`.conversate/`)

The conv root **is** the `.conversate/` directory. The CLI resolves it in this order
(`resolve_conv_root` → `Resolution`):
1. `--conv-root` CLI flag (layer `flag`), then
2. `$CONVERSATE_ROOT` environment variable (layer `env-conversate`), then
3. `$BRAIN_CONV` environment variable (legacy, layer `env-brain`), then
4. **marker search** (layer `marker`): walk the cwd's ancestors, then the script
   directory's ancestors, nearest-first. Within a dir: a dir *named* `.conversate` is the
   root; a `.conv-root` sentinel makes *that dir* the root; a `.conversate/` subdir means
   `<dir>/.conversate`; a legacy `conv/` subdir means `<dir>/conv`. The walk stops at a
   `.git` boundary but still checks the repo-root dir itself (so `.conversate` next to
   `.git` resolves). The first marker found wins.
5. else **fail loud** (layer `none`): a clean `ConvError` naming the overrides — never a
   path-arithmetic guess. `init` is the sole exception: with nothing to resolve it
   bootstraps `<cwd>/.conversate/`.

`init` writes a `.conv-root` sentinel and a `.gitignore` into the resolved root, so the
store is rediscoverable by later bare commands from anywhere beneath it. `doctor` echoes
the resolution as `resolution = { layer, marker }`.

```
.conversate/                # the store root IS this directory
├── .conv-root              # sentinel marker written by init; makes the store rediscoverable
├── .gitignore              # ignores .semble/, index.jsonl, __pycache__/ (records stay tracked)
├── convs/                  # *.md conversation records — source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl             # derived cache, one conversation record per line
├── .semble/                # semantic search cache directory
└── scripts/conv_cli.py     # the engine (harness skill dirs symlink to .conversate)
```

## The conversation file format

```toml
+++
id = "conv_260616_auth-redesign"     # conv_<YYMMDD>_<topic-slug>
topic = "auth redesign"
status = "active"                     # active | parked | closed
tags = ["auth", "infra"]
refs = [
  { id = "conv_260615_login-bug", rel = "spawned-from" },
]
created = "2026-06-16T00:00:00Z"      # ISO-8601 UTC, Z-suffixed
updated = "2026-06-16T01:00:00Z"
+++
## summary
One line describing what this conversation is.

## dict
- **term** - agreed meaning.

## qa
- **Q:** question? **A:** answer.
- **Q (open):** an unresolved thread.

## resume
- goal: the one-line objective.
- next-steps:
  - the next concrete action.
- open-questions:
  - a live thread to pick up.
- suggested-skills:
  - conv:resume

## user-instructions
- a standing directive the user set (e.g. tone, constraints).

## condensed-transcript
- U: a compressed user turn.
- A: a compressed agent turn (artifacts referenced by path).

## sources       (optional)
## insights      (optional)
## decisions     (optional, append-only by convention)
## digest        (optional, written when a branch returns)
```

Frontmatter is intentionally **thin**: `id, topic, status, tags, refs, created,
updated` only. Everything else lives in the body sections. `summary`, `dict`, and `qa`
are mandatory; `resume`, `user-instructions`, and `condensed-transcript` always appear
(rendered `(none)` when empty). Section render order is fixed: `summary, dict, qa, resume,
user-instructions, condensed-transcript, sources, insights, decisions, digest`, with any
extra sections appended alphabetically.

## Relationship graph (refs)

Conversations link to each other through `refs`, and every relation has a reverse
that the CLI keeps in sync automatically (`regen-refs`, also run on every `upsert`):

| forward          | reverse          | meaning                                      |
|------------------|------------------|----------------------------------------------|
| `spawned-from`   | `spawned-to`     | a sidekick branch off a parent               |
| `continued-from` | `continued-as`   | the same topic resumed in a clean session    |
| `informed-by`    | `informed`       | one conversation fed insight into another    |

This makes the store a small bidirectional graph: parents surface their branches,
continuations chain back to their origin, and cross-pollination is traceable.

## Status lifecycle

- `active` — currently live / recently worked.
- `parked` — set aside intentionally (e.g. while a sidekick runs), to be resumed.
- `closed` — concluded; a returned branch ends here, often carrying a `## digest`.

`list` orders by status (`active → parked → closed`) then by most-recent `updated`.

## Search cascade (retrieval)

`search` short-circuits at the first layer that yields a confident hit:

1. **Filename / id match** (`fff` layer label) — substring scoring over id + file path.
2. **Index field match** — `rg` over `index.jsonl` (topic/tags/refs/status), or a
   pure-Python field scorer when `rg` is missing (`rg-index-fallback`).
3. **Semantic** — `semble search ... .conversate/convs --content docs` (uses installed
   `semble`, or `uvx semble` when `CONV_USE_UVX_SEMBLE=1`).
4. **Body fallback** — built-in term scoring over conversation bodies
   (`semble-body-fallback`) when no semantic engine is available.

A single confident hit at any layer is returned immediately; otherwise ranked hits
are returned for the agent to disambiguate with the user.

## Auto-save trigger

conversate is agent-agnostic about auto-save. Where a harness supports hooks, an optional
turn counter injects the reminder; where it does not, the skill self-triggers a save at
milestones and before the session ends.

For Claude Code, `hooks/claude/conv-turn-counter.ps1` is a `UserPromptSubmit` hook that
keeps a per-session counter in `$TEMP/conv-session-<session_id>.count` (keyed by
`session_id`, not PID). Once the count reaches 10 (and every 10 after) it prints
`CONV AUTO-SAVE: threshold reached ...` on stdout, which Claude Code injects into context.
The skill then runs the save flow silently and tells the user only
`Auto-saved as <id> - rename anytime.` Registration lives in
`hooks/claude/settings-snippet.json`; see `hooks/README.md`.

## Current build status (snapshot)

- **Implemented & complete:** agent-neutral `SKILL.md`, all five `references/*.md`
  playbooks, and the full `scripts/conv_cli.py` (9 subcommands). The store is the
  self-contained `.conversate/` directory (records in `convs/`); resolution is
  `flag → env-conversate → env-brain → marker → fail-loud` (with `init` bootstrapping
  `<cwd>/.conversate`), `init` writes the `.conv-root` sentinel and `.gitignore`, and
  `doctor` reports the resolution layer and warns about records missing the resumption
  sections. Records carry the resumption sections `resume`, `user-instructions`,
  `condensed-transcript`. The test suite — `test_marker_resolution.py`,
  `test_doctor_resolution_report.py`, `test_store_layout.py`, `test_record_schema.py`
  (34 tests) — is green.
- **Recreated:** the Claude Code turn-counter hook now lives in `hooks/claude/`.
- **External / not in repo:** the `.conversate/` store itself (created at runtime by
  `init`); the installer that symlinks harness skill dirs and registers hooks.
- **Repo state:** `.arca/` IS tracked in git.

See `manifest.md` for the file-by-file breakdown and `flows.md` for the workflows and
state machines.
