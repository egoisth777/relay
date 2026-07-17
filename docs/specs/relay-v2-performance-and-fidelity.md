# Relay v2 — Performance, Scalability & Recovery Fidelity

- **Status:** Reviewed; implementation quality gates defined in §11
- **Branch:** `relay-perf` (Orca worktree)
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

Normative words in this document use their RFC 2119 meanings. In particular,
**must** clauses are release gates, not aspirations. §11 maps each observable
requirement to a deterministic test; machine-dependent wall-clock targets are kept
separate from per-commit correctness gates.

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
   that makes warm rebuilds O(N metadata + changed-file bytes), with `index.jsonl`
   kept byte-compatible as the derived export.
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
  (work distribution in deterministic chunks of at most 16). No rayon and no new
  runtime dependency.
- **Worker count:** `available_parallelism().map(NonZeroUsize::get).unwrap_or(1)`
  clamped to `1..=8`, overridable with `RELAY_SCAN_THREADS`. The override must be a
  decimal integer in `1..=64`; invalid or zero values are usage errors. Value `1`
  forces sequential execution for equivalence tests.
- **Determinism:** results are collected and sorted by relative path before return, so
  parallel and sequential runs are byte-identical downstream. If multiple files have
  the same frontmatter `id`, strict commands fail with every conflicting relative path
  sorted lexicographically; tolerant commands report/skip all duplicates rather than
  choosing whichever worker finishes first.
- **Recursive snapshot:** fixes the §2.4 import bug — nested records are indexed.
  The walker accepts regular files with the exact lowercase `.md` extension only,
  never follows directory/file symlinks or Windows reparse points, normalizes cache
  paths to `/`, and sorts by normalized relative path. Unix non-UTF-8 relative paths
  are not representable in the public JSON contract: strict commands reject them with
  escaped raw bytes, while `doctor` reports them; lossy strings are forbidden.
  `snapshot()` collects `(relative path, size, mtime_ns)` during that single walk;
  consumers must not enumerate the Relay archive a second time in one command.
- **Consumers:** cold index build, changed-file re-parse (§5), `regen-refs` full sweep,
  body-scoring search fallback, `doctor` record validation.

"One scan" means one recursive enumeration and at most one content open per file that
actually needs parsing. It does not mean O(1): every freshness check remains O(N)
directory metadata. This distinction is part of the profiler's structural telemetry
contract (§9.4), so an implementation cannot satisfy a latency number by silently
skipping freshness.

Every cached `file` is type-checked and must be the unique normalized snapshot path
`convs/<segments>.md` with no root/prefix, backslash, empty/`.`/`..` segment, alternate
spelling, or symlink target. Duplicate cache paths/ids invalidate the cache. A cache
artifact or manifest symlink/reparse point is rejected rather than followed outside the
Plugin installation root.

## 5. Index v2 (better database design)

### 5.1 Why not SQLite (alternatives considered)

- **SQLite / redb / sled:** a real database file would become a second source of
  truth, conflicting with the "index is a derived cache that can be rebuilt" invariant;
  bundling SQLite adds ~700 KB plus C-toolchain friction on Windows for zero benefit at
  ≤100k small text records. Rejected.
- **tantivy** (full-text engine): orders of magnitude heavier than the problem.
  Rejected.
- **Chosen:** a fingerprinted incremental cache in the existing `.semble/` private
  directory. Record rows remain debuggable JSONL; search postings use a small
  random-access binary format (§7) so a short-lived process does not parse the complete
  postings corpus per query. The cache derives the compat `index.jsonl` on change.

### 5.2 Layout

```
~/.relay/
├── index.jsonl              # UNCHANGED byte format — derived export, still greppable
└── .semble/
    ├── write.lock           # existing exclusive mutation lock (unchanged)
    ├── txn.pending          # crash-recovery journal; absent outside an interrupted txn
    └── index-v2/
        ├── manifest.json    # atomic commit point; points at one complete generation
        ├── records.N.jsonl       # complete sorted row snapshot for generation N
        ├── postings.base.B.bin   # compact random-access base (§7)
        └── postings.delta.N.bin  # bounded add/remove overlay; zero or more (§7)
```

Each `records.N.jsonl` row =
`{id, topic, status, tags, refs, created, updated, file, open, size, mtime_ns, fp}`
where `fp` is an FNV-1a 64-bit hash of file content (FNV-1a is ~10 lines inline —
still zero new dependencies). Rows are serialized one per line in normalized `file`
ascending order with lexicographically sorted keys and exactly one trailing newline per
row; the file is empty when `record_count` is zero.

The manifest schema is
`{version: 2, generation: N, record_count: C, records_file, records_hash,
postings_base_generation: B, postings_base, postings_base_directory_hash,
postings_deltas: [{generation: D,file,directory_hash}], compat_hash}`. The records filename
contains `N`, the base filename contains `B`, and every delta filename contains its `D`;
delta generations are unique, ascending, greater than `B`, and no greater than `N`.
Manifest hashes are 16-character lowercase hexadecimal FNV-1a-64 values over the exact
published bytes (or, for a postings directory hash, over its exact directory bytes).
JSONL/compat hashes and posting-directory/block checksums catch valid-JSON bit flips,
stale substitutions, and block corruption; they are integrity checks, not a security
boundary. Generation filenames, rather than several in-place renames, make publication
transactional: write and `sync_all` the records snapshot and any posting base/delta,
atomically replace `index.jsonl`, then atomically replace `manifest.json` **last**.
Readers trust only files named by a valid manifest.
Unreferenced generation files left by a crash are ignored and pruned after a later
successful commit. At least the currently published generation is retained.
Manifest filenames must be basenames matching their declared generation/base exactly;
absolute paths, separators, `..`, alternate extensions, and generation mismatches
invalidate the cache without being opened.

