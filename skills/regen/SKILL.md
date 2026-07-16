---
name: regen
description: Regenerate refs and rebuild derived indexes for the Relay archive.
disable-model-invocation: false
argument-hint: "[id]"
---

Run the Relay `relay:regen` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and rebuilds derived indexes for the Relay archive (`~/.relay/convs/`).

1. Read `~/.relay/references/cli.md` and follow it exactly.
2. Rebuild refs/index with `~/.relay/bin/relay regen-refs` (and `doctor` for drift checks); treat any `$ARGUMENTS` as a record id to scope.

The index is a derived cache; `~/.relay/convs/*.md` is source of truth. See `~/.relay/SKILL.md` for invariants.

$ARGUMENTS
