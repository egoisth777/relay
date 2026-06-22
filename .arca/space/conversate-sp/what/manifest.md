# conversate — File Manifest

A thin map of every file in the system and what it does. Paths are relative to the
skill root (`engineering/conversate/`, deployed as `.claude/skills/conv/`).

## Top level

| file | role |
|------|------|
| `SKILL.md` | The skill entry point. TOML/YAML frontmatter (`name: conv`, trigger description) plus the **invariants**, the **routing table** (which reference doc to read per command), and the **auto-save behavior** contract. This is what Claude Code loads to decide when and how to use the skill. |
| `README.md` | Human-facing repo readme. Currently a one-line stub ("conversation manager"). |
| `LICENSE` | License text. |
| `.gitignore` | Ignore rules for the repo. |

## scripts/

| file | role |
|------|------|
| `scripts/conv_cli.py` | The entire engine. A single-file Python 3.11+ CLI (uses `tomllib`) that owns every read and write to the store. No third-party Python deps. Optionally shells out to `rg`, `fff`, `semble`, `uvx`. Exposes 9 subcommands (see below). |

### `conv_cli.py` internals (what each part does)

**Store resolution & layout**
- `repo_root_from_script()` / `conv_root()` — resolve the store root (`--conv-root` →
  `$BRAIN_CONV` → `<repo>/conv`).
- `log_dir()`, `index_path()`, `ensure_layout()` — paths for `log/`, `index.jsonl`,
  `.semble/`; create them idempotently.

**Frontmatter & file IO**
- `split_frontmatter()` — parse the `+++ ... +++` TOML block off a file.
- `read_conv()` / `read_all()` / `find_by_id()` — load conversations (with a
  `tolerate` mode that skips unparseable files).
- `dump_frontmatter()` / `write_conv()` — render thin TOML frontmatter and write the
  file only when content actually changes (byte-stable, idempotent writes).
- `normalize_meta()`, `normalize_ref()`, `normalize_refs()` — validate and canonicalize
  metadata (status whitelist, sorted unique tags, sorted unique refs, ISO-UTC dates).
- `iso_value()`, `now_utc()`, `toml_quote()`, `toml_list()` — value formatting helpers.

**Identity**
- `slugify()`, `make_id()`, `make_unique_id()`, `file_name_for_id()` — generate
  `conv_<YYMMDD>_<slug>` ids, ensure uniqueness, and map id → `YYYY-MM-DD_<slug>.md`.

**Body sections**
- `sections_from_body()` — split body into `## section` map.
- `build_body()` — assemble body from a `sections` object (or accept a raw `body`),
  enforcing the mandatory `summary`/`dict`/`qa` and the fixed section order.
- `normalize_section_value()` — coerce list/None section inputs to text.
- `count_open()` — count open threads (`Q (open)` / `open:`) for the index `open` column.

**Index & refs**
- `index_record()` / `rebuild_index()` / `read_index()` — build and read the derived
  `index.jsonl` cache (one compact JSON object per line, sorted by id).
- `upsert()` — the main write path: normalize → build body → write file → `regen_refs`
  → `rebuild_index`.
- `set_status()` — change a conversation's status + timestamp, then rebuild index.
- `regen_refs()` — reconcile bidirectional refs across the whole store using
  `REL_REVERSE`, writing reverse links where missing.

**Search**
- `stopwords()`, `query_terms()`, `text_score()` — query tokenization and scoring.
- `search()` — the tiered cascade (filename → index/`rg` → semble → body fallback).
- `search_semble()` — drive `semble` (or `uvx semble`) over `conv/log`.
- `resolve()` — id-or-query → exactly one conversation, else raise ambiguous.

**Command handlers & CLI**
- `cmd_init`, `cmd_rebuild_index`, `cmd_regen_refs`, `cmd_upsert`, `cmd_set_status`,
  `cmd_list`, `cmd_search`, `cmd_show`, `cmd_doctor` — one per subcommand.
- `print_json()`, `print_table()` — output formatting.
- `build_parser()` / `main()` — argparse wiring; `ConvError` → exit code 2.

### CLI subcommands

| command | what it does |
|---------|--------------|
| `init` | Create `conv/`, `conv/log/`, `conv/.semble/` and rebuild the index. |
| `upsert --stdin` / `--json PATH` `[--status ...]` | Create or replace a conversation from JSON; reconciles refs and rebuilds index. |
| `rebuild-index` | Regenerate `index.jsonl` from the log files. |
| `regen-refs` | Repair missing reverse refs across the store, then rebuild index. |
| `list [--status] [--json] [--limit N]` | Index-only listing, ordered active→parked→closed then recency. |
| `search "<query>" [--limit N]` | Tiered filename/index/semantic/body search (JSON output). |
| `show <id-or-query> [--markdown]` | Print one conversation as JSON record or raw markdown. |
| `set-status <id> <status>` | Set status to `active`/`parked`/`closed` and bump `updated`. |
| `doctor` | Validate layout, optional tool availability, file parseability, and index count. |

## references/

Playbooks the skill reads on demand per the `SKILL.md` routing table. They contain
agent instructions (what to extract, what order to reconstruct in), not code.

| file | covers commands | what it tells the agent |
|------|-----------------|--------------------------|
| `references/save.md` | `conv:save`, `conv:park`, auto-save, "save/checkpoint this" | How to extract state by priority (`dict` highest), the JSON shape to pipe to `upsert`, and how to report the save. |
| `references/resume.md` | `conv:resume`, "continue where we left off" | How to resolve a target via `search`, the reconstruction reading order (`dict` first), and to mark the conversation `active` after loading. |
| `references/list.md` | `conv:list`, "what's open" | The three `list` invocations and the fact that listing reads only the index, not the markdown. |
| `references/branching.md` | `conv:sidekick`, `conv:return`, `conv:continue` | The branch lifecycle: probe vs. sidekick modes, parking the parent, returning a `## digest`, and the continue-in-clean-session flow with `continued-from` refs. |
| `references/cli.md` | `conv:regen`, drift checks, troubleshooting | Full CLI reference, the turn-counter hook, ref-regen semantics, the semantic search layer config, and the `upsert` JSON shape. |

## .arca/space/conversate-sp/what/  (this folder)

| file | role |
|------|------|
| `architecture.md` | System design, component map, store/file formats, ref graph, search cascade, build status. |
| `manifest.md` | This file — what every file does. |
| `flows.md` | Workflows and state machines (Mermaid). |

## Runtime / external (not source files in this repo)

| path | role |
|------|------|
| `conv/` (`log/`, `index.jsonl`, `.semble/`) | The conversation store, created at runtime by `init`. |
| `.claude/hooks/conv-turn-counter.ps1` | Session turn counter that emits the auto-save reminder past 10 turns. Referenced by `cli.md`; lives in the deployed harness, not this checkout. |