All `atomic`/`durable` writes in this spec use one `atomic_replace_durable` primitive:
same-directory unpredictable `create_new` temp; write + flush + file `sync_all`; atomically
replace an existing destination without a delete window (POSIX `rename`, Windows
`ReplaceFileW` or `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)` as appropriate); then
sync the parent directory where the platform supports it. Directory-sync unsupported
errors are classified/documented, not confused with a successful barrier. Deleting a
journal or obsolete generation is followed by the same parent-directory durability
barrier. Plain `fs::rename` portability assumptions and delete-then-rename are forbidden.

### 5.3 Freshness algorithm (archive-consuming commands)

1. `ScanEngine::snapshot()` — one recursive readdir, the **only** filesystem
   enumeration in the command.
2. Load the manifest and its named records generation. A missing, malformed, wrong-
   version, hash/count-mismatched, or malformed records generation is a cache miss and
   triggers a full parallel rebuild. A malformed posting directory/block or overlay
   chain falls back to exact cached-row scans and rebuilds postings from valid rows; it
   does not discard record rows.
3. Diff snapshot against cached rows by normalized relative path and `(size, mtime_ns)`:
   - match → trust cached row (no file read at all);
   - mismatch or new file → parse in parallel (§4), recompute row + `fp`;
   - cached row with no file → drop (tombstone-free: the snapshot is authoritative).
4. Verify `index.jsonl` against `compat_hash`. If rows changed, publish a complete
   `records.N.jsonl`, one bounded posting delta (or a compacted base), and the compat
   export with the manifest-last protocol in §5.2. If only `index.jsonl` is
   missing/corrupt/stale, re-emit it from cached compat rows without opening record
   files or bumping the cache generation.

Consequences:

- "Rebuild" becomes **O(N metadata + changed-file bytes)**. Warm `rebuild-index` on
  an unchanged 10k-record archive is readdir/cache-read-bound.
- `valid_index()`'s per-record `is_file()` storm is deleted — validity comes from the
  snapshot taken once.
- Corruption is fail-safe: malformed record-cache state causes a full source rebuild;
  malformed postings fall back to exact cached-row scans and rebuild; malformed compat
  `index.jsonl` re-emits from cached rows. Tolerant reads must never expose a partial
  prefix of a corrupt cache.
- Deleting `.semble/index-v2/` (or `index.jsonl`) self-heals on the next command —
  the derived-cache invariant holds for the whole v2 layer.

Commands that provably do not consume the archive (`--help`, usage errors, and the
hook fast path) must not initialize the Plugin installation root or snapshot it.

Source errors never authorize stale cache data. `rebuild-index`/`--full`,
`regen-refs`, and mutators are strict for every record they validate/touch and publish
nothing on error. `show` and the owned target of `context` are strict and reject
ambiguous/duplicate/malformed targets. `list`/`search` are tolerant: a changed malformed
record loses its old cached row, and every file in a duplicate-id set is excluded; they
do not choose a winner or print warnings that would break the array contract. `doctor`
is exhaustive/tolerant and reports every normalized parse/duplicate path. Malformed or
duplicate linked context targets become sorted optional warnings. `import` preserves
arbitrary source bytes, indexes only valid unique records, and leaves diagnosis to
`doctor`; a later strict rebuild fails until malformed/duplicate source records are
repaired.

### 5.4 Fingerprint edge cases

- CLI-owned writes always carry their new row/fingerprint into the same transaction, so
  they never rely on mtime invalidation. For out-of-band edits, size/mtime is the fast
  invalidator. Zero or regressed mtimes force a content-hash comparison. Coarse but
  nonzero stable mtimes use the same fast path and therefore widen the documented blind
  window for out-of-band same-size edits; they do not silently turn every warm read into
  O(N) content I/O. A deliberately same-size edit with an identical observed mtime is
  outside automatic detection; `rebuild-index --full`, `doctor --fix`, or
  `RELAY_NO_CACHE=1` is the explicit repair path. This limitation must be documented,
  not presented as collision-proof cache validation.
- `rebuild-index --full` ignores the cache entirely (parallel cold build).
- `doctor --fix` likewise performs a full source parse before cache/index repair; it is
  a valid same-stat repair path, not a warm metadata-only check.
- `RELAY_NO_CACHE=1` disables index-v2 reads and writes for the invocation — the
  escape hatch and reference behavior for equivalence tests (§11). It still emits the
  required compat `index.jsonl` for mutating/rebuild commands and never deletes a valid
  cache generation.

### 5.5 Concurrency

- Mutating commands hold the exclusive `write.lock` across recovery, snapshot, record
  commit, and cache publication. A second mutator waits; it never computes from a stale
  pre-lock snapshot.
- Read-only archive commands open the existing lock file without creating it and take a
  shared lock for the snapshot/read. If no lock exists, a read uses an optimistic
  lockless protocol: snapshot/compute, take a second snapshot, and re-check lock
  absence; it may return only if both normalized snapshots are identical and the lock
  is still absent, otherwise it retries under the now-existing shared lock. This covers
  both new empty roots and copied pre-lock archives, prevents the first writer race, and
  preserves the existing "reads do not create `write.lock`" contract.
- A read that discovers stale cache state computes correct output in memory. It may
  upgrade by releasing the shared lock and attempting the exclusive lock, but must
  re-snapshot after upgrade before persistence. On contention it skips persistence;
  output remains based on the source snapshot it actually parsed.
- Generation-named artifacts plus manifest-last publication prevent mixed cache
  generations. `index.jsonl` is a compatibility export, not a commit marker.

### 5.6 Archive layout at scale

