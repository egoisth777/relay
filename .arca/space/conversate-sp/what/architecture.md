# conversate — Architecture

> This folder (`.arca/space/conversate-sp/what/`) is the natural-language source of
> truth for the **conversate** skill. The files here *reflect and define* the whole
> system. If code and these docs disagree, treat it as a drift to reconcile.

## What conversate is

conversate is a **conversation memory manager** packaged as a Claude Code skill
(skill name: `conv`). It lets an agent persist, retrieve, list, park, branch,
return, and continue *topic-bound conversations* so that headspace survives across
sessions and context windows.

The core idea: a conversation is not a transcript. It is a distilled, reconstructable
artifact — a topic plus a dictionary of agreed language, a Q&A spine, and optional
sources/insights/decisions — written so a *cold* agent can recover the mental model
without replaying the chat.

## Design pillars

1. **Markdown is the source of truth.** Each conversation is one Markdown file under
   `conv/log/*.md` with **TOML frontmatter** delimited by `+++`. The file is the
   durable, human-editable record.
2. **The index is a derived cache.** `conv/index.jsonl` is rebuilt from the log files
   on every write. It is never authoritative; it can be deleted and regenerated.
3. **The CLI owns all writes.** A single Python helper (`scripts/conv_cli.py`)
   performs every mutation: creating files, normalizing frontmatter, reconciling
   bidirectional refs, and rebuilding the index. The skill prose never hand-edits the
   store; it shells out to the CLI.
4. **The skill (LLM) owns extraction + judgment.** Deciding *what* to save (topic,
   tags, the high-value `dict`, open questions) and *which* conversation to resume is
   the agent's job, guided by `SKILL.md` + the `references/*.md` playbooks.
5. **Mandatory shape.** Every conversation must have a `topic` and the three body
   sections `## summary`, `## dict`, `## qa`. `dict` (the ubiquitous language) is the
   highest-value section and is reconstructed first.
6. **Tiered search, graceful degradation.** Retrieval cascades from cheap exact
   matches to semantic search, degrading to a built-in body scorer when external
   tools (`rg`, `fff`, `semble`) are absent.

## Component map

```
┌─────────────────────────────────────────────────────────────────────┐
│  Claude Code agent                                                     │
│                                                                       │
│   reads ┌──────────────┐  routes to  ┌────────────────────────────┐   │
│  ──────▶│   SKILL.md   ├────────────▶│  references/*.md playbooks  │   │
│         │ (manifest +  │             │ save · resume · list ·      │   │
│         │  invariants  │             │ branching · cli             │   │
│         │  + routing)  │             └─────────────┬──────────────┘   │
│         └──────────────┘                           │ "run this CLI"    │
│                                                    ▼                   │
│                                       ┌────────────────────────────┐   │
│                                       │   scripts/conv_cli.py      │   │
│                                       │   (all reads + writes)     │   │
│                                       └─────────────┬──────────────┘   │
└─────────────────────────────────────────────────────┼─────────────────┘
                                                       │
                          ┌────────────────────────────┴───────────┐
                          ▼                                         ▼
              ┌──────────────────────┐                  ┌──────────────────────┐
              │  conv/log/*.md       │  rebuild ───────▶│  conv/index.jsonl    │
              │  SOURCE OF TRUTH     │◀──── derived     │  DERIVED CACHE       │
              │  TOML +++ frontmatter│                  │  one JSON per line   │
              └──────────────────────┘                  └──────────────────────┘
                          │                                         ▲
                          │ optional semantic layer                 │ exact/rg search
                          ▼                                         │
              ┌──────────────────────┐                              │
              │  conv/.semble/ cache │   semble / rg / fff ─────────┘
              │  (semantic search)   │
              └──────────────────────┘
```

## The store layout (`conv/`)

The CLI resolves the store root in this order:
1. `--conv-root` CLI flag, then
2. `$BRAIN_CONV` environment variable, then
3. `<repo-root>/conv`, where repo-root is the script's path `parents[4]`
   (i.e. the CLI expects to live at `<repo>/.claude/skills/conv/scripts/conv_cli.py`).

```
conv/
├── log/                 # *.md conversation files — source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl          # derived cache, one conversation record per line
└── .semble/             # semantic search cache directory
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

## sources       (optional)
## insights      (optional)
## decisions     (optional, append-only by convention)
## digest        (optional, written when a branch returns)
```

Frontmatter is intentionally **thin**: `id, topic, status, tags, refs, created,
updated` only. Everything else lives in the body sections. Section render order is
fixed: `summary, dict, qa, sources, insights, decisions, digest`, with any extra
sections appended alphabetically.

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
3. **Semantic** — `semble search ... conv/log --content docs` (uses installed
   `semble`, or `uvx semble` when `CONV_USE_UVX_SEMBLE=1`).
4. **Body fallback** — built-in term scoring over conversation bodies
   (`semble-body-fallback`) when no semantic engine is available.

A single confident hit at any layer is returned immediately; otherwise ranked hits
are returned for the agent to disambiguate with the user.

## Auto-save trigger

A session-scoped turn counter hook
(`.claude/hooks/conv-turn-counter.ps1`, referenced by `references/cli.md`) tracks
turns in `$TEMP/conv-session-<PID>.count`. When the count exceeds 10 it injects a
`CONV AUTO-SAVE: threshold reached` reminder. The skill then runs the save flow
silently and tells the user only `Auto-saved as <id> - rename anytime.`

> Note: the hook script is part of the deployed harness, not currently present in
> this repository checkout.

## Current build status (snapshot)

- **Implemented & complete:** `SKILL.md`, all five `references/*.md` playbooks, and
  the full `scripts/conv_cli.py` (9 subcommands).
- **Stub:** `README.md` (one line).
- **External / not in repo:** the PowerShell turn-counter hook; the `conv/` store
  itself (created at runtime by `init`).
- **Repo state:** initial-import commits only; `.arca/` is untracked.

See `manifest.md` for the file-by-file breakdown and `flows.md` for the workflows and
state machines.
