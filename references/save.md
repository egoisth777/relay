# conv save and park

Use this for `conv:save`, `conv:park`, auto-save reminders, and natural-language checkpoint requests.

## Steps

1. Ensure the store exists:
   `python .conversate/scripts/conv_cli.py init`
2. Infer a concise topic and tags from the current conversation.
3. Extract state in this priority order (highest value first):
   - `dict`: terms coined or agreed on, with meanings. This is the highest-value section
     — the agreed language a cold agent must adopt.
   - `user-instructions`: standing directives the user gave (constraints, workflow
     preferences, tone) that a fresh agent must keep honoring.
   - `resume`: the resumption plan — `goal` (one line), `next_steps`, `open_questions`,
     and `suggested_skills` (skills/commands/playbooks the resuming agent should invoke).
   - `qa`: sharp question/answer pairs; mark unresolved items as `**Q (open):**`.
   - `condensed-transcript`: a chronological, compressed exchange log so deep context
     survives — bullets of user/agent turns, not a raw replay.
   - `sources` / `insights` / `decisions`: files and contexts used, realizations worth
     keeping, and only settled decisions with reasoning.
4. Write the JSON payload (shape below) and pipe it to:
   `python .conversate/scripts/conv_cli.py upsert --stdin`
   Use `--status parked` for `conv:park`.
5. The CLI writes the TOML markdown, always renders the resumption sections (empty ones
   become `(none)`), reconciles reverse refs, and rebuilds `index.jsonl`.
6. For manual saves, present the inferred id/topic and invite rename. For auto-save, only
   print the one-line saved-as note.

## Rules

- **Redact secrets and PII.** Never write tokens, keys, passwords, or personal data into a
  record.
- **Reference, don't duplicate.** Point at files, commits, PRs, and docs by path or URL
  instead of pasting their contents into the record.
- Write for a *cold* agent recovering headspace, not for replaying a transcript. Exclude
  acknowledgments, tool noise, and chatter.

## Upsert payload shape

```json
{
  "topic": "conversation database skill implementation",
  "status": "active",
  "tags": ["skill", "infra"],
  "refs": [{"id": "conv_260615_parent", "rel": "spawned-from"}],
  "sections": {
    "summary": "One line describing what this conversation is.",
    "dict": "- **term** - agreed meaning.",
    "qa": "- **Q:** question? **A:** answer.\n- **Q (open):** unresolved question?",
    "sources": "- file: path/to/file\n- skill: conv",
    "insights": "- Useful realization.",
    "decisions": "1. Decision and reasoning."
  },
  "resume": {
    "goal": "Ship the redesigned engine and docs.",
    "next_steps": ["update tests", "rewrite SKILL.md"],
    "open_questions": ["how do pi/codex adapters register hooks?"],
    "suggested_skills": ["conv:save", "conv:resume"]
  },
  "user_instructions": ["use PowerShell on Windows", "never git commit without asking"],
  "condensed_transcript": [
    {"u": "redesign conversate", "a": "read the engine, planned the changes"},
    {"u": "make it agent-agnostic", "a": "moved store to .conversate/ (see scripts/conv_cli.py)"}
  ]
}
```

`summary`, `dict`, and `qa` are mandatory (upsert fails without them). `resume`,
`user_instructions`, and `condensed_transcript` are structured payload keys that always
render as sections — omit them and they render `(none)`, so never fabricate content to
fill them. Optional `sources`/`insights`/`decisions` are omitted from the file when empty.