The flat `convs/` directory remains the default. Recursive scanning supports nested
layouts already produced by `import`; the `file` field in `index.jsonl` carries the
normalized relative path. Users and agents must not manually move live handoff records
because mutations remain CLI-owned. A future `relay compact --shard-by-year` may add a
safe sharding mutation; it is not part of this spec.

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
  record and the union of its old and new forward-ref *targets* need reconsideration —
  O(old degree + new degree), not O(N). The
  forward/reverse pairs (`spawned-from`/`spawned-to`, `continued-from`/`continued-as`,
  `informed-by`/`informed`) are computed from cache rows, and only affected neighbor
  records are rewritten. `regen-refs` remains the full repair sweep (now parallel) for
  `doctor --fix` and manual reconciliation.
- **One emit.** After journaling, `txn.commit()` writes dirty records (atomic each),
  then publishes cache + `index.jsonl` exactly once.
- **Recoverable commit.** Before the first record rename, `txn.commit()` writes and
  durably publishes `.semble/txn.pending` with a versioned transaction id and the
  complete ordered after-images for every dirty record. The binary journal encodes
  normalized relative path length, arbitrary byte length/content, and checksum, so
  `import` can preserve malformed TOML or non-UTF-8 legacy bytes. It then durably writes
  records in normalized-path order, publishes the cache generation, durably unlinks the
  journal, and syncs `.semble/`. On the next archive command, a journal is replayed under
  the exclusive lock before any snapshot. Replay is idempotent; it either completes all
  after-images and cache publication or returns an error while retaining the journal.
  No command may silently ignore or delete an unreadable journal.
- **Recovery authority.** `txn.pending` is authoritative in-flight recovery state once
  any after-image may have landed. Deleting `.semble/index-v2/` is safe; deleting or
  treating `.semble/txn.pending` as disposable can lose a requested mutation. Installer
  update/repair, `doctor`, pruning, and cache cleanup must preserve it. It ceases to be
  authoritative only after all record/cache barriers and its durable unlink complete.
- **Failure semantics.** Validation, collision, and serialization errors happen before
  the journal is published and therefore write nothing. I/O failure after publication
  may leave an interrupted transaction, but the next command completes the requested
  state; it must not expose a permanently half-reconciled ref graph.

"Incremental" applies to source-record parsing/writes and posting deltas, not to every
byte emitted. Because compatibility requires a complete sorted `index.jsonl`, and this
design keeps a complete `records.N.jsonl` snapshot, a successful mutation still
serializes/writes O(N) compat + record-cache bytes. Its end-to-end cost is
`O(N metadata + changed-file bytes + degree record writes + N cache/export bytes)`;
the spec must not label the whole mutation O(degree). §9 gates bytes and slopes as well
as publication counts so one enormous "emit" cannot hide write amplification.

Per-command effect (at N records, k = touched refs):

| Command | Today | v2 |
|---|---|---|
| `upsert` (with refs) | 2 full scans, up to N rewrites, full index rewrite | 1 readdir, 1 record write + k neighbor writes, 1 emit |
| `sidekick` / `continue` | ≥4 full scans | 1 readdir, 2 record writes + k neighbors, 1 emit |
| `return` | 2 full scans | 1 readdir, 1 + k writes, 1 emit |
| `set-status` | 1 full scan + full index rewrite | 1 readdir, 1 write, 1 emit |
| `import` | recursive copy + full rebuild | stage/classify all source bytes, 1 journaled batch copy, 1 emit; collisions remain report-only |
| `list` | index read + N stats | 1 recursive metadata snapshot, 0 unchanged record opens |
| ID collision probe | `find()` per candidate (full scans) | hashmap lookup in cache |

`RELAY_TEST_CRASH_AT=after_journal|after_record:N|after_records_cache|after_postings|
after_compat|after_manifest|after_journal_unlink` is a debug-build-only abrupt-exit hook
used by §11 when `RELAY_TEST_MODE=1`; release builds ignore it. The crash matrix restarts
after every durability boundary and accepts only the old complete state (before journal
publication) or the fully rolled-forward state, never a missing target or half graph.

`import` stages and classifies every source entry before journal publication, preserves
arbitrary bytes and the read-only legacy source, and sorts `copied`/`unchanged`/
`collisions` by normalized relative path. A collision remains a reported non-write, not
a transaction-wide validation error. Recovery completes every staged copy without
re-reading or mutating `~/.conversate/`.

## 7. Search v2 (multi-threaded, indexed)

Tier order and ranking are compatibility contracts. In tiers 1–2, score is the number
of normalized query terms contained as substrings in the tier's haystack, ordered by
score descending then `updated` descending, with the existing single-hit short-circuit
and layer labels. Tier 3 preserves semble order. Tier 4 preserves today's normalized
archive-path order (it currently does not score-sort); changing that order requires a
separate compatibility decision. Cache-only fields (`size`, `mtime_ns`, `fp`) must
never enter a searchable haystack.

| Tier | Today | v2 |
|---|---|---|
| 0 | no separate search tier (`find()` has a direct probe) | exact-id O(1) cache probe, equivalent to a tier-1 single hit (`layer: "fff"`) |
| 1 | substring scan over id+file of all index rows | trigram candidates over exactly id+file, then exact verification (`layer: "fff"`) |
| 2 | `serde_json::to_string` per compat row + substring | trigram candidates over the canonical compat-row JSON, then exact verification (`layer: "rg-index-fallback"`) |
| 3 | semble subprocess | unchanged (`layer: "semble"`), timeout configurable via `RELAY_SEMBLE_TIMEOUT` (default 20 s), `--no-semble` flag to skip |
| 4 | sequential full body re-read | **parallel** body scoring via ScanEngine (`layer: "semble-body-fallback"`) |

