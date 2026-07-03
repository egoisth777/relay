# conversate — File Manifest

A thin map of every file in the system and what it does. Paths are relative to the repo
root. At runtime the skill is packaged as `.conversate/`, which each harness reaches
through a symlinked skill dir named `conversate` (`.claude/skills/conversate`,
`.agents/skills/conversate`, ...).

## Top level

| file | role |
|------|------|
| `SKILL.md` | The agent-neutral skill entry point. YAML frontmatter (`name: conversate`, trigger description) plus the **invariants**, the **routing table** (which reference doc to read per command), the store-location note, and the **auto-save behavior** contract. This is what a harness loads to decide when and how to use the skill. |
| `README.md` | Human-facing repo readme: what conversate is (agent-agnostic recorder), the `.conversate/` layout, supported harnesses, record format, CLI quickstart, requirements. |
| `LICENSE` | License text. |
| `.gitignore` | Repo ignore rules (Python + `.semble/`). |

## hooks/

| file | role |
|------|------|
| `hooks/README.md` | What per-harness hooks exist and that they are an installer concern. |
| `hooks/claude/conv-turn-counter.ps1` | Claude Code `UserPromptSubmit` hook: per-session prompt counter (keyed by `session_id`) that prints the `CONV AUTO-SAVE` reminder every 10 prompts. |
| `hooks/claude/settings-snippet.json` | The hook registration block to merge into `.claude/settings.json`; points at `.conversate/hooks/claude/conv-turn-counter.ps1`. |

## scripts/

| file | role |
|------|------|
| `scripts/conv_cli.py` | The entire engine. A single-file Python 3.11+ CLI (uses `tomllib`) that owns every read and write to the store. No third-party Python deps. Optionally shells out to `rg`, `fff`, `semble`, `uvx`. Exposes 9 subcommands (see below). |

### `conv_cli.py` internals (what each part does)

**Store resolution & layout**
- `resolve_conv_root()` → `Resolution(root, layer, marker)` — resolve the store root by
  layer (`flag` → `env-conversate` → `env-brain` → `marker` → `none`). `find_marker()`
  walks the cwd's then the script dir's ancestors (nearest-first, stopping at a `.git`
  boundary but still checking the repo root) for a dir *named* `.conversate`, a `.conv-root`
  sentinel, a `.conversate/` subdir, or a legacy `conv/` subdir.
- `conv_root()` / `_root_or_raise()` — unwrap the resolution, raising a clean `ConvError`
  (naming `--conv-root`, `$CONVERSATE_ROOT`, `$BRAIN_CONV`) when nothing resolves — never
  path arithmetic.
- `write_sentinel()` — drop a `.conv-root` marker in the root (called by `init`).
- `write_gitignore()` — drop a `.gitignore` (ignoring `.semble/`, `index.jsonl`,
  `__pycache__/`) in the root (called by `init`).
- `resolution_report()` — `{layer, marker}` for `doctor`.
- `convs_dir()`, `index_path()`, `gitignore_path()`, `ensure_layout()` — paths for
  `convs/`, `index.jsonl`, `.gitignore`, `.semble/`; create them idempotently.

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
  enforcing the mandatory `summary`/`dict`/`qa` and the fixed section order, and always
  emitting the resumption sections (`(none)` when empty).
- `render_resume()`, `render_user_instructions()`, `render_condensed_transcript()`,
  `resumption_sections()`, `_as_items()` — render the structured `resume` /
  `user_instructions` / `condensed_transcript` payload keys into the always-on sections.
- `normalize_section_value()` — coerce list/None section inputs to text.
- `count_open()` — count open threads (`Q (open)` / `open:`) for the index `open` column.
- `missing_always_sections()` — resumption sections absent from a body (drives `doctor` warnings).

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
- `search_semble()` — drive `semble` (or `uvx semble`) over `.conversate/convs`.
- `resolve()` — id-or-query → exactly one conversation, else raise ambiguous.

**Command handlers & CLI**
- `cmd_init`, `cmd_rebuild_index`, `cmd_regen_refs`, `cmd_upsert`, `cmd_set_status`,
  `cmd_list`, `cmd_search`, `cmd_show`, `cmd_doctor` — one per subcommand.
