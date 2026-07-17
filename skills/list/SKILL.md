---
name: list
description: List recent or open conversations in the Relay archive.
disable-model-invocation: false
argument-hint: "[filter]"
---

Run the Relay flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and reads records from the Relay archive (`~/.relay/convs/`). Do not load broad instructions for the common path; this file suffices.

## Common Path

1. Recent/open: `~/.relay/bin/relay list --limit 10`
2. By status (`active`/`parked`/`closed`): `~/.relay/bin/relay list --status <status> --limit 20`
3. JSON: `~/.relay/bin/relay list --json --limit 50`
4. Show ids with topic, status, updated time, and open questions. Do not read individual files to list.

## Required Rules

- Command queries cache kept fresh by snapshotting archive, reusing metadata-matching rows, parsing changed/new records, and safely repairing derived state. Legacy `index.jsonl` is retained as compatibility export.
- `~/.relay/convs/*.md` is source of truth for full records.
- Rebuilding cache (`~/.relay/bin/relay rebuild-index`) is optional for troubleshooting, not freshness.
- Keep output compact; listing is orientation.

## Lazy References

Only after the common list is not enough for advanced behavior, read `~/.relay/references/list.md`.

$ARGUMENTS
