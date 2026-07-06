---
name: resume
description: Resume a saved conversation from the Conversation database and continue where it left off.
disable-model-invocation: false
argument-hint: "[id-or-query]"
---

Run the conversate `conv:resume` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.conversate/` by default) and reads records from the Conversation database (`~/.conversate/convs/`).

Do not load broad instructions for the common path; this file is enough to resolve and resume a normal record.

## Common Path

1. Use `$ARGUMENTS` as the record id or search query. If it is empty, list candidates first:
   `python ~/.conversate/scripts/conv_cli.py list --limit 10`
2. Resolve the target with:
   `python ~/.conversate/scripts/conv_cli.py search "<id-or-query>"`
3. If exactly one confident hit returns, show it:
   `python ~/.conversate/scripts/conv_cli.py show <id> --markdown`
   If multiple hits return, present the ranked ids/topics and ask the user to choose.
4. Read frontmatter first for identity, status, tags, and refs. Then reconstruct in this order:
   - `## summary`: orientation.
   - `## dict`: agreed language. Internalize this before other sections.
   - `## user-instructions`: adopt these as standing behavior for the resumed session.
   - `## resume`: note the goal, act on `next-steps`, keep `open-questions` live, and invoke listed `suggested-skills` when needed.
   - `## qa`: treat `Q (open)` entries as live threads.
   - `## condensed-transcript`: use when summary and qa are not enough.
   - `## decisions`: settled items; do not relitigate unless the user asks.
   - `## insights` and `## sources`: read referenced files only as the resumed task needs them.
5. Mark the conversation active:
   `python ~/.conversate/scripts/conv_cli.py set-status <id> active`
6. Present a short summary, the active goal or next step, and the open threads.

## Required Rules

- Treat `~/.conversate/convs/*.md` as source of truth and `~/.conversate/index.jsonl` as a derived cache.
- Every record is a resumption point. Do not skip `## dict` or `## user-instructions`.
- Do not mutate `## decisions` during resume unless the user explicitly asks to edit decisions.
- Use the CLI search before raw text search; the CLI handles the filename, index, and body-scoring path.

## Lazy References

Only after search/show needs branch or advanced behavior, read `~/.conversate/references/resume.md`. Examples: ambiguous linked conversations, branch digests, search-cascade troubleshooting, or resolver details not covered above.

$ARGUMENTS