- **Random-access postings:** each distinct lower-cased UTF-8 byte trigram in a tier
  haystack maps to sorted record ids. A posting file has a versioned header, a sorted
  fixed-width directory `(tier,gram,offset,length,block_checksum)`, and packed record-id
  blocks. The reader binary-searches directory entries with `seek` and reads only the
  requested blocks; it must not deserialize the whole postings corpus. The header
  authenticates the directory bytes, and each fetched block is checked before use.
- **Candidate algebra:** for each query term of at least three bytes, intersect that
  term's trigram lists; then **union candidates across query terms** before running the
  exact scorer. This preserves today's “match any term, score every matching term”
  behavior. Terms shorter than three bytes and terms whose lower-casing is not safely
  representable use an in-memory scan of compat rows. This fallback is O(N) CPU but
  opens no record files and preserves Unicode/current tokenizer behavior.
- Body text is deliberately absent from tiers 1–2 postings. Indexing a subset of body
  "signal terms" would create false negatives and would change which tier wins.
- Posting deltas contain sorted add/remove record-id blocks for changed rows. A manifest
  references at most eight deltas; publish compacts to a new base before adding a ninth
  delta or when total delta bytes exceed 25% of base bytes. Queries apply deltas in
  manifest order. Missing/corrupt directories or any requested block fall back to exact
  in-memory scans and schedule repair; search never fails or changes results because a
  derived posting is absent.
- `--no-semble` guarantees that no semble availability probe or subprocess occurs;
  tier 4 follows tier 2 directly. `RELAY_SEMBLE_TIMEOUT` accepts a positive finite
  duration in seconds and invalid values are usage errors.

For tiers 1–2 the total comparator is `(score desc, updated desc, id asc)`; list uses
`(updated desc, id desc)`, matching the current stable-sort/reverse tie behavior.
`--limit 0` returns `[]` before any tier or subprocess (a deliberate correction of the
current single-hit shortcut leak); positive limits apply after tier ranking. The compat
export keeps today's exact field set, lexicographic key serialization, id-sorted lines,
UTF-8 escaping, and one trailing newline per row; golden tests lock those bytes.

Semble output first matches a normalized full archive-relative or absolute path.
Basename-only output is accepted only when that basename is globally unique; duplicate
basenames in nested directories never broaden one semantic hit into several records.
Equal Semble positions retain normalized path order, and both `/` and `\` output are
normalized without lossy substring matching.

## 8. Blazingly fast and lightweight (micro-layer)

- **Drop the `regex` dependency.** Every pattern in the binary is fixed-shape:
  - section headers: a line scan for `##` plus one-or-more whitespace, preserving the
    current trimmed, non-greedy capture behavior;
  - `filename()`: fixed `conv_DDDDDD_slug` slicing with the same dated filename mapping;
  - `terms()`: ASCII alphanumeric run scanner after Unicode lower-casing, with the same
    stop-word filter;
  - `count_open()`: case-insensitive, current Unicode-word-boundary checks for `q` + optional
    whitespace + `(open)`, and for `open:`, per line.
  Hand parsers remove per-call compilation (§2.4), shrink the release binary, and cut
  cold-start time. (Anything that must remain a regex would use `OnceLock` statics —
  current expectation: none.)
- `sort_by(|a, b| a.path.cmp(&b.path))` instead of key cloning.
- At most one `read_to_string` per parsed file per command. With a valid enabled cache,
  metadata-matching unchanged files are not opened; full/no-cache modes intentionally
  parse every source record (§5.3–5.4).
- No new crates anywhere in this spec: hashing is inline FNV-1a, threading is
  `std::thread::scope`. Net dependency delta: **−1** (`regex` removed).
- The release binary must not exceed the same-target base-commit artifact by more than
  2%, and removing `regex` should make the expected delta negative. Cross-target byte
  sizes are not comparable. `cli_help` retains its §9 budget and `--help` must perform
  zero Plugin-installation-root or Relay-archive filesystem operations. The previous
  absolute claims (1.5 MB and 5 ms) are not release gates because the current Windows
  baseline is about 2.9 MB and process-spawn latency is runner-dependent.

## 9. Performance budgets and scalability gates

Budgets live in versioned `tools/profiler/runtime_budgets.m1.json` and
`runtime_budgets.v2.json` profiles with matching built-in defaults in
`relay_loading_profiler.py`; `tests/test_profiler_gates.py` rejects drift between them.
CI passes `--budget-profile m1|v2` explicitly (M1 selects `m1`, M2+ selects `v2`), or
passes an explicit `--budget-file` for a one-off equivalent profile. A gate run with
neither is a usage error. Absolute timing gates run on the pinned
Windows x64 release-profile runner.
Other platforms emit the same report and enforce structural/correctness gates, but do
not compare their timings to Windows numbers.

Gate mode requires an explicit `--binary` built by
`cargo build --release --locked --target <pinned-triple>`; debug binaries are rejected.
Every report records commit, binary path/size/SHA-256, Cargo.lock SHA-256, rustc/Cargo
versions, target triple, profile/flags, logical CPUs, RAM, OS image id/build, storage
volume/filesystem, CI runner image, power policy, AV policy, fixture-manifest SHA-256,
and base-commit-report SHA-256. The pinned runner
definition and a base-commit report are committed profiler fixtures. The §8 size-ratio
job builds base and current in one job with identical toolchain/flags and archives both
artifact manifests.

### 9.1 Restore then tighten (100-record corpus, existing gate)

