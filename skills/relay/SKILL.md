---
name: relay
description: Persist, retrieve, list, park, branch, return, continue, import, and repair distilled session handoffs. Use for relay:save, relay:resume, relay:list, relay:park, relay:sidekick, relay:return, relay:continue, relay:regen, save/checkpoint requests, resuming a handoff, or injected RELAY HANDOFF reminders.
---

# Relay

Relay turns a session's runtime artifact into a compact handoff another session can
continue. Records are distilled context, not transcripts. Plugin source is this repo;
the Plugin installation root is `~/.relay/`, and its Relay archive at
`~/.relay/convs/` is the source of truth for Claude Code, pi, oh-my-pi, and Codex.

## Invariants

- Treat `~/.relay/convs/*.md` as the source of truth and
  `~/.relay/index.jsonl` as a rebuildable cache.
- Keep TOML frontmatter thin: id, topic, status, tags, refs, created, and updated.
- Every record must support cold resumption: required sections are `summary`, `glossary`,
  and `qa`; `resume`, `user-instructions`, and `condensed-transcript` are always on.
- Read `## glossary` first so the next session adopts established language before acting.
- Keep `spawned-from`/`spawned-to`, `continued-from`/`continued-as`, and
  `informed-by`/`informed` refs bidirectional.
- Do not rewrite decisions without explicit user approval. Redact secrets and PII;
  reference artifacts by path, commit, PR, or URL instead of duplicating them.
- Use `~/.relay/bin/relay <command>` for writes, indexes, refs, statuses, and imports.

## Routing

- `relay:save`, checkpoint requests, and `RELAY HANDOFF` reminders: read
  `~/.relay/references/save.md`.
- `relay:resume` and "continue where we left off": read
  `~/.relay/references/resume.md`, then use `relay context <id>` for the reconstruction pack.
- `relay:list` and open/recent handoff requests: read
  `~/.relay/references/list.md`.
- `relay:park`: use `~/.relay/references/save.md` with status `parked`.
- `relay:sidekick`, `relay:return`, and `relay:continue`: read
  `~/.relay/references/branching.md`.
- `relay:regen`, diagnostics, and legacy imports: read `~/.relay/references/cli.md`.

## Legacy recovery

Relay never changes `~/.conversate/` automatically. To preserve prior work, run:

```text
~/.relay/bin/relay import --from ~/.conversate
```

Only missing records are copied. Same-name collisions are reported and never
overwritten.

## Handoff behavior

On `RELAY HANDOFF: threshold reached`, run `relay:save` silently, infer the topic and
ID, write the handoff, rebuild the index, and tell the user only:

`Handed off as <id> - resume anytime.`

Without hooks, create a handoff at natural milestones, topic shifts, and before the
session ends without blocking the user's task for confirmation.
