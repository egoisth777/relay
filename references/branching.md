# conv branching

Use this for `conv:sidekick`, `conv:return`, and `conv:continue`.

## sidekick

1. If the branch topic is not specified, ask what to sidekick.
2. Pick the mode from intent:
   - `probe`: use a subagent, return a digest into the parent only, and do not create a file unless the user explicitly opts in.
   - `sidekick`: default. Create a peer conversation with `status = "active"` and ref `{ "id": "<parent>", "rel": "spawned-from" }`.
   - Same-session organize branches are non-protecting convenience only; do not use as default.
3. Park the parent if needed:
   `python .conversate/scripts/conv_cli.py set-status <parent-id> parked`
4. Create the sidekick conversation with `upsert --stdin`. Include parent summary/decisions as sources/context, not as mutable branch decisions.

## return

1. Resolve the branch and parent via refs.
2. Generate a digest covering what was explored, conclusions, useful files/patterns, contradictions, and next steps.
3. Update the branch body with `## digest` and put unresolved contradictions in `## qa` as `Q (open)`.
4. Set the branch closed:
   `python .conversate/scripts/conv_cli.py set-status <branch-id> closed`
5. Ensure refs are repaired:
   `python .conversate/scripts/conv_cli.py regen-refs`
6. If the parent is live, inject the digest into the current context. If parked, rely on the parent's next resume to surface the closed branch through refs.

## continue

Use this when the user wants the same topic in a clean session, not a side exploration.

1. Save and park the current conversation.
2. Create a new conversation with ref `{ "id": "<parent>", "rel": "continued-from" }`.
3. Seed the new conversation from the parent's `## dict`, `## resume`, `## qa`, sources,
   insights, and decisions, carrying a clear continued-from marker.
4. Let the CLI add the reverse `continued-as` ref and rebuild the index.
