# Relay v2 — Performance, Scalability & Recovery Fidelity

- **Status:** Draft for review
- **Branch:** `feature/relay-perf` (worktree `.claude/worktrees/relay-perf`)
- **Base:** `04ef7b6` (conversate→relay rebrand, Rust CLI)
- **Date:** 2026-07-16
- **Owners:** Relay maintainers

## 1. Motivation

Relay's Rust CLI is correct but architecturally O(N) on nearly every operation: most
commands re-read and re-parse the entire Relay archive one file at a time, some more
than once. The pressure is already visible in the repo's own history — commit `6e54eb7`
*raised* the record-op latency budgets from 100/125 ms to 140/175 ms because
`upsert`/`regen-refs`/`rebuild-index` no longer fit, at only **100 records**.

This spec redesigns the runtime around five goals (the feature request, verbatim):

| # | Goal | Spec section |
|---|------|--------------|
| 1 | Multi-threaded relay conversations lookup | §4 Scan engine, §7 Search v2 |
| 2 | Better database design | §5 Index v2, §6 Mutation engine |
| 3 | Extended scalability | §5, §9 Budgets (10k-record gates) |
| 4 | Blazingly fast and lightweight | §6, §8 Micro-optimizations (zero new deps, one dep removed) |
| 5 | Better fidelity recovering previous conversations (memory relay / context relay) | §10 Fidelity v2 |

Everything here preserves the core invariants in `AGENTS.md` and `SKILL.md`:
Markdown handoff records under `~/.relay/convs/` remain the **source of truth**;
`index.jsonl` remains a **derived, rebuildable cache** with an unchanged line format;
the CLI owns all mutations; `~/.conversate/` stays read-only.

## 2. Current architecture and measured bottlenecks

All references are to the code at base commit `04ef7b6`.

### 2.1 Full-archive scans

`all_convs()` (`src/main.rs:350`) does a sequential `read_to_string` + TOML parse +
section split of every record. It is called by:

- `rebuild()` (`src/main.rs:789`) — every index rebuild.
- `regen()` (`src/main.rs:917`) — reads all N records, recomputes the *entire*
  reverse-ref graph, rewrites any changed record.
- `find()` fallback (`src/main.rs:401`) — any lookup that isn't an exact
  `conv_YYMMDD_slug` filename hit degenerates to a full scan.
- body-fallback search (`src/main.rs:1089`) — re-reads every record body.

### 2.2 Scan multiplication per command

| Command | Full-archive passes today |
|---|---|
| `upsert` (with refs) | `regen()` + `rebuild()` = **2 scans + up to N record rewrites** (`src/main.rs:899-901`) |
| `sidekick` / `continue` | upsert's 2 scans, then parent-park + `regen()` + `rebuild()` again = **≥4 scans** (`src/main.rs:1443-1452`) |
| `return` | `regen()` + `rebuild()` = **2 scans** (`src/main.rs:1533-1534`) |
| `set-status` | full `rebuild()` for a one-record change (`src/main.rs:1333`) |
| `search` (miss on index tiers) | full body scan (`src/main.rs:1089`) |

### 2.3 Index pathologies

- `read_index()` + `valid_index()` (`src/main.rs:808-861`) call `is_file()` on **every**
  record path on **every index read** — O(N) syscalls just to trust the cache.
- One invalid index line in tolerant mode discards the whole index
  (`return Ok(vec![])`, `src/main.rs:820`) and triggers a full rebuild.
- `write_index()` (`src/main.rs:799`) rewrites the entire file per mutation — O(N)
  write amplification.
- Search tier 2 (`src/main.rs:1062`) serializes each index record with
  `serde_json::to_string` per query just to substring-match it.

### 2.4 Per-call recompilation and misc

- `Regex::new` runs inside every call of `filename()` (`src/main.rs:273`, called per
  record in hot loops), `sections()`/`sections_allow_dup()`/`duplicates()`/
  `count_open()` (`src/main.rs:437-489`, per record per rebuild), and `terms()`
  (`src/main.rs:990`).
- ID collision probing in `normalize_meta()` (`src/main.rs:746-754`) calls `find()`
  per candidate — each potentially a full scan.
