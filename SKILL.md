---
name: conversate
description: Persist, retrieve, list, park, branch, return, continue, and repair topic-bound conversations in the .conversate store as distilled, resumable records. Use for explicit commands like conv:save, conv:resume, conv:list, conv:park, conv:sidekick, conv:return, conv:continue, conv:regen; natural language such as save this, checkpoint this, resume the auth discussion, what conversations are open, sidekick this, bring the branch back, or continue fresh; and any injected CONV AUTO-SAVE reminders.
---

# conversate

Use this skill to manage the conversation store in `.conversate/`. A record is not a
transcript: it is a distilled, resumable artifact any agent can pick up cold. This skill
is agent-agnostic — the same `.conversate/` store and CLI back every harness (claude, pi,
omp, codex), which reach it through symlinked skill directories.

## Invariants

- Treat `.conversate/convs/*.md` as the source of truth. Treat `.conversate/index.jsonl`
  as a derived cache that can be deleted and rebuilt.
- Use TOML frontmatter delimited by `+++`. Keep it thin: id, topic, status, tags, refs,
  created, updated.
- Every record is a resumption point. Mandatory body sections `## summary`, `## dict`,
  `## qa`, plus the always-present resumption sections `## resume`,
  `## user-instructions`, `## condensed-transcript` (empty ones render `(none)`).
- Reconstruct language first: read `## dict` before `## qa`, sources, insights, or
  decisions.
- Keep refs bidirectional with valued directional labels:
  - `spawned-from` <-> `spawned-to`
  - `continued-from` <-> `continued-as`
  - `informed-by` <-> `informed`
- Never mutate `## decisions` unless the user explicitly asks to edit decisions.
  Contradictions from branches go into `## qa` as open questions.
- Redact secrets and PII; reference artifacts (files, commits, PRs) by path or URL rather
  than duplicating their contents.
- Use the CLI for every write, index rebuild, status change, and ref regeneration:
  `python .conversate/scripts/conv_cli.py <command>`.

## Routing

- For `conv:save`, auto-save reminders, "save this", or "checkpoint this", read `references/save.md`.
- For `conv:resume` or "continue where we left off", read `references/resume.md`.
- For `conv:list`, "what's open", or recent/open conversation lists, read `references/list.md`.
- For `conv:park`, read `references/save.md` and save with status `parked`.
- For `conv:sidekick`, `conv:return`, or `conv:continue`, read `references/branching.md`.
- For `conv:regen`, drift checks, CLI details, or implementation troubleshooting, read `references/cli.md`.

## Store location

`init` with no override creates `<cwd>/.conversate/`. To point the store elsewhere — a
shared vault, a personal brain — set `$CONVERSATE_ROOT` (or the legacy `$BRAIN_CONV`), or
pass `--conv-root PATH`. Resolution and precedence are detailed in `references/cli.md`.

## Auto-Save Behavior

If the harness injects a `CONV AUTO-SAVE: threshold reached` reminder (via hooks), honor
it: run the `conv:save` flow silently, infer the id and topic, write the checkpoint,
rebuild the index, and tell the user only:

`Auto-saved as <id> - rename anytime.`

In harnesses without hooks, self-trigger the same save at natural milestones, on topic
shifts, and before the session ends. Do not block the user's current task for
confirmation.
