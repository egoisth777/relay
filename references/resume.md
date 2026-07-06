# conv resume

Use this for `conv:resume` and natural-language requests to resume or continue a previous discussion.

## Resolve Target

1. Run:
   `python ~/.conversate/scripts/conv_cli.py search "<user query>"`
2. If exactly one confident hit returns, use it.
3. If multiple hits return, present the ranked ids/topics and ask the user to choose.
4. Show the resolved conversation:
   `python ~/.conversate/scripts/conv_cli.py show <id> --markdown`

The CLI implements the tiered cascade as filename/path match, `rg` over `index.jsonl`,
then Semble over Conversation database bodies under `~/.conversate/convs/`. It uses
installed `semble` automatically, can use `uvx semble` when `CONV_USE_UVX_SEMBLE=1`, and
otherwise falls back to built-in body scoring. If `fff` is available, prefer it manually
for the filename layer; keep the same short-circuit behavior.

## Reconstruction Order

Every record is a resumption point. Read and internalize in this order:

1. `## summary`: orientation — what this conversation is.
2. `## dict`: ubiquitous language — the agreed terms, first.
3. `## user-instructions`: **adopt these as standing behavior** for the resumed session
   (constraints, workflow preferences, tone the user set).
4. `## resume`: the plan. Note the `goal`, then **act on `next-steps`**, keep
   `open-questions` live, and invoke the listed `suggested-skills`.
5. `## qa`: the spine of the conversation; treat `Q (open)` entries as live threads.
6. `## condensed-transcript`: deep context — the chronological exchange log, when you need
   more than the summary and qa give.
7. `## decisions`: settled items. Do not relitigate them unless the user asks.
8. `## insights` and `## sources`: realizations, plus files/skills to read only as the
   resumed task needs them.
9. Linked conversations through frontmatter refs. Surface useful branch digests from
   closed peers.

Also read frontmatter (identity, status, tags, refs) up front. After loading, mark the
conversation active:

`python ~/.conversate/scripts/conv_cli.py set-status <id> active`

Then present a short summary and the open threads.
