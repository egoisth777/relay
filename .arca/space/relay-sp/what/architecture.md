# Relay architecture

Relay is a short-lived Rust CLI over a Markdown source of truth. The Plugin
installation root defaults to `~/.relay/`; the Relay archive is its recursive
`convs/` tree. Runtime discovery never uses the source checkout or current working
directory. Public machine output spells the archive `relay_archive`;
`conversation_database` remains only as a deprecated compatibility alias sourced from
the same path.

## Scan engine

Every archive-consuming command takes one symlink-safe recursive metadata snapshot.
`ScanEngine` parses changed or explicitly full-scan records with scoped standard-library
workers, deterministic chunks, and path-sorted collection. `RELAY_SCAN_THREADS=1..64`
overrides the default; normal automatic parallelism is capped at eight.

## Index cache (index-v2)

`.semble/index-v2/manifest.json` is the commit point for generation-named record rows
and trigram postings. A cache row adds `size`, `mtime_ns`, and an FNV-1a `fingerprint`
to the stable public index fields. Metadata-matching rows avoid record opens; changed
rows are reparsed. Integrity or source drift rebuilds derived state from the archive.
`index.jsonl` remains the byte-stable, id-sorted compatibility export.

Search uses exact ids, random-access tier-1/tier-2 postings, optional Semble, and a
parallel body fallback. An installed `semble` has priority. When it is absent,
`RELAY_USE_UVX_SEMBLE=1` enables `uvx semble`; all other values fall back to body
scoring. Doctor uses this same selection rule. `RELAY_NO_CACHE=1` is the reference
bypass; `rebuild-index --full` is the explicit complete parse.

## Mutations and recovery

Mutators hold `.semble/write.lock`, take one snapshot, stage targeted after-images,
and publish `.semble/txn.pending` before the first record replacement. The journal is
authoritative until all record writes, cache generation, compatibility export, and
manifest-last publication complete. Startup replay is idempotent. Reverse references
are reconciled only for changed forward-ref targets; `regen-refs` is the full repair.

## Fidelity

Record mandatory sections are `summary`, `glossary`, and `qa`. Schema-2 records add
`environment`, `artifacts`, resume `checkpoint` entries, and a durable `transcript weight`
of 1–3. `relay context` produces a v2 budget-aware context pack in reconstruction order,
adds one-hop linked digests, includes a structured action argv, and ends with a
`truncated: yes|no` marker. `relay list` orders active, parked, and closed records by
default. Doctor reports low-fidelity records but never fabricates missing content.
