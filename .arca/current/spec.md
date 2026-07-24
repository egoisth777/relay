# Relay delivered specification

## Purpose

Relay is a short-lived Rust CLI and plugin workflow over a Markdown source of truth. It resolves a plugin installation root (normally `~/.relay/`), operates on the recursive Relay archive under `convs/`, and never uses the repository checkout or current working directory for runtime discovery or storage.

## Required behavior
Each `REQ-###` heading below is a stable, unique requirement identifier and Markdown anchor for goal, residual, ticket, and test references.

### REQ-001 — Archive authority

The Relay archive is authoritative human-readable handoff records. Derived indexes and caches may be rebuilt from it.
### REQ-002 — Public archive output

Public machine output uses `relay_archive` for the archive path. `conversation_database` remains an explicit deprecated compatibility alias with the same value.
### REQ-003 — Consistent archive snapshots

Archive-consuming commands take one symlink-safe snapshot and never serve a stale changed record.
### REQ-004 — Durable mutations

Mutations are serialized, journaled before replacement, recoverable after interruption, and publish derived artifacts only after ordered record writes; the manifest is published last.
### REQ-005 — Search tier selection

Search selects an installed `semble`, then opt-in `uvx semble` only when `RELAY_USE_UVX_SEMBLE=1`, otherwise body scoring fallback. Doctor reports the same selected tier.
### REQ-006 — Record schema

Records retain mandatory `summary`, `glossary`, and `qa` sections. Schema 2 adds environment, artifacts, checkpoint entries, and transcript weight without breaking legacy records.
### REQ-007 — Context reconstruction

`relay context` reconstructs required sections, adds bounded linked context, trims in the documented order, emits the v2 banner and final `truncated: yes|no`, and does not mutate status.
### REQ-008 — Read-only legacy import

Import reads legacy `~/.conversate/` as a read-only source, reports collisions, and never changes that source.

## Authority links

The detailed, maintained contracts are preserved in:

- [Architecture](../space/relay-sp/what/architecture.md)
- [Artifact manifest](../space/relay-sp/what/manifest.md)
- [Operational flows](../space/relay-sp/what/flows.md)

This specification summarizes their delivered contract; those files remain the detailed product knowledge and are not duplicated here.