- `all_convs` sorts with `sort_by_key(|c| c.path.clone())` — clones every `PathBuf`.
- Everything is single-threaded; the only concurrency in the binary is the semble
  subprocess plumbing (`src/search_backend.rs`), with a hardwired 20 s timeout
  (`src/search_backend.rs:38`).
- Latent bug worth fixing in passing: `import` preserves nested `convs/**/` layouts
  (`src/main.rs:1590`), but `all_convs()` reads only the top level of `convs/` —
  imported nested records silently vanish from the index.

### 2.5 Fidelity gaps

Recovery today rests on `## resume`, `## user-instructions`, and
`## condensed-transcript` (flat, unweighted). A resuming agent must run `show`, then
manually chase frontmatter refs for branch digests. There is no notion of execution
environment, touched artifacts, or completed checkpoints, and no way to fit the
rehydration payload to a context budget.

## 3. Design overview

Keep Markdown records as the source of truth and rebuild the runtime around four
pillars, plus a fidelity layer:

1. **Scan engine (§4):** one parallel directory-scan + parse service used by every
   command that must touch many records.
2. **Index v2 (§5):** a fingerprinted, incremental cache under `.semble/index-v2/`
   that makes "rebuild" O(changed files), with `index.jsonl` kept byte-compatible as
   the derived export.
3. **Single-pass mutation engine (§6):** every mutating command performs exactly one
   snapshot, targeted record writes, *incremental* reverse-ref reconciliation, and one
   index emit.
4. **Search v2 (§7):** tiered lookup backed by term postings instead of N substring
   scans, with the semble tier unchanged.
5. **Fidelity v2 (§10):** richer capture schema plus a new `relay context` command —
   the "context relay" — that emits a budget-aware, reconstruction-ordered context
   pack.

## 4. Scan engine (multi-threaded lookup)

A single internal API replaces ad-hoc `all_convs()` call sites:

```rust
struct ScanEngine { workers: usize }

impl ScanEngine {
    /// Snapshot convs/ recursively: (relative path, size, mtime) per .md file.
    fn snapshot(root: &Path) -> io::Result<Vec<FileStat>>;

    /// Parse the given files across the worker pool; deterministic output
    /// (collected, then sorted by path).
    fn parse_files(&self, root: &Path, files: &[FileStat], tolerate: bool)
        -> Result<Vec<Conv>, ConvError>;
}
```

- **Worker model:** `std::thread::scope` + an `AtomicUsize` cursor over the file list
  (work stealing by chunk of 16). No rayon, no new dependencies — ~40 lines.
- **Worker count:** `available_parallelism().clamp(1, 8)`, overridable with
  `RELAY_SCAN_THREADS` (value `1` forces sequential, used by determinism tests).
- **Determinism:** results are collected and sorted by relative path before return, so
  parallel and sequential runs are byte-identical downstream.
- **Recursive snapshot:** fixes the §2.4 import bug — nested records are indexed.
  `snapshot()` collects metadata during the directory walk (cheap on Windows, where
  `DirEntry::metadata` reads from the directory itself), so no second stat pass exists
  anywhere.
- **Consumers:** cold index build, changed-file re-parse (§5), `regen-refs` full sweep,
  body-scoring search fallback, `doctor` record validation.

## 5. Index v2 (better database design)

### 5.1 Why not SQLite (alternatives considered)

- **SQLite / redb / sled:** a real database file would become a second source of
  truth, conflicting with the "index is a derived cache that can be rebuilt" invariant;
  bundling SQLite adds ~700 KB plus C-toolchain friction on Windows for zero benefit at
  ≤100k small text records. Rejected.
- **tantivy** (full-text engine): orders of magnitude heavier than the problem.
  Rejected.
- **Chosen:** a fingerprinted incremental cache in the existing `.semble/` private
  directory, JSONL-encoded (debuggable, greppable, serde_json already a dependency),
  deriving the compat `index.jsonl` on change.

### 5.2 Layout

```
~/.relay/
├── index.jsonl              # UNCHANGED byte format — derived export, still greppable
└── .semble/
    ├── write.lock           # existing exclusive mutation lock (unchanged)
    └── index-v2/
        ├── manifest.json    # { "version": 1, "generation": N, "records": N, "jsonl_synced": true }
        ├── records.jsonl    # one row per record: index fields + fingerprint + open count
        └── postings.jsonl   # search term -> [record ordinals]   (§7)
```

