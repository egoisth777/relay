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
2. `## dict`: ubiquitous language — the agreed terms, first.
3. `## user-instructions`: **adopt these as standing behavior** for the resumed session
   (constraints, workflow preferences, tone the user set).
4. `## resume`: the plan. Note the `goal`, then **act on `next-steps`**, keep
   `open-questions` live, and invoke the listed `suggested-skills`.
5. `## qa`: the spine of the conversation; treat `Q (open)` entries as live threads.
6. `## decisions`, `## environment`, and `## artifacts`: settled choices and
   reference-only execution state. Do not relitigate decisions unless asked.
7. `## condensed-transcript`: deep context in chronological order; budget trimming
   preserves load-bearing weight-3 exchanges longest.
8. `## insights` and `## sources`: realizations, plus files/skills to read only as the
   resumed task needs them.
9. Linked conversations through frontmatter refs. Surface useful branch digests from
   closed peers.

The context pack includes frontmatter, one-hop closed-branch digests, warnings for
unavailable links, and an unambiguous `next action argv` for marking the record active.
Execute that argv through the harness without re-quoting it as a shell string.

Then present a short summary and the open threads.
