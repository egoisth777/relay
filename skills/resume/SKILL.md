---
name: resume
description: Resume a saved conversation from the Relay archive and continue where it left off.
disable-model-invocation: false
argument-hint: "[id-or-query]"
---

Run the Relay flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and reads records from the Relay archive (`~/.relay/convs/`). Do not load broad instructions for the common path; this file suffices.

## Common Path

1. Query/id: `$ARGUMENTS`. If empty, list candidates: `~/.relay/bin/relay list --limit 10`
2. Search: `~/.relay/bin/relay search "<id-or-query>"`
3. If one confident hit, build pack: `~/.relay/bin/relay context <id> --budget-tokens <harness-budget>`. If multiple, present ranked ids/topics for user choice.
4. Read frontmatter (identity, status, tags, refs) and reconstruct in order:
   - `## summary`: orientation.
   - `## dict`: agreed terms/meanings; internalize first.
   - `## user-instructions`: adopt as standing behavior.
   - `## resume`: note goal, completed checkpoints (avoid redoing work), act on `next-steps`, keep `open-questions` live, invoke `suggested-skills` as needed.
   - `## qa`: treat `Q (open)` as live threads.
   - `## decisions`, `## environment`, and `## artifacts`: settled choices and reference execution state (do not relitigate).
   - `## condensed-transcript`: chronological exchange log.
   - `## insights` / `## sources`: read referenced files only as needed.
5. Run context pack's `next action argv` to activate conversation.
6. Show summary, active goal/next step, and open threads.

## Required Rules

- `~/.relay/convs/*.md` is source of truth; `~/.relay/index.jsonl` is derived cache.
- Every record is a resumption point. Do not skip `## dict` or `## user-instructions`.
- Mutate `## decisions` only when explicitly asked to edit decisions.
- CLI search handles filename, index, and body-scoring; use before raw search.

## Lazy References

Only after search/show needs advanced behavior, read `~/.relay/references/resume.md`.

$ARGUMENTS