`records.jsonl` row =
`{id, topic, status, tags, refs, created, updated, file, open, size, mtime_ns, fp}`
where `fp` is an FNV-1a 64-bit hash of file content (FNV-1a is ~10 lines inline —
still zero new dependencies).

### 5.3 Freshness algorithm (every command, read or write)

1. `ScanEngine::snapshot()` — one recursive readdir, the **only** filesystem
   enumeration in the command.
2. Diff snapshot against `records.jsonl` rows by `(size, mtime_ns)`:
   - match → trust cached row (no file read at all);
   - mismatch or new file → parse in parallel (§4), recompute row + `fp`;
   - cached row with no file → drop (tombstone-free: the snapshot is authoritative).
3. If anything changed: `generation += 1`, atomically rewrite `records.jsonl`,
   `postings.jsonl`, `manifest.json`, and `index.jsonl` (all via the existing
   `write_atomic` temp+rename).

Consequences:

- "Rebuild" becomes **O(changed)** parses + one readdir. Warm `rebuild-index` on an
  unchanged 10k-record archive is readdir-bound.
- `valid_index()`'s per-record `is_file()` storm is deleted — validity comes from the
  snapshot taken once.
- A single corrupt cache/index line no longer nukes the whole index: the offending row
  is repaired from its source record; only a bad `manifest.json`/version mismatch
  forces a full (parallel) rebuild.
- Deleting `.semble/index-v2/` (or `index.jsonl`) self-heals on the next command —
  the derived-cache invariant holds for the whole v2 layer.

### 5.4 Fingerprint edge cases

- mtime granularity (FAT/network shares, restored backups): when mtime is suspicious
  (zero, or size matches but mtime regressed), fall back to content hash comparison
  before trusting the row.
- `rebuild-index --full` ignores the cache entirely (parallel cold build).
- `RELAY_NO_CACHE=1` disables index-v2 reads and writes for the invocation — the
  escape hatch and the reference behavior for equivalence tests (§11).

### 5.5 Concurrency

- Mutating commands already hold the exclusive `write.lock`
  (`src/main.rs:1199-1211`); all index-v2 writes happen only under it.
- Read-only commands never *require* the lock; if freshening discovered changes, they
  opportunistically `try_lock` to persist the warmed cache and silently skip
  persistence on contention (correct output either way — the freshened state lives in
  memory).
- `generation` + atomic rename gives readers a consistent view; a torn/partial state is
  impossible by construction (rename is atomic on NTFS and POSIX).

### 5.6 Archive layout at scale

The flat `convs/` directory remains the default. Because the scan is now recursive
(§4), users with very large archives may shard as `convs/2026/…` manually or via a
future `relay compact --shard-by-year`; the `file` field in `index.jsonl` already
carries relative paths, and `import` already produces nested layouts. No behavior
change is required in this spec beyond recursive scanning; sharding is documented as
supported, not automated.

## 6. Single-pass mutation engine

One internal transaction type replaces the scattered `regen()` + `rebuild()` pairs:

```rust
struct StoreTxn<'a> {
    root: &'a Path,
    snapshot: Vec<FileStat>,   // from ScanEngine
    cache: IndexCache,         // freshened per §5.3
    dirty: Vec<RecordWrite>,   // records to write at commit
}
```

- **Incremental reverse-ref reconciliation.** When a record's refs change, only that
  record and its ref *targets* need reconsideration — O(degree), not O(N). The
  forward/reverse pairs (`spawned-from`/`spawned-to`, `continued-from`/`continued-as`,
  `informed-by`/`informed`) are computed from cache rows, and only affected neighbor
  records are rewritten. `regen-refs` remains the full repair sweep (now parallel) for
  `doctor --fix` and manual reconciliation.
- **One emit.** `txn.commit()` writes dirty records (atomic each), then emits
  cache + `index.jsonl` exactly once.

Per-command effect (at N records, k = touched refs):

