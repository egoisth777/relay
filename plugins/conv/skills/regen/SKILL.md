---
name: regen
description: Regenerate refs and rebuild derived indexes for the Conversation database.
disable-model-invocation: false
argument-hint: "[id]"
---

Run the conversate `conv:regen` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.conversate/` by default) and rebuilds derived indexes for the Conversation database (`~/.conversate/convs/`).

1. Read `~/.conversate/references/cli.md` and follow it exactly.
2. Rebuild refs/index with `python ~/.conversate/scripts/conv_cli.py regen-refs` (and `doctor` for drift checks); treat any `$ARGUMENTS` as a record id to scope.

The index is a derived cache; `~/.conversate/convs/*.md` is source of truth. See `~/.conversate/SKILL.md` for invariants.

$ARGUMENTS
