# Relay flows

## Warm read

1. Resolve the Plugin installation root and recover any transaction journal.
2. Acquire the existing shared store lock without creating one for a read-only root.
3. Snapshot the Relay archive once.
4. Trust metadata-matching index-v2 rows; parse only changed/new records.
5. Execute list/search/show/context from the fresh in-memory rows.
6. Repair derived artifacts atomically when safe; never serve a stale changed row.

## Mutation

1. Acquire the exclusive lock and replay interrupted state.
2. Snapshot/freshen once and validate before writes.
3. Stage the owned record plus affected reverse-ref neighbors.
4. Durably publish the complete transaction journal.
5. Replace ordered record after-images.
6. Publish record cache, postings, compatibility export, then manifest last.
7. Durably unlink the journal.

## Resume/context relay

1. Resolve an exact or unambiguous record.
2. Reconstruct frontmatter, summary, dict, instructions, resume/checkpoints, qa,
   decisions, environment, artifacts, and weighted transcript.
3. Add sorted one-hop refs and closed-record digests.
4. Trim linked units first, then low-weight/older transcript exchanges and optional
   sections until the requested byte budget fits.
5. Emit the context pack and activation argv without mutating status.

## Full repair

`regen-refs` reconciles the complete graph. `rebuild-index --full` and `doctor --fix`
reparse source records and reconstruct all derived cache artifacts. Import stages raw
legacy bytes, reports collisions, journals missing copies, and never changes its source.