| Command | Today | v2 |
|---|---|---|
| `upsert` (with refs) | 2 full scans, up to N rewrites, full index rewrite | 1 readdir, 1 record write + k neighbor writes, 1 emit |
| `sidekick` / `continue` | ≥4 full scans | 1 readdir, 2 record writes + k neighbors, 1 emit |
| `return` | 2 full scans | 1 readdir, 1 + k writes, 1 emit |
| `set-status` | 1 full scan + full index rewrite | 1 readdir, 1 write, 1 emit |
| `list` | index read + N stats | cache read, 0 stats |
| ID collision probe | `find()` per candidate (full scans) | hashmap lookup in cache |

## 7. Search v2 (multi-threaded, indexed)

Tier order and ranking contract are preserved exactly (score desc, then `updated`
desc; single-hit short-circuit at every tier; same `layer` labels plus new ones):

| Tier | Today | v2 |
|---|---|---|
| 0 | direct filename/id probe in `find()` | O(1) hashmap on cache (`layer: "fff"`) |
| 1 | substring scan over id+file of all index rows | **postings lookup** over id/file/topic/tag terms (`layer: "fff"`) |
| 2 | `serde_json::to_string` per row + substring | postings over body **signal terms** (`layer: "rg-index-fallback"` kept for contract stability) |
| 3 | semble subprocess | unchanged (`layer: "semble"`), timeout configurable via `RELAY_SEMBLE_TIMEOUT` (default 20 s), `--no-semble` flag to skip |
| 4 | sequential full body re-read | **parallel** body scoring via ScanEngine (`layer: "semble-body-fallback"`) |

- **Signal terms:** at parse time each record contributes its tokenized id, topic,
  tags, section headers, and the ~64 most distinctive body tokens (stopword-filtered,
  same `terms()` tokenizer) to `postings.jsonl`. Query cost becomes
  O(query terms × posting length) instead of O(N × row size).
- Postings are part of index-v2 and freshen incrementally with it.
- If postings are missing/corrupt, tiers 1–2 fall back to today's scan semantics
  (parallelized) — search never errors because of the cache.

## 8. Blazingly fast and lightweight (micro-layer)

- **Drop the `regex` dependency.** Every pattern in the binary is fixed-shape:
  - section headers: a line scan for `## ` prefixes;
  - `filename()`: fixed `conv_DDDDDD_slug` shape check;
  - `terms()`: ASCII alphanumeric run scanner;
  - `count_open()`: case-insensitive substring checks per line.
  Hand parsers remove per-call compilation (§2.4), shrink the release binary, and cut
  cold-start time. (Anything that must remain a regex would use `OnceLock` statics —
  current expectation: none.)
- `sort_by(|a, b| a.path.cmp(&b.path))` instead of key cloning.
- Single `read_to_string` per changed file per command — unchanged files are **never
  opened** (§5.3).
- No new crates anywhere in this spec: hashing is inline FNV-1a, threading is
  `std::thread::scope`. Net dependency delta: **−1** (`regex` removed).
- Targets: release binary ≤ 1.5 MB; process cold start ≤ 5 ms; `--help` never touches
  the filesystem.

## 9. Performance budgets and scalability gates

Budgets live in `tools/profiler/relay_loading_profiler.py` (`BUILTIN_BUDGETS`,
lines 89–105) and are enforced by `tests/test_profiler_gates.py`.

### 9.1 Restore then tighten (100-record corpus, existing gate)

| Operation | Today (median/max ms) | After M1 | After M2 (target) |
|---|---|---|---|
| `cli_upsert` | 140 / 175 | 100 / 125 | 60 / 80 |
| `cli_regen_refs` | 140 / 175 | 100 / 125 | 60 / 80 |
| `cli_rebuild_index` | 140 / 175 | 100 / 125 | 60 / 80 |
| `cli_list` | 100 / 125 | 100 / 125 | 60 / 80 |
| `cli_search` (new op) | — | 100 / 125 | 60 / 80 |
| `cli_context` (new op, §10) | — | — | 100 / 125 |
| `codex_hook` | 50 / 75 | unchanged | unchanged |

The M1 row deliberately reverts commit `6e54eb7`'s budget raise — the raise papered
over the O(N) rescans this spec removes.

### 9.2 New scale profile (10,000-record corpus)

Run via `relay_loading_profiler.py --records 10000 --scale-gates` (nightly / manual,
not per-commit CI):

