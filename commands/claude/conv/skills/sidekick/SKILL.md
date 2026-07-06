---
name: sidekick
description: Branch the current conversation into a sidekick record in the Conversation database.
disable-model-invocation: false
argument-hint: "[parent] [topic]"
---

Run the conversate `conv:sidekick` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.conversate/` by default) and writes records to the Conversation database (`~/.conversate/convs/`).

Do not load broad instructions for the common path; this file is enough to create a normal sidekick branch.

## Common Path

1. Resolve the parent from the current conversation id or the first clear id/query in `$ARGUMENTS`. If no parent can be inferred, ask once for the parent conversation.
2. Treat the remaining `$ARGUMENTS` as the sidekick topic. If no topic is available, ask once for the sidekick topic.
3. Create the branch with the deterministic primitive:
   `python ~/.conversate/scripts/conv_cli.py sidekick <parent-id-or-query> "<topic>"`
4. Add `--id <new-id>` only when a scripted caller needs a stable id. Add `--keep-parent-active` only when the user explicitly asks to keep the parent active.
5. Report the new branch id and the parent status returned by the CLI.

## Required Rules

- Treat `~/.conversate/convs/*.md` as source of truth and `~/.conversate/index.jsonl` as a derived cache.
- Do not hand-edit records or build branch refs yourself for the common path; the sidekick primitive creates the child, parks the parent by default, reconciles refs, and rebuilds the index.
- If the CLI reports an ambiguous parent query, present the choices and ask the user to choose.

## Lazy References

Only after the common sidekick command needs advanced branch behavior, read `~/.conversate/references/branching.md`. Examples: probe-vs-sidekick intent, same-session organization, stable scripted ids, or parent-active exceptions.

$ARGUMENTS
