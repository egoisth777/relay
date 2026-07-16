# Relay branching

Use this only after the direct `relay:sidekick`, `relay:return`, or `relay:continue`
common path needs advanced branch behavior. The common path is the deterministic CLI
primitive for each action; do not hand-build refs or statuses for normal branch work.

## sidekick

1. If the branch topic is not specified, ask what to sidekick.
2. Pick the mode from intent:
   - `probe`: use a subagent, return a digest into the parent only, and do not create a file unless the user explicitly opts in.
   - `sidekick`: default. Create a peer conversation through the sidekick primitive.
   - Same-session organize branches are non-protecting convenience only; do not use as default.
3. Create the sidekick conversation:
   `~/.relay/bin/relay sidekick <parent-id-or-query> "<topic>"`
4. Add `--keep-parent-active` only when the user explicitly wants the parent to stay active. Add `--id <new-id>` only for scripted stable ids.
5. Include parent summary and decisions as context for the work, not as mutable branch decisions.

## return

1. Resolve the branch and parent via refs.
2. Generate a digest covering what was explored, conclusions, useful files/patterns, contradictions, and next steps.
3. Put unresolved contradictions in the digest as explicit open questions.
4. Close the branch through the return primitive:
   `~/.relay/bin/relay return <branch-id-or-query> --digest "<digest>"`
5. Add `--parent <parent-id>` only when the branch has ambiguous parent refs.
6. If the parent is live, inject the digest into the current context. If parked, rely on the parent's next resume to surface the closed branch through refs.

## continue

Use this when the user wants the same topic in a clean session, not a side exploration.

1. Resolve the parent conversation id or query.
2. Create the new active continuation:
   `~/.relay/bin/relay continue <parent-id-or-query>`
3. If the user provides a clean topic, pass it explicitly:
   `~/.relay/bin/relay continue <parent-id-or-query> --topic "<topic>"`
4. Add `--id <new-id>` only for scripted stable ids.
5. Let the CLI park the parent, carry forward recovery sections, add reverse refs, and rebuild the index.