| Operation | Today (median/max ms) | After M1 | After M2 (target) |
|---|---|---|---|
| `cli_upsert` | 140 / 175 | 100 / 125 | 60 / 80 |
| `cli_regen_refs` | 140 / 175 | 100 / 125 | 60 / 80 |
| `cli_rebuild_index` | 140 / 175 | 100 / 125 | 60 / 80 |
| `cli_rebuild_index_full` | — | 300 / 400 | 250 / 350 |
| `cli_list` | 100 / 125 | 100 / 125 | 60 / 80 |
| `cli_search` (new op) | — | 100 / 125 | 60 / 80 |
| `cli_context` (new op, §10) | — | — | 100 / 125 |
| `codex_hook` | 50 / 75 | unchanged | unchanged |

The M1 row deliberately reverts commit `6e54eb7`'s budget raise — the raise papered
over the O(N) rescans this spec removes. Final M2 numbers become the default only after
the pinned runner records three consecutive green runs and a reviewed promotion record;
milestone branches continue selecting the M1 profile until that change lands.

The 100-record corpus is deterministic: 100 valid records in four exact UTF-8 byte-size
buckets spanning 2–4 KiB, fixed timestamps, a 20-value tag vocabulary with three tags
per record, a sparse ref graph with a recorded edge/degree histogram (maximum out-degree
3), and no malformed files. A committed generator/fixture manifest records version,
seed, archive SHA-256, recursive file/byte counts, size/tag/edge histograms, flat/nested
layout, query strings, expected hit ids/layers/scores, and expected output bytes. Each
promotion run gives each subprocess operation two untimed warmups and 21 measured runs
from a fresh cloned identical pre-state; clone/setup time is excluded, and every clone id and pre-state
hash/generation is reported. Each subprocess operation retains an `attempts` array in
execution order; every entry has `phase`, phase-local `ordinal`, `clone_id`,
`pre_state_sha256`, `pre_generation`, `elapsed_ms`, `returncode`, and `timed_out`.
`samples_ms` is derived only from successful measured attempts, but failed/timed-out
attempts remain in the report and fail the gate. `cli_upsert` updates one existing unreferenced record;
a separate scale mutation retargets a degree-3 ref;
`cli_regen_refs` uses the stable sparse graph; warm rebuild/list/search/context start
with a valid v2 generation; search has 10 matching postings and context uses a record
with 12 transcript exchanges plus two one-hop refs. Median and max are over successful
measured runs only, every attempted run/raw sample is retained, and any
warmup/subprocess failure fails the gate. Gate reruns append evidence rather than
replacing a failed attempt. A run set with median absolute deviation / median >15% or
with the interleaved `cli_help` control drifting >15% between first and last quartiles
is environmental noise and cannot promote a budget; it is reported as an invalid run,
not silently retried into a pass. An explicit `--runs` override is allowed for profiler
integration tests, but a report with other than 21 measured attempts per selected
subprocess operation is marked `promotion_eligible: false` even when its correctness
and requested timing gates pass.

### 9.2 New scale profile (10,000-record corpus)

Run via `relay_loading_profiler.py --records 10000 --scale-gates` nightly and before a
release, not in ordinary per-commit CI. It uses the same fixed schema, record-size
distribution, ref degree, query selectivity, clone-per-sample rule, release binary,
two warmups, and 21 runs as §9.1:

| Operation | Budget (median/max ms) |
|---|---|
| `cli_list` | 150 / 200 |
| `cli_upsert` | 200 / 250 |
| `cli_rebuild_index` (warm, unchanged archive) | 150 / 200 |
| `cli_rebuild_index --full` (cold, parallel) | 2000 / 3000 |
| `cli_search` (warm postings) | 200 / 250 |
| `cli_search --no-semble` (forced full body fallback) | 1500 / 2200 |
| `cli_context` (two linked records) | 250 / 325 |
| `cli_regen_refs` (stable graph) | 1200 / 1800 |

The report must retain corpus byte count, record count, query hit count, configured/actual
workers, cache generation before/after, per-operation structural I/O counters, artifact
bytes, peak private working set/RSS, and peak open handles/file descriptors so a tiny or
stale corpus or oversized cache cannot game a latency gate. Both flat and 10%-nested
layouts run nightly.

### 9.3 Scaling expectation

On the pinned runner with at least eight logical CPUs, 21 paired samples alternate the
1-thread/8-thread order by a committed seed. The median cold full rebuild with
`RELAY_SCAN_THREADS=8` must be no slower than 85% of its paired 1-thread control, using
a fresh cloned 10k corpus for every sample; both arms must also meet absolute budgets,
so slowing the control cannot pass the ratio. The former
4× claim is retained as an optimization target, not a gate: small-file scans are often
storage/AV-bound, so requiring 4× would measure the host more than the implementation.
Regardless of timing, tests must prove configured parallel execution and byte-identical
output for thread counts 1 and 8.

A 100k-record release smoke runs full rebuild, warm list, selective search, and forced
body fallback. Results must be correct with peak RSS ≤768 MiB, derived cache + compat
export ≤512 MiB, peak open handles/file descriptors ≤64, and no more than eight scan
workers. Timing at 1k/10k/100k must stay within a committed scaling envelope; promotion
requires an explicit reviewed baseline update rather than treating 100k as report-only.

### 9.4 Deterministic structural telemetry

