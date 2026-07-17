# Relay save and park

Use this for `relay:save`, `relay:park`, auto-save reminders, and natural-language checkpoint requests.

## Steps

1. Ensure the Plugin installation root and Relay archive exist:
   `~/.relay/bin/relay init`
2. Infer a concise topic and tags from the current conversation.
3. Extract state in this priority order (highest value first):
   - `dict`: terms coined or agreed on, with meanings. This is the highest-value section
     — the agreed language a cold agent must adopt.
   - `user-instructions`: standing directives the user gave (constraints, workflow
     preferences, tone) that a fresh agent must keep honoring.
   - `resume`: the resumption plan — `goal`, completed `checkpoints`, `next_steps`,
     `open_questions`, and `suggested_skills`. Checkpoints tell a cold agent what not
     to redo.
   - `qa`: sharp question/answer pairs; mark unresolved items as `**Q (open):**`.
   - `condensed-transcript`: a chronological, compressed exchange log. Give each
     structured exchange weight 1–3; weight 3 means the user would repeat it verbatim
     when resuming.
   - `environment`: reference-only harness/platform/cwd/repo/branch/HEAD/PR state.
   - `artifacts`: files, commits, and PRs touched, with one line of state each. Never
     include file contents.
   - `sources` / `insights` / `decisions`: files and contexts used, realizations worth
     keeping, and only settled decisions with reasoning.
4. Write the conversation JSON (shape below) and pipe it to:
   `~/.relay/bin/relay upsert --stdin`
   Use `--status parked` for `relay:park`.
5. The CLI transactionally writes the Markdown record, reconciles targeted reverse
   refs, updates index-v2, and emits the compatible `index.jsonl` once.
6. For manual saves, present the inferred id/topic and invite rename. For auto-save, only
   print the one-line saved-as note.

## Rules

- **Redact secrets and PII.** Never write tokens, keys, passwords, or personal data into a
  record.
- **Reference, don't duplicate.** Point at files, commits, PRs, and docs by path or URL
  instead of pasting their contents into the record.
- Write for a *cold* agent recovering headspace, not for replaying a transcript. Exclude
  acknowledgments, tool noise, and chatter.

## Upsert JSON Shape

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
    "sources": "- file: path/to/file\n- skill: relay",
    "insights": "- Useful realization.",
    "decisions": "1. Decision and reasoning."
  },
  "resume": {
    "goal": "Ship the redesigned engine and docs.",
    "checkpoints": ["schema agreed", "fixtures written"],
    "next_steps": ["update tests", "rewrite SKILL.md"],
    "open_questions": ["how do pi/codex adapters register hooks?"],
    "suggested_skills": ["relay:save", "relay:resume"]
  },
  "user_instructions": ["use PowerShell on Windows", "never git commit without asking"],
  "environment": ["platform: Windows", "repo: E:/repos/relay", "branch: relay-perf"],
  "artifacts": ["src/main.rs — index-v2 cache implemented", "tests/test_context_pack.py — green"],
  "condensed_transcript": [
    {"u": "redesign relay", "a": "read the engine, planned the changes", "w": 3},
    {"u": "make it agent-agnostic", "a": "moved records to ~/.relay/convs/", "w": 2}
  ]
}
```

`summary`, `dict`, and `qa` are mandatory (upsert fails without them). `resume`,
`user_instructions`, and `condensed_transcript` are structured JSON keys that always
render as sections — omit them and they render `(none)`, so never fabricate content to
fill them. `environment` and `artifacts` are optional and reference-only. Transcript
weights must be integers in `1..=3`; omitted weights default to 1 and are stored in
hidden Markdown markers so cache rebuilds and branches preserve fidelity.