| Operation | Budget (median/max ms) |
|---|---|
| `cli_list` | 150 / 200 |
| `cli_upsert` | 200 / 250 |
| `cli_rebuild_index` (warm, unchanged archive) | 150 / 200 |
| `cli_rebuild_index --full` (cold, parallel) | 2000 / 3000 |
| `cli_search` (warm postings) | 200 / 250 |

100k records is the design ceiling, documented and load-tested once per release but
not gated: the readdir + cache read dominates, both linear with small constants.

### 9.3 Scaling expectation

Cold full parse throughput must scale ≥ 4× over the sequential baseline on an 8-core
machine (measured by the profiler's cold-rebuild op with `RELAY_SCAN_THREADS=1` vs
default).

## 10. Fidelity v2 — memory relay / context relay

### 10.1 Schema additions (all optional, fully backward compatible)

New structured upsert keys, rendered as sections in the existing canonical order
machinery (inserted after `decisions` in `ORDER`):

- `environment` → `## environment`: harness, platform, cwd, repo root, branch, HEAD
  commit, in-flight PR/issue URLs. Reference-only — the redaction rule from `save.md`
  applies unchanged.
- `artifacts` → `## artifacts`: files/commits/PRs touched, one line of state each
  (`- path/to/file — rewritten parser, tests green`). By reference, never contents.
- `resume.checkpoints`: ordered list of **completed** milestones, so a cold agent
  knows what *not* to redo — the complement of `next_steps`.
- `condensed_transcript` entries gain an optional weight `{"u": …, "a": …, "w": 1..3}`
  (3 = load-bearing exchange). Rendering is unchanged (weights are not printed);
  weights drive budget trimming in `relay context`.

Records without the new keys parse, render, index, and resume exactly as today.
`upsert` continues to fail only on missing `summary`/`dict`/`qa`.

### 10.2 `relay context` — the context relay

```
relay context <id-or-query> [--budget-tokens N] [--json] [--no-refs]
```

One command replaces `show --markdown` + manual ref chasing. It emits a rehydration
pack in the exact reconstruction order from `references/resume.md`:

1. Frontmatter digest (id, topic, status, tags, refs).
2. `## summary`, `## dict` (language first — invariant).
3. `## user-instructions` (standing behavior).
4. `## resume` including checkpoints.
5. `## qa` (open questions flagged).
6. `## decisions`, `## environment`, `## artifacts`.
7. `## condensed-transcript`, weight-trimmed (see below).
8. **Linked context (1 hop):** for each ref, the target's topic + status, and for
   closed branches their `## digest` — the piece agents forget to fetch today.
   Suppressed by `--no-refs`.
9. A closing action line: the `set-status <id> active` command to run after loading.

**Budgeting:** with `--budget-tokens N` (estimate = bytes/4), sections are kept whole
in the order above; the condensed transcript is trimmed lowest-weight-first (then
oldest-first within a weight); linked digests are dropped before any owned section.
The pack always ends with a `truncated: yes/no` marker so the resuming agent knows
whether it saw everything.

`--json` emits the same pack as a structured object for harnesses that inject context
programmatically.

### 10.3 Fidelity lint in `doctor`

`doctor` gains a per-record fidelity score (0–5): non-`(none)` resume goal, ≥1
next-step, ≥1 dict entry, user-instructions present, ≥3 transcript entries. Records
scoring ≤2 produce a warning (`{"file": …, "fidelity": 2, "missing": [...]}`).
`--fix` never fabricates content — fidelity stays report-only, consistent with the
existing "never fabricate to fill sections" rule.

### 10.4 Playbook updates

- `references/resume.md`: resolve target via `search`, then run `relay context <id>`
  (with harness-appropriate budget) instead of `show --markdown` + manual ref reads.
