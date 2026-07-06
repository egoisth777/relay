---
name: list
description: List recent or open conversations in the Conversation database.
disable-model-invocation: false
argument-hint: "[filter]"
---

Run the conversate `conv:list` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.conversate/` by default) and reads records from the Conversation database (`~/.conversate/convs/`).

Do not load broad instructions for the common path; this file is enough to list normal records.

## Common Path

1. For recent/open records, run:
   `python ~/.conversate/scripts/conv_cli.py list --limit 10`
2. If `$ARGUMENTS` is a clear status such as `active`, `parked`, or `closed`, run:
   `python ~/.conversate/scripts/conv_cli.py list --status <status> --limit 20`
3. If you need machine-readable output for simple filtering, run:
   `python ~/.conversate/scripts/conv_cli.py list --json --limit 50`
4. Present ids with topic, status, updated time, and open-question count. Do not read individual conversation markdown files just to list them.

## Required Rules

- The list command reads `~/.conversate/index.jsonl`, a derived cache rebuilt from the Conversation database.
- Treat `~/.conversate/convs/*.md` as source of truth if a later action needs a full record.
- If the index appears stale or missing, rebuild it with `python ~/.conversate/scripts/conv_cli.py rebuild-index`, then rerun the list command.
- Keep the result compact. Listing is orientation, not resume.

## Lazy References

Only after the common list command is not enough for advanced behavior, read `~/.conversate/references/list.md`. Examples: uncommon filtering, troubleshooting derived counts, or command details not covered above.

$ARGUMENTS
