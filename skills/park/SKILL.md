---
name: park
description: Park the current conversation - save it with status parked in the Relay archive.
disable-model-invocation: false
argument-hint: "[id-or-topic]"
---

Run the Relay `relay:park` flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and writes records to the Relay archive (`~/.relay/convs/`).

Do not load broad instructions for the common path; this file is enough to park a normal checkpoint.

## Common Path

1. Ensure the Plugin installation root and Relay archive exist:
   `~/.relay/bin/relay init`
2. Infer a concise topic and tags from the current conversation. Treat `$ARGUMENTS` as an id or topic hint.
3. Extract a normal save record: `summary`, `dict`, and `qa` are mandatory; `resume`, `user_instructions`, `condensed_transcript`, `sources`, `insights`, and `decisions` are optional.
4. Pipe the conversation JSON to the minimal park owner command:
   `~/.relay/bin/relay upsert --stdin --status parked`
5. Report the resulting id/topic and that the conversation is parked.

## Required Rules

- Treat `~/.relay/convs/*.md` as source of truth and `~/.relay/index.jsonl` as a derived cache.
- Redact secrets and PII. Never write tokens, keys, passwords, or personal data into a record.
- Reference artifacts by path, commit, PR, or URL instead of duplicating their contents.
- Write for a cold agent recovering headspace. Exclude acknowledgments, tool noise, and chatter.

## Lazy References

Only after the common path needs advanced save behavior, read `~/.relay/references/save.md`. Examples: unusual ref labels, schema detail not covered above, or troubleshooting a failed parked upsert.

$ARGUMENTS