- `print_json()`, `print_table()` — output formatting.
- `build_parser()` / `main()` — argparse wiring; `ConvError` → exit code 2.

### CLI subcommands

| command | what it does |
|---------|--------------|
| `init` | Create `.conversate/`, `convs/`, `.semble/`, write the `.conv-root` sentinel and `.gitignore`, and rebuild the index; with no override, targets `<cwd>/.conversate/`. |
| `upsert --stdin` / `--json PATH` `[--status ...]` | Create or replace a conversation from JSON (structured `resume`/`user_instructions`/`condensed_transcript` keys → always-on sections); reconciles refs and rebuilds index. |
| `rebuild-index` | Regenerate `index.jsonl` from `.conversate/convs/*.md` (tolerates legacy records missing the resumption sections). |
| `regen-refs` | Repair missing reverse refs across the store, then rebuild index. |
| `list [--status] [--json] [--limit N]` | Index-only listing, ordered active→parked→closed then recency. |
| `search "<query>" [--limit N]` | Tiered filename/index/semantic/body search (JSON output). |
| `show <id-or-query> [--markdown]` | Print one conversation as JSON record or raw markdown. |
| `set-status <id> <status>` | Set status to `active`/`parked`/`closed` and bump `updated`. |
| `doctor` | Report the resolved root + resolution layer (`resolution: {layer, marker}`), validate layout, optional tool availability, file parseability, and index count, and WARN about records missing the resumption sections. Tolerates an unresolved root (layer `none`). |

## references/

Playbooks the skill reads on demand per the `SKILL.md` routing table. They contain
agent instructions (what to extract, what order to reconstruct in), not code.

| file | covers commands | what it tells the agent |
|------|-----------------|--------------------------|
| `references/save.md` | `conv:save`, `conv:park`, auto-save, "save/checkpoint this" | Extraction priority (`dict` > user-instructions > resume > qa > condensed-transcript > sources/insights/decisions), the redact / reference-don't-duplicate rules, the full `upsert` payload shape, and how to report the save. |
| `references/resume.md` | `conv:resume`, "continue where we left off" | Resolving a target via `search`, the reconstruction order (summary → user-instructions → dict → resume → qa → condensed-transcript → decisions/insights/sources), adopting user-instructions and acting on resume.next-steps, and marking the conversation `active`. |
| `references/list.md` | `conv:list`, "what's open" | The three `list` invocations and the fact that listing reads only the index, not the markdown. |
| `references/branching.md` | `conv:sidekick`, `conv:return`, `conv:continue` | The branch lifecycle: probe vs. sidekick modes, parking the parent, returning a `## digest`, and the continue-in-clean-session flow with `continued-from` refs. |
| `references/cli.md` | `conv:regen`, drift checks, troubleshooting | Full CLI reference: the `.conversate/` layout, root resolution (`CONVERSATE_ROOT`, `.conversate` markers), commands, the turn-counter hook, ref-regen semantics, the semantic search config, and the `upsert` payload keys (`resume`/`user_instructions`/`condensed_transcript`). |

## .arca/space/conversate-sp/what/  (this folder)

| file | role |
|------|------|
| `architecture.md` | System design, component map, store/file formats, ref graph, search cascade, build status. |
| `manifest.md` | This file — what every file does. |
| `flows.md` | Workflows and state machines (Mermaid). |

## Runtime / external (not source files in this repo)

| path | role |
|------|------|
| `.conversate/` (`.conv-root`, `.gitignore`, `convs/`, `index.jsonl`, `.semble/`) | The conversation store, created at runtime by `init`. The `.conv-root` sentinel marks the root for later marker-based resolution. |
| `.claude/skills/conversate`, `.agents/skills/conversate`, ... | Per-harness skill dirs (named `conversate`) symlinked into `.conversate` by the installer, so every harness runs the CLI at `.conversate/scripts/conv_cli.py`. |
| `.claude/settings.json` | Where the user merges `hooks/claude/settings-snippet.json` to register the turn-counter hook. |
