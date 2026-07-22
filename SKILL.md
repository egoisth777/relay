---
name: relay
description: Persistent session handoffs. Use Relay to capture a session runtime artifact, distill it into a durable record, resume it in a later session, list handoffs, park work, branch exploration, return a digest, continue fresh, or repair the Relay archive.
---

# Relay plugin

Relay preserves momentum across sessions. It records the runtime artifact of active
work as a compact handoff another agent can pick up cold; it does not store a raw
transcript.

Plugin source is this repository. The Plugin installation root (Relay installation
root) is `~/.relay/` by default. The Relay archive,
`~/.relay/convs/`, is the source of truth for every supported harness.

## Invariants

- Treat `~/.relay/convs/*.md` as the source of truth. Treat
  `~/.relay/index.jsonl` as a derived cache that can be rebuilt.
- Use TOML frontmatter delimited by `+++`: id, topic, status, tags, refs, created,
  and updated.
- Every record is a resumption point. Required sections are `## summary`,
  `## glossary`, and `## qa`; `## resume`, `## user-instructions`, and
  `## condensed-transcript` are always present.
- Reconstruct language first: read `## glossary` before instructions, questions, sources,
  insights, or decisions.
- Keep directional refs bidirectional: `spawned-from`/`spawned-to`,
  `continued-from`/`continued-as`, and `informed-by`/`informed`.
- Never alter `## decisions` unless the user explicitly asks. Put branch
  contradictions into `## qa` as open questions.
- Redact secrets and PII. Reference external artifacts by path, commit, PR, or URL
  rather than duplicating them.
- Use `~/.relay/bin/relay <command>` for every write, index rebuild, status change,
  ref repair, and legacy import.

## Routing

- For `relay:save`, save/checkpoint requests, or `RELAY HANDOFF` reminders, read
  `~/.relay/references/save.md`.
- For `relay:resume` or "continue where we left off", read
  `~/.relay/references/resume.md`, then use `relay context <id>` for the reconstruction pack.
- For `relay:list` or open/recent handoff lists, read `~/.relay/references/list.md`.
- For `relay:park`, read `~/.relay/references/save.md` and save with status `parked`.
- For `relay:sidekick`, `relay:return`, or `relay:continue`, read
  `~/.relay/references/branching.md`.
- For `relay:regen`, drift checks, imports, or implementation troubleshooting, read
  `~/.relay/references/cli.md`.

## Legacy recovery

Relay never changes `~/.conversate/` automatically. To carry forward previous
records, run `~/.relay/bin/relay import --from ~/.conversate`. The import copies only
missing records, reports same-name collisions, and never overwrites either archive.

## Handoff behavior

When the harness injects a `RELAY HANDOFF: threshold reached` reminder, run the
`relay:save` flow silently, infer the record ID and topic, rebuild the index, and tell
the user only:

`Handed off as <id> - resume anytime.`

In harnesses without hooks, create the same handoff at natural milestones, topic
changes, and before a session ends. Do not block the user's current task for
confirmation.
