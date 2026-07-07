---
name: conversate
description: Persist, retrieve, list, park, branch, return, continue, and repair topic-bound conversations in the Conversation database as distilled, resumable records. Use for explicit commands like conv:save, conv:resume, conv:list, conv:park, conv:sidekick, conv:return, conv:continue, conv:regen; natural language such as save this, checkpoint this, resume the auth discussion, what conversations are open, sidekick this, bring the branch back, or continue fresh; and any injected CONV AUTO-SAVE reminders.
---

# conversate

Use this skill to manage the Conversation database. A record is not a transcript: it is a
distilled, resumable artifact any agent can pick up cold. Plugin source is this repo.
Installed plugin files live under the Plugin installation root, `~/.conversate/` by
default. Conversation records live under the Conversation database,
`~/.conversate/convs/`, which is the source of truth for every harness (Claude Code, pi,
oh-my-pi, and Codex).

## Invariants

- Treat `~/.conversate/convs/*.md` as the source of truth. Treat
  `~/.conversate/index.jsonl` as a derived cache that can be deleted and rebuilt.
- Use TOML frontmatter delimited by `+++`. Keep it thin: id, topic, status, tags, refs,
  created, updated.
- Every record is a resumption point. Mandatory body sections `## summary`, `## dict`,
  `## qa`, plus the always-present resumption sections `## resume`,
  `## user-instructions`, `## condensed-transcript` (empty ones render `(none)`).
- Reconstruct language first: read `## dict` before `## user-instructions`, `## qa`,
  sources, insights, or decisions.
- Keep refs bidirectional with valued directional labels:
  - `spawned-from` <-> `spawned-to`
  - `continued-from` <-> `continued-as`
  - `informed-by` <-> `informed`
- Never mutate `## decisions` unless the user explicitly asks to edit decisions.
  Contradictions from branches go into `## qa` as open questions.
- Redact secrets and PII; reference artifacts (files, commits, PRs) by path or URL rather
  than duplicating their contents.
- Use the CLI for every write, index rebuild, status change, and ref regeneration:
  `python ~/.conversate/scripts/conv_cli.py <command>`.

## Routing

- For `conv:save`, auto-save reminders, "save this", or "checkpoint this", read `~/.conversate/references/save.md`.
- For `conv:resume` or "continue where we left off", read `~/.conversate/references/resume.md`.
- For `conv:list`, "what's open", or recent/open conversation lists, read `~/.conversate/references/list.md`.
- For `conv:park`, read `~/.conversate/references/save.md` and save with status `parked`.
- For `conv:sidekick`, `conv:return`, or `conv:continue`, read `~/.conversate/references/branching.md`.
- For `conv:regen`, drift checks, CLI details, or implementation troubleshooting, read `~/.conversate/references/cli.md`.

## Runtime Paths

Normal operation uses the installed CLI under the Plugin installation root:
`python ~/.conversate/scripts/conv_cli.py`. The default Conversation database is
`~/.conversate/convs/`. A cwd-local `.conversate/` directory is only a non-default compatibility root when the user explicitly passes `--conv-root PATH`; do not teach it as the normal plugin model.

## Auto-Save Behavior

If the harness injects a `CONV AUTO-SAVE: threshold reached` reminder (via hooks), honor
it: run the `conv:save` flow silently, infer the id and topic, write the checkpoint,
rebuild the index, and tell the user only:

`Auto-saved as <id> - rename anytime.`

In harnesses without hooks, self-trigger the same save at natural milestones, on topic
shifts, and before the session ends. Do not block the user's current task for
confirmation.