- `references/save.md`: document `environment`, `artifacts`, `resume.checkpoints`,
  transcript weights; capture guidance ("weight 3 = the user would repeat it verbatim
  when resuming").
- `skills/relay/SKILL.md` + verb skills: mention `relay context` in the resume flow.

## 11. Testing and verification

- **Equivalence property (the load-bearing test):** for randomized command sequences
  (upsert/sidekick/continue/return/set-status/import), running with index-v2 enabled
  vs `RELAY_NO_CACHE=1` must produce byte-identical `index.jsonl`, identical record
  files, and identical command stdout. New `tests/test_index_cache.py`.
- **Determinism:** `RELAY_SCAN_THREADS=1` vs default produce identical outputs
  (new cargo unit + pytest case).
- **Contract suites unchanged:** `test_record_schema`, `test_store_layout`,
  `test_cli_e2e_global_root`, `test_branch_primitives`, `test_cli_edge_cases`,
  `test_doctor_resolution_report`, `test_agent_facing_text_contract` must pass without
  modification (they define the compat surface).
- **Updated:** `test_profiler_gates.py` for §9 budgets and new operations.
- **New:** `tests/test_context_pack.py` (ordering, budgeting, ref digests, truncation
  marker), `tests/test_search_tiers.py` (tier short-circuits, layer labels, postings
  fallback), cargo unit tests for the scanner, fingerprint diffing, postings, and each
  hand parser (ported from the regex behavior, including Unicode cases in
  `search_backend.rs` tests).
- **Concurrency hammer:** parallel `upsert` × `list` loops on one root — no torn
  index, no lost records, lock semantics preserved.
- **Scale smoke:** profiler `--records 10000` run in CI nightly.

## 12. Compatibility and invariants checklist

- [x] `~/.relay/convs/*.md` remain the only source of truth; every v2 artifact under
  `.semble/index-v2/` (and `index.jsonl`) is derived and self-healing when deleted.
- [x] `index.jsonl` line format byte-identical to today.
- [x] Record file format, `conv_*` id scheme, filename mapping, section canonical
  order (with `environment`/`artifacts` appended to the optional group), `(none)`
  rendering — unchanged.
- [x] `~/.conversate/` untouched; `import` unchanged (and its nested layouts now
  actually get indexed, fixing §2.4).
- [x] All mutations remain CLI-owned; agents never edit records or indexes directly.
- [x] `.semble/` stays in `.gitignore` (index-v2 lives inside it — records stay
  git-trackable).
- [x] Knowledge base reconciled during implementation:
  `.arca/space/relay-sp/what/{architecture,manifest,flows}.md` and
  `.arca/index/ubiquitous-language.md` gain: **scan engine**, **index cache
  (index-v2)**, **fingerprint**, **postings**, **context pack**, **checkpoint**,
  **transcript weight**.

## 13. Milestones

| # | Scope | Exit criteria |
|---|---|---|
| M1 | Single-pass mutation engine (§6), hand parsers / drop `regex` (§8), parallel full scans (§4), recursive scan fix | All contract suites green; budgets restored to 100/125; `cargo test` green |
| M2 | Index v2 cache + fingerprints + freshness (§5) | Equivalence property green; 10k scale gates green; 60/80 targets met at 100 records |
| M3 | Search v2 postings + tiers + `--no-semble` (§7) | `test_search_tiers` green; `cli_search` gates green |
| M4 | Fidelity v2: schema, `relay context`, doctor lint, playbooks (§10) | `test_context_pack` green; docs/playbooks updated |
| M5 | Profiler scale ops, nightly wiring, `.arca` knowledge-base reconciliation, README | Nightly scale profile in place; drift reconciled |

M1 and M2 deliver the headline speed; M3–M5 can land independently.

## 14. Risks

| Risk | Mitigation |
|---|---|
| mtime unreliability (FAT, restores, clock skew) | size+mtime *and* FNV content hash guard (§5.4); `--full` and `RELAY_NO_CACHE=1` escape hatches |
| Cache/records divergence bug | equivalence property test fuzzes exactly this; any manifest/version anomaly ⇒ full parallel rebuild |
| Windows Defender / AV latency on temp+rename bursts | one emit per command (§6) minimizes rename count; budgets measured on Windows |
| Hand parsers diverge from regex behavior | port regex unit tests verbatim before deleting the dependency (§11) |
| Postings blow up on huge bodies | signal terms capped at ~64 per record; postings are derived and rebuildable |
| `relay context` leaks secrets from new sections | `environment`/`artifacts` are reference-only by rule; save.md redaction rule restated in both playbooks; fidelity lint does not auto-fill |

## 15. Out of scope

- Changing the handoff record format itself (stays human-readable Markdown + TOML).
- Automatic archive sharding (`compact --shard-by-year` noted as future work, §5.6).
- Replacing or embedding the semble semantic tier.
- Any daemon/watcher process — relay stays a short-lived CLI.