All builds accept `RELAY_TEST_TRACE_IO=<path>` only when `RELAY_TEST_MODE=1`; the tracer
is absent from the hot path when unset. Debug builds additionally accept the RFC 3339
clock override `RELAY_TEST_NOW`. The CLI appends JSONL events for
`snapshot`, `snapshot_end` (files/directories seen), `scan_start` (configured workers),
`worker_start`/`worker_end`, `scan_end` (workers started and maximum concurrently
active), `record_open`/`record_write` (worker id and bytes), `cache_read`/`cache_write`
(artifact class and bytes), `journal_publish`, `cache_publish`, and
`compat_index_publish`, plus `lock_wait`, `lock_acquire`, and `lock_release` with lock
mode. Crash and lock-barrier fault injection remain debug-only. The profiler runs one
separate traced clone per structurally gated operation, reports it as `structural_run`,
and excludes it from warmups and timing samples; trace-file I/O therefore cannot improve
or degrade a latency result. Production invocations without the two test variables do
not initialize tracing. §11 uses this trace
to enforce one enumeration, zero unchanged-file opens on warm reads, targeted neighbor
writes, one journal/cache/index publication per mutation, and no filesystem activity
for `--help`. These are correctness/complexity gates and run on every platform.
Every event has `{"event": NAME}`; record events add a normalized root-relative
`path`, and publish events add `generation`. All archive/cache I/O must pass through the
instrumented store layer: event counts are cross-checked against an independently
generated fixture manifest and byte counts, so emitting labels without doing the work
cannot pass. Scan workers synchronize their startup before consuming a multi-chunk cold
scan, making `max_active >= 2` deterministic when eight workers and at least two chunks
are requested. Paths outside the temporary Plugin installation root are never recorded.

For deterministic lock tests only, a debug build with `RELAY_TEST_MODE=1` honors
`RELAY_TEST_BARRIER_AFTER_LOCK=<base>`: after acquiring the command's shared/exclusive
store lock it creates `<base>.<pid>.ready` and waits (maximum 10 seconds) for
`<base>.release`. Release builds ignore it. The gate starts two readers at the barrier,
then a writer, proving shared-reader overlap and bounded writer progress after release
without relying on scheduler timing.

## 10. Fidelity v2 — memory relay / context relay

### 10.1 Schema additions (all optional, fully backward compatible)

New structured upsert keys, rendered as sections in the existing canonical order
machinery (`environment` and `artifacts` are inserted after `decisions` in `ORDER`):

- `environment` → `## environment`: harness, platform, cwd, repo root, branch, HEAD
  commit, in-flight PR/issue URLs. Reference-only — the redaction rule from `save.md`
  applies unchanged.
- `artifacts` → `## artifacts`: files/commits/PRs touched, one line of state each
  (`- path/to/file — rewritten parser, tests green`). By reference, never contents.
- `resume.checkpoints`: ordered list of **completed** milestones, so a cold agent
  knows what *not* to redo — the complement of `next_steps`. It renders as the
  `checkpoints` group inside `## resume`, before `next-steps`.
- `condensed_transcript` entries gain an optional weight `{"u": …, "a": …, "w": 1..3}`
  (3 = load-bearing exchange). `w` must be an integer in `1..=3`; omitted weights
  default to 1 and invalid values reject the upsert before any write. Each structured
  exchange is preceded in the stored Markdown by the hidden metadata line
  `<!-- relay:transcript-weight=N -->`, and the frontmatter gains additive
  `relay_schema = 2`. Markdown rendering is visually unchanged, the
  weight survives cache deletion/branch copying, and `relay context` strips the comment
  from emitted content. Raw legacy transcript text without a valid immediately
  preceding marker has weight 1. In pre-v2 records (`relay_schema` absent), even an
  exact marker-looking line is literal content. In schema 2, a marker is metadata only
  when immediately followed by `- U:` or `- A:`; invalid/orphan markers remain literal
  weight-1 content. A U line and its immediately following A line form one exchange;
  indented continuation lines belong to that side until the next top-level bullet or
  marker; orphan U or A is one exchange. Stored order is age order, oldest first. This
  single parser is shared by context and doctor.

Records without the new keys parse, render, index, and resume exactly as today.
The three existing mandatory sections remain `summary`/`dict`/`qa`; the new structured
keys add only type/range validation. `environment` and `artifacts` accept a string or a
list of strings and render `(none)` only when explicitly present and empty; they are
otherwise omitted for legacy byte compatibility.

`sidekick` and `continue` carry the parent's dict, resume/checkpoints,
user-instructions, environment, and weighted transcript markers through the child
record; artifacts are deliberately not inherited because they describe files touched
by one execution branch. Generated child-specific summary/goal rules remain in force.
Cache deletion/rebuild and branch round trips must not change marker bytes or weights.

### 10.2 `relay context` — the context relay

```
relay context <id-or-query> [--budget-tokens N] [--json] [--no-refs]
```

One command replaces `show --markdown` + manual ref chasing. Target resolution uses the
same exact/ambiguous/not-found contract as `show`. It emits a rehydration pack in this
exact reconstruction order from `references/resume.md`:

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
9. A closing structured action-argv line that includes the resolved Plugin installation
   root, never an ambiguous shell string.

Linked refs are de-duplicated by `(id, rel)`, ordered by target id then relation, limited
to one hop, and never recursively expanded. Missing/malformed targets produce a
deterministic linked warning rather than failing the owned record. A closed linked
record includes its `## digest` when present; other linked bodies are not copied. A
linked entry and any warning about it form one optional indivisible unit in the same
display/drop position.
Warnings use `{id, rel, error}` with `error` in `missing|malformed|duplicate`, sorted by
id/relation/error; text renders the same fields on one line.

**Budgeting:** `--budget-tokens N` accepts an integer `N > 0`. Estimated tokens are
`ceil(UTF-8 rendered-text bytes / 4)`, and successful text output must be at most `4*N`
bytes. JSON uses the identical logical selection and reports the equivalent text
estimate, but JSON transport overhead is not byte-capped.
Frontmatter, `summary`, `dict`, `user-instructions`, `resume`, `qa`, the closing action,
and the final marker are the mandatory envelope. If that envelope alone exceeds N,
the command exits 2, prints the required minimum estimate, emits no partial pack, and
does not mutate the record or its status.

