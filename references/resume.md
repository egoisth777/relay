# Relay resume

Use this for `relay:resume` and natural-language requests to resume or continue a previous discussion.

## Resolve Target

1. Run:
   `~/.relay/bin/relay search "<user query>"`
2. If exactly one confident hit returns, use it.
3. If multiple hits return, present the ranked ids/topics and ask the user to choose.
4. Build the reconstruction-ordered context pack:
   `~/.relay/bin/relay context <id> --budget-tokens <harness-budget>`
   Omit the budget only when the harness has no useful context cap. Use `--json` for
   structured consumers and `--no-refs` only when linked branch context is unwanted.

The CLI uses exact id/path lookup, indexed metadata postings, optional Semble, and a
parallel body fallback. `--no-semble` bypasses the external semantic tier completely.

## Reconstruction Order

Every record is a resumption point. Read and internalize in this order:

1. `## summary`: orientation — what this conversation is.
2. `## glossary`: record glossary — adopt the agreed terms before acting.
3. `## user-instructions`: **adopt these as standing behavior** for the resumed session
   (constraints, workflow preferences, tone the user set).
4. `## resume`: the plan. Note the `goal`, then **act on `next-steps`**, keep
   `open-questions` live, and invoke the listed `suggested-skills`.
5. `## qa`: the spine of the conversation; treat `Q (open)` entries as live threads.
6. `## decisions`, `## environment`, `## artifacts`, `## sources`, and `## insights`:
   settled choices and reference-only execution state. When present, sources and
   insights arrive in the context pack; read referenced files only as the resumed task
   needs them. Do not relitigate decisions unless asked.
7. `## condensed-transcript`: deep context in chronological order; budget trimming
   preserves load-bearing weight-3 exchanges longest.
8. Linked conversations through frontmatter refs: surface useful branch digests from
   closed peers.
9. `next action argv`: execute it through the harness without re-quoting it as a shell
   string.
10. `truncated` flag: note whether budget trimming removed context.

The context pack starts with the `relay context pack v2` banner and includes fixed
sections `summary, glossary, user-instructions, resume, qa`; optional sections
`decisions, environment, artifacts, sources, insights` when present; then transcript,
linked-context, action argv, and the truncated flag. It also includes frontmatter,
one-hop closed-branch digests, and warnings for unavailable links.

Then present a short summary and the open threads.
