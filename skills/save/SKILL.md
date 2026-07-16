---
name: save
description: Save or checkpoint the current conversation as a resumable record in the Relay archive.
disable-model-invocation: false
argument-hint: "[id-or-topic]"
---

Run the Relay `relay:save` flow for this conversation. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and writes records to the Relay archive (`~/.relay/convs/`).

Do not load broad instructions for the common path; this file is enough to save a normal checkpoint.

## Common Path

1. Ensure the Plugin installation root and Relay archive exist:
   `~/.relay/bin/relay init`
2. Infer a concise topic and tags from the current conversation. Treat `$ARGUMENTS` as an id or topic hint.
3. Extract the record in this priority order:
   - `dict`: agreed terms and meanings. This is the language a cold agent must adopt.
   - `user-instructions`: standing user directives, constraints, workflow preferences, and tone.
   - `resume`: `goal`, `next_steps`, `open_questions`, and `suggested_skills`.
   - `qa`: sharp question/answer pairs; mark unresolved items as `**Q (open):**`.
   - `condensed-transcript`: a compressed chronological exchange log, not a raw transcript.
   - `sources`, `insights`, and `decisions`: referenced artifacts, useful realizations, and settled decisions with reasoning.
4. Pipe the conversation JSON to:
   `~/.relay/bin/relay upsert --stdin`
5. The CLI writes the TOML markdown under `~/.relay/convs/`, reconciles refs when present, and rebuilds `~/.relay/index.jsonl`.
6. For a manual save, report the inferred id/topic and invite a rename. For auto-save, print only `Auto-saved as <id> - rename anytime.`

## Required Rules

- Treat `~/.relay/convs/*.md` as source of truth and `~/.relay/index.jsonl` as a derived cache.
- `summary`, `dict`, and `qa` are mandatory. `resume`, `user_instructions`, and `condensed_transcript` may be empty but should never be fabricated.
- Redact secrets and PII. Never write tokens, keys, passwords, or personal data into a record.
- Reference artifacts by path, commit, PR, or URL instead of duplicating their contents.
- Write for a cold agent recovering headspace. Exclude acknowledgments, tool noise, and chatter.
- Do not edit settled decisions unless the user explicitly asks for a decision change.

## JSON Shape

```json
{
  "topic": "conversation database skill implementation",
  "status": "active",
  "tags": ["skill", "infra"],
  "refs": [],
  "sections": {
    "summary": "One line describing what this conversation is.",
    "dict": "- **term** - agreed meaning.",
    "qa": "- **Q:** question? **A:** answer.\n- **Q (open):** unresolved question?",
    "sources": "- file: path/to/file",
    "insights": "- Useful realization.",
    "decisions": "1. Decision and reasoning."
  },
  "resume": {
    "goal": "Ship the redesigned engine and docs.",
    "next_steps": ["update tests", "rewrite SKILL.md"],
    "open_questions": ["how do adapters register hooks?"],
    "suggested_skills": ["relay:save", "relay:resume"]
  },
  "user_instructions": ["use PowerShell on Windows"],
  "condensed_transcript": [
    {"u": "redesign relay", "a": "read the engine, planned the changes"}
  ]
}
```

## Lazy References

Only after the common path needs branch or advanced behavior, read `~/.relay/references/save.md`. Examples: explicit branch ref labels, parking through `relay:park`, or schema detail not covered above.

$ARGUMENTS
