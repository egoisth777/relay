# Relay artifact manifest

| Artifact | Authority | Purpose |
|---|---|---|
| `convs/**/*.md` | Source of truth | Human-readable handoff records |
| `index.jsonl` | Derived | Stable public/greppable compatibility rows |
| `.semble/index-v2/manifest.json` | Derived commit point | Names one complete cache generation |
| `.semble/index-v2/records.N.jsonl` | Derived | Sorted rows with stat and fingerprint fields |
| `.semble/index-v2/postings.base.B.bin` | Derived | Random-access base postings |
| `.semble/index-v2/postings.delta.N.bin` | Derived | Bounded changed-row posting overlays |
| `.semble/write.lock` | Coordination | Shared readers and exclusive mutations |
| `.semble/txn.pending` | Authoritative while present | Recoverable ordered record after-images |

The CLI may delete/prune obsolete cache generations only after a successful manifest
commit. It must never treat `txn.pending` as disposable cache. `~/.conversate/` is a
read-only import source and is never a runtime root.

Public record frontmatter remains compatible. `relay_schema = 2` is additive and marks
valid hidden transcript-weight metadata. `environment` and `artifacts` are Markdown
sections, not embedded file contents.
