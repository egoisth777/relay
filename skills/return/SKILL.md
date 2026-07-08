---
name: return
description: Return from a branch back to its parent conversation in the Conversation database.
disable-model-invocation: false
argument-hint: "[branch] [digest]"
---

Run the Conversate `conversate:return` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.conversate/` by default) and writes records to the Conversation database (`~/.conversate/convs/`).

Do not load broad instructions for the common path; this file is enough to return a normal branch.

## Common Path

1. Resolve the branch from the current conversation id or the first clear id/query in `$ARGUMENTS`. If no branch can be inferred, ask once for the branch conversation.
2. Draft a concise digest covering what was explored, the conclusion, useful files or patterns, contradictions, and next steps. Use any remaining `$ARGUMENTS` as digest input.
3. Close the branch with the deterministic primitive:
   `python ~/.conversate/scripts/conv_cli.py return <branch-id-or-query> --digest "<digest>"`
4. Add `--parent <parent-id>` only when the CLI reports ambiguous parent refs.
5. If the parent is the live conversation, inject the digest into current context after the command succeeds.

## Required Rules

- Treat `~/.conversate/convs/*.md` as source of truth and `~/.conversate/index.jsonl` as a derived cache.
- Do not hand-edit branch markdown or run separate repair steps for the common path; the return primitive writes `## digest`, closes the branch, reconciles refs, and rebuilds the index.
- Put unresolved contradictions in the digest as explicit open questions so the parent can decide them.

## Lazy References

Only after the common return command needs advanced branch behavior, read `~/.conversate/references/branching.md`. Examples: ambiguous parent refs, contradiction handling, digest shape, or parent-context merge choices.

$ARGUMENTS