Optional units are indivisible and removed in this deterministic order until the cap
fits: linked entries in reverse display order; transcript exchanges by weight ascending
then age oldest-first; then whole `artifacts`, `environment`, and `decisions` sections
in that order. Display order never changes. The pack always ends with exactly one
`truncated: yes|no` marker; it is `yes` iff any optional unit was omitted. No budget
means no trimming.

`--json` emits a versioned representation from which text output is rendered:

```json
{
  "schema_version": 1,
  "id": "conv_...",
  "plugin_installation_root": "/resolved/.relay",
  "budget_tokens": null,
  "estimated_tokens": 123,
  "minimum_tokens": 80,
  "truncated": false,
  "frontmatter": {"id": "conv_...", "topic": "...", "status": "active", "tags": [], "refs": []},
  "sections": [{"name": "summary", "markdown": "..."}],
  "linked": [{"id": "conv_...", "rel": "spawned-to", "topic": "...", "status": "closed", "digest": "..."}],
  "warnings": [],
  "action_argv": ["/resolved/.relay/bin/relay", "set-status", "conv_...", "active", "--relay-root", "/resolved/.relay"]
}
```

`sections` uses the reconstruction order above and contains no transcript-weight
markers. `estimated_tokens` describes the equivalent text pack including its final
marker. `minimum_tokens` is the mandatory-envelope estimate and is stable between text
and JSON modes. Text renders `action_argv` as a JSON array after `next action argv:` so
paths with spaces remain unambiguous and no platform-specific shell quoting is implied.
The first argv element is `current_exe()` (normally the installed binary under the
Plugin installation root), not a PATH-dependent alias.

### 10.3 Fidelity lint in `doctor`

`doctor` gains a per-record fidelity score (0–5): non-`(none)` resume goal, ≥1
next-step, ≥1 dict entry, user-instructions present, ≥3 transcript exchanges. Records
scoring ≤2 produce a warning (`{"file": …, "fidelity": 2, "missing": [...]}`).
`--fix` never fabricates content — fidelity stays report-only, consistent with the
existing "never fabricate to fill sections" rule.

A dict entry is a nonblank Markdown list line in `## dict`; a next-step is an item
under the rendered `next-steps` group; user-instructions means at least one non-
`(none)` line. A structured weight marker plus its following U/A block counts as one
transcript exchange; legacy `- U:` with an optional immediately following `- A:` also
counts as one, and any other nonblank legacy list line counts as one. `missing` uses the
fixed key order `resume-goal,next-step,dict-entry,user-instructions,transcript-entries`
rather than alphabetical order, so diagnostics are stable and actionable.

### 10.4 Playbook updates

- `references/resume.md`: resolve target via `search`, then run `relay context <id>`
  (with harness-appropriate budget) instead of `show --markdown` + manual ref reads.
