---
name: list
description: List recent or open conversations in the Relay archive.
disable-model-invocation: false
argument-hint: "[filter]"
---

Run the Relay `relay:list` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and reads records from the Relay archive (`~/.relay/convs/`).

Do not load broad instructions for the common path; this file is enough to list normal records.

## Common Path

1. For recent/open records, run:
   `~/.relay/bin/relay list --limit 10`
2. If `$ARGUMENTS` is a clear status such as `active`, `parked`, or `closed`, run:
   `~/.relay/bin/relay list --status <status> --limit 20`
3. If you need machine-readable output for simple filtering, run:
   `~/.relay/bin/relay list --json --limit 50`
4. Present ids with topic, status, updated time, and open-question count. Do not read individual conversation markdown files just to list them.

## Required Rules

- The list command reads `~/.relay/index.jsonl`, a derived cache rebuilt from the Relay archive.
- Treat `~/.relay/convs/*.md` as source of truth if a later action needs a full record.
- If the index appears stale or missing, rebuild it with `~/.relay/bin/relay rebuild-index`, then rerun the list command.
- Keep the result compact. Listing is orientation, not resume.

## Lazy References

Only after the common list command is not enough for advanced behavior, read `~/.relay/references/list.md`. Examples: uncommon filtering, troubleshooting derived counts, or command details not covered above.

$ARGUMENTS
