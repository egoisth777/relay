---
name: continue
description: Continue a conversation fresh as a new linked record in the Conversation database.
disable-model-invocation: false
argument-hint: "[parent] [topic]"
---

Run the conversate `conv:continue` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.conversate/` by default) and writes records to the Conversation database (`~/.conversate/convs/`).

Do not load broad instructions for the common path; this file is enough to continue a normal conversation in a fresh record.

## Common Path

1. Resolve the parent from the current conversation id or the first clear id/query in `$ARGUMENTS`. If no parent can be inferred, ask once for the conversation to continue.
2. Use a supplied topic only when the user gives one; otherwise let the CLI derive the continuation topic from the parent.
3. Create the continuation with the deterministic primitive:
   `python ~/.conversate/scripts/conv_cli.py continue <parent-id-or-query>`
4. When a user supplied a clean topic, pass it explicitly:
   `python ~/.conversate/scripts/conv_cli.py continue <parent-id-or-query> --topic "<topic>"`
5. Add `--id <new-id>` only when a scripted caller needs a stable id. Report the new id and parent status returned by the CLI.

## Required Rules

- Treat `~/.conversate/convs/*.md` as source of truth and `~/.conversate/index.jsonl` as a derived cache.
- Do not hand-edit records or build continuation refs yourself for the common path; the continue primitive parks the parent, creates the child, carries recovery sections, reconciles refs, and rebuilds the index.
- Use `conv:sidekick` instead when the user wants a side exploration rather than the same topic in a clean record.

## Lazy References

Only after the common continue command needs advanced branch behavior, read `~/.conversate/references/branching.md`. Examples: sidekick-vs-continue intent, stable scripted ids, parent query ambiguity, or recovery-section carry-forward details.

$ARGUMENTS