- `references/save.md`: document `environment`, `artifacts`, `resume.checkpoints`,
  transcript weights; capture guidance ("weight 3 = the user would repeat it verbatim
  when resuming").
- `skills/relay/SKILL.md` + verb skills: mention `relay context` in the resume flow.

## 11. Testing and verification

The new tests are forward quality gates: they are expected to fail against base commit
`04ef7b6` because v2 is not implemented. They must not be marked `xfail` or skipped
merely to keep a milestone green. Milestone CI selects the gates listed in §13; final
release CI runs all of them.

| Contract | Deterministic gate |
|---|---|
| Recursive, symlink-safe, duplicate-deterministic scan (§4) | `tests/test_index_cache.py`; scanner cargo unit tests |
| Cached vs `RELAY_NO_CACHE=1` semantic/byte equivalence (§5.4) | cloned roots and a fixed-timestamp sequence of upsert/sidekick/continue/return/set-status/import in `test_index_cache.py` |
| Thread-count determinism and invalid override errors (§4) | `RELAY_SCAN_THREADS=1` vs `8` byte comparison plus parser tests |
| Metadata-resolution policy (§5.4) | `test_index_cache.py` proves the documented same-stat blind window plus no-cache/`--full` repair; injected `FileStat` unit cases cover equal/coarse, zero, regressed, and size-changed metadata and the specified hash/open behavior |
| Manifest-last recovery and cache corruption matrix (§5.2–5.3) | delete/corrupt manifest, records generation, postings generation, and `index.jsonl` independently; assert full output, repaired state, and no partial-prefix reads |
| Warm-read/single-pass complexity (§4, §6, §9.4) | debug trace asserts one snapshot, zero unchanged record opens/writes, targeted neighbor writes, and one publication |
| Transaction crash recovery (§6) | fault after the first of multiple record writes; next command replays journal and yields a fully reconciled ref graph |
| Search tier/ranking compatibility (§7) | `tests/test_search_tiers.py`: exact id/file tier, compat-row tier, per-term-intersection/cross-term-union, tier-4 path order, short/Unicode query fallback, corrupt base/delta fallback, selective posting-read bytes, `--no-semble`, and cached/no-cache byte equality |
| Fidelity schema and durable weights (§10.1) | `tests/test_context_pack.py`: canonical order, checkpoints, hidden marker persistence, legacy default weight, and pre-write rejection of invalid weights |
| Context pack order/link/budget contract (§10.2) | text and JSON schema tests, one-hop closed digest, missing-ref warning, exact byte cap, deterministic drop order, minimum-budget error, and no status mutation |
| Fidelity doctor lint (§10.3) | score/missing fields are stable; `--fix` never fabricates content |
| Profiler workload/budgets/coverage (§9) | updated `tests/test_profiler_gates.py` requires versioned budget profiles, release-binary provenance, committed fixture manifest/hash, clone-per-sample pre-state, structural/byte/resource counters, paired sample order, `cli_search`/body fallback/context/regen/cold+warm rebuild, and 10k/100k profiles |
| Dependency/parser compatibility (§8) | `regex` absent from direct dependencies; cargo unit tests port all old regex cases, including non-ASCII and malformed boundaries |
| Concurrent readers/writers (§5.5) | bounded parallel upsert/list/search loop: every subprocess succeeds, JSON always parses, no record is lost, and published generation files match the manifest |
| Source/runtime separation (§12) | no-home invocation errors without cwd writes; an isolated copied binary's `doctor --fix` cannot discover compile-time repo `scripts/install.py`; emitted context action targets only its resolved custom root |

The equivalence sequence compares only source and public artifacts: recursively sorted
record relative paths + bytes, byte-identical `index.jsonl`, normalized command exit
codes/stdout/stderr, and final list/search/show/context JSON. It deliberately excludes
`.semble/index-v2/` and `write.lock`, which differ by cache mode. Fixed ids and
timestamps remove clock/slug nondeterminism; a fixed PRNG seed is logged on failure.

Existing contract suites (`test_record_schema`, `test_store_layout`,
`test_cli_e2e_global_root`, `test_branch_primitives`, `test_cli_edge_cases`,
`test_doctor_resolution_report`, `test_agent_facing_text_contract`) remain mandatory.
Changes required solely to recognize additive v2 fields/commands are allowed, but
their pre-v2 behavioral assertions may not be weakened. `cargo test` and
`python -m pytest` are the final local verification commands. The 10k profile is a
nightly/release job; ordinary unit tests use small corpora and structural telemetry.

## 12. Compatibility and invariants checklist

- [x] Markdown records under `~/.relay/convs/` (including nested paths) remain the only source of truth; every v2 artifact under
  `.semble/index-v2/` (and `index.jsonl`) is derived and self-healing when deleted.
- [x] `index.jsonl` line format byte-identical to today.
- [x] Record file format, `conv_*` id scheme, filename mapping, section canonical
  order (with additive `environment`/`artifacts`, resume checkpoints, and hidden
  transcript-weight comments), and existing `(none)` rendering remain backward
  compatible. Legacy record bytes are not rewritten merely because v2 reads them.
- [x] `~/.conversate/` untouched; `import` preserves its public copy/collision contract
  (and its nested layouts now actually get indexed, fixing §2.4).
- [x] All mutations remain CLI-owned; agents never edit records or indexes directly.
- [x] Runtime root resolution never falls back to cwd/repository discovery: it uses an
  explicit compatibility flag or a real platform home and otherwise errors before any
  write. `doctor --fix` invokes only installer/repair artifacts under the selected
  Plugin installation root; it never uses compile-time `CARGO_MANIFEST_DIR` source.
- [x] `.semble/` stays in `.gitignore` (index-v2 lives inside it — records stay
  git-trackable).
- [ ] Knowledge-base reconciliation is an implementation exit criterion (the files are
  not present in this worktree); when available,
  `.arca/space/relay-sp/what/{architecture,manifest,flows}.md` and
  `.arca/index/ubiquitous-language.md` gain: **scan engine**, **index cache
  (index-v2)**, **fingerprint**, **postings**, **context pack**, **checkpoint**,
  **transcript weight**.

## 13. Milestones

| # | Scope | Exit criteria |
|---|---|---|
| M1 | Recoverable single-pass mutation engine (§6), hand parsers / drop `regex` (§8), parallel recursive scans (§4) | Existing contract suites, parser/determinism, journal recovery, and structural I/O gates green; budgets restored to 100/125 |
| M2 | Generation-published index v2 cache + fingerprints + freshness (§5) | Equivalence/corruption/concurrency gates green; 10k scale gates green; 60/80 targets green on pinned runner |
| M3 | Search v2 postings + tiers + `--no-semble` (§7) | `test_search_tiers` green; `cli_search` gates green |
| M4 | Fidelity v2: durable schema, `relay context`, doctor lint, playbooks (§10) | `test_context_pack` green; text/JSON budget contract and docs/playbooks updated |
| M5 | Profiler scale ops, nightly wiring, `.arca` knowledge-base reconciliation, README | Nightly scale profile in place; drift reconciled |

M1 and M2 deliver the headline speed; M3–M5 can land independently.

## 14. Risks

| Risk | Mitigation |
|---|---|
| mtime unreliability (FAT, restores, clock skew) | CLI writes update rows transactionally; suspicious metadata hashes; documented same-stat limit plus `--full`/`doctor --fix`/`RELAY_NO_CACHE=1` |
| Cache/records divergence or mixed generations | manifest-last generation publication; corruption matrix and cache/no-cache equivalence gates |
| Crash between neighbor record writes | fsynced, idempotent transaction journal replayed before the next snapshot |
| Windows Defender / AV latency on temp+rename bursts | one emit per command (§6) minimizes rename count; budgets measured on Windows |
| Hand parsers diverge from regex behavior | port regex unit tests verbatim before deleting the dependency (§11) |
| Postings grow unexpectedly | postings cover compact index haystacks, never bodies; profiler reports postings bytes and cache remains deletable/rebuildable |
| `relay context` leaks secrets from new sections | `environment`/`artifacts` are reference-only by rule; save.md redaction rule restated in both playbooks; fidelity lint does not auto-fill |
| Budgeting drops load-bearing context nondeterministically | explicit mandatory envelope, stable unit/drop order, exact UTF-8 byte cap, and text/JSON equivalence tests |

## 15. Out of scope

- Changing the handoff record format itself (stays human-readable Markdown + TOML).
- Automatic archive sharding (`compact --shard-by-year` noted as future work, §5.6).
- Replacing or embedding the semble semantic tier.
- Any daemon/watcher process — relay stays a short-lived CLI.
