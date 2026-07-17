---
name: save
description: Save or checkpoint the current conversation as a resumable record in the Relay archive.
disable-model-invocation: false
argument-hint: "[id-or-topic]"
---

Run the Relay flow. Plugin source is this repo. The installed CLI lives under the Plugin installation root (`~/.relay/` by default) and writes records to the Relay archive (`~/.relay/convs/`). Do not load broad instructions for the common path; this file suffices.

## Common Path

1. Initialize: `~/.relay/bin/relay init`
2. Infer topic/tags; treat `$ARGUMENTS` as id/topic hint.
3. Extract record in priority order:
   - `dict`: agreed terms and meanings.
   - `user-instructions`: directives/tone, constraints, and workflow preferences.
   - `resume`: `goal`, completed `checkpoints` (avoid redoing work), `next_steps`, `open_questions`, `suggested_skills`.
   - `qa`: QA pairs; unresolved as `**Q (open):**`.
   - `condensed-transcript`: compressed chronological log; weight `w` (1-3; w=3 means user would repeat it verbatim).
   - `environment`: harness/platform/cwd/repo/branch/HEAD/PR execution state.
   - `artifacts`: touched files/commits/PRs (one line of state each, no content).
   - `sources`, `insights`, `decisions`: reference files, realizations, settled decisions with reasoning.
4. Save: `~/.relay/bin/relay upsert --stdin`
5. CLI writes markdown to `~/.relay/convs/`, reconciles refs, and updates `~/.relay/index.jsonl`.
6. Manual: report id/topic and invite rename. Auto-save: print only 'Auto-saved as <id> - rename anytime.'

## Required Rules

- `~/.relay/convs/*.md` is source of truth; `~/.relay/index.jsonl` is derived cache.
- `summary`, `dict`, and `qa` are mandatory. `resume`, `user_instructions`, `condensed_transcript`, `environment`, and `artifacts` may be empty or omitted but should never be fabricated.
- Redact secrets/PII. Reference artifacts by path, commit, PR, or URL (never duplicate contents).
- Keep records concise; exclude chatter, acknowledgments, and tool noise.
- Edit settled decisions only when explicitly requested.

## JSON Shape

```json
{
  "topic": "db", "status": "active", "tags": ["infra"], "refs": [],
  "sections": {
    "summary": "sum", "dict": "- **t** - m.", "qa": "- **Q:** q? **A:** a.\\n- **Q (open):** o?",
    "sources": "", "insights": "", "decisions": ""
  },
  "resume": {
    "goal": "g", "checkpoints": ["c"], "next_steps": ["s"], "open_questions": ["q"], "suggested_skills": []
  },
  "user_instructions": [],
  "environment": ["platform: OS", "cwd: path", "repo: path", "branch: main", "HEAD: commit", "PR: #1"],
  "artifacts": ["file - state"],
  "condensed_transcript": [{"u": "u", "a": "a", "w": 3}]
}
```

## Lazy References

Only after save needs advanced behavior, read `~/.relay/references/save.md`.

$ARGUMENTS
