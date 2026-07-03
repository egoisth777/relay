# conversate

conversate is an **agent-agnostic conversation recorder**. It persists topic-bound
conversations as distilled, resumable records — not transcripts — so that headspace
survives across sessions, context windows, and even across different agents.

A record captures the *mental model*: the agreed language, the resumption plan, the
standing user instructions, and a condensed exchange log — written so a cold agent (any
agent) can pick the thread back up without replaying the chat.

## The `.conversate/` store

The store is a self-contained directory installed per project. The conv root **is** the
`.conversate/` directory:

```
.conversate/
├── .conv-root      # sentinel marking the store root
├── .gitignore      # ignores derived artifacts; records stay tracked
├── convs/          # *.md conversation records — source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl     # derived cache (rebuildable), one record per line
├── .semble/        # semantic-search cache
└── scripts/
    └── conv_cli.py # the engine
```

Records (`convs/`) are trackable in git; the index and caches are ignored.

## Supported agents

conversate is packaged as a skill (`SKILL.md`, skill name `conversate`) read by four
harnesses — Claude Code, pi, oh-my-pi (omp), and Codex. Each reaches the shared store
through a symlinked `conversate` skill directory (the name matches SKILL.md `name:`), so
the CLI is invoked at the same path in every harness:
`python .conversate/scripts/conv_cli.py`. See **Installation** for the exact links.

## Installation

conversate installs into a project as a self-contained `.conversate/` directory and
links itself into each agent's skill-discovery path. From a conversate checkout:

    python scripts/install.py --target /path/to/your/project

This copies the skill payload into `<project>/.conversate/`, initializes the
conversation store, and creates two symlinks that cover all four supported agents:

| Link | Consumed by |
|------|-------------|
| `<project>/.claude/skills/conversate` -> `.conversate` | Claude Code (oh-my-pi also reads this) |
| `<project>/.agents/skills/conversate` -> `.conversate` | pi, oh-my-pi, Codex |

On Windows the installer prefers a real symlink (needs Developer Mode or an elevated
shell), falls back to an NTFS junction (no privileges), then to a copy of the skill
payload with a loud warning.

### Options

    python scripts/install.py [--target DIR] [--source DIR]
          [--agents claude,pi,omp,codex|all] [--hooks claude,pi,omp,codex|all|none]
          [--update] [--force] [--uninstall] [--status] [--dry-run]

- `--agents` (default `all`) - which agents to link; deduped to the two links above.
- `--hooks` (default `none`) - install per-agent auto-save turn-counter hooks (see `hooks/README.md`); without it the installer prints wiring instructions.
- `--update` - refresh the skill payload in place; never touches `convs/`, `index.jsonl`, `.semble/`, or `.conv-root`.
- `--force` - overwrite differing payload files and replace foreign links (backs up first).
- `--status` - report install state for the target.
- `--uninstall` - remove the skill links and installer-wired hooks. Conversation data under `.conversate/convs/` is never touched.
- `--dry-run` - print planned actions, change nothing.

Re-running is idempotent. The installer refuses to install into the conversate
checkout itself unless you pass an explicit `--target`.

## Record format

Each record is Markdown with thin TOML frontmatter (`id, topic, status, tags, refs,
created, updated`) delimited by `+++`, followed by body sections in a fixed order:

- `## summary` — one-line orientation *(required)*
- `## dict` — the agreed/coined language, highest-value, reconstructed first *(required)*
- `## qa` — the question/answer spine; `Q (open)` marks live threads *(required)*
- `## resume` — the resumption plan: goal, next-steps, open-questions, suggested-skills
- `## user-instructions` — standing directives a fresh agent must keep honoring
- `## condensed-transcript` — compressed chronological exchange log for deep context
- `## sources`, `## insights`, `## decisions`, `## digest` — optional

`summary`/`dict`/`qa` are mandatory; the three resumption sections always appear (empty
ones render `(none)`). Optional sections are omitted when empty.

## CLI quickstart

```bash
# initialize the store (installation already does this; init is idempotent)
python .conversate/scripts/conv_cli.py init

# save a conversation (pipe the JSON payload; see references/save.md for the shape)
python .conversate/scripts/conv_cli.py upsert --stdin < payload.json

# find and resume
python .conversate/scripts/conv_cli.py search "auth redesign"
python .conversate/scripts/conv_cli.py show <id> --markdown
python .conversate/scripts/conv_cli.py set-status <id> active

# list, repair, diagnose
python .conversate/scripts/conv_cli.py list --limit 10
python .conversate/scripts/conv_cli.py regen-refs
python .conversate/scripts/conv_cli.py doctor
```

To point the store elsewhere (a shared vault, a personal brain), set `$CONVERSATE_ROOT`
(or the legacy `$BRAIN_CONV`), or pass `--conv-root PATH`. Full resolution rules,
commands, and the payload schema are in `references/cli.md`.

## Requirements

- **Python 3.11+** (the engine uses `tomllib`). No third-party Python dependencies.
- Optional external tools improve search: `rg`, `fff`, `semble` (or `uvx semble`). Absent
  those, the CLI degrades gracefully to a built-in body scorer.

The engine (`scripts/conv_cli.py`) is a single dependency-free file and owns every store
mutation. See `.arca/space/conversate-sp/what/` for the architecture, file manifest, and
flow diagrams.

## Notes

- **Never place an `AGENTS.md` inside `.conversate/`.** pi mis-detects `AGENTS.md` files
  inside skill dirs as skills (pi issue #2473), so keep the store payload clean of them.
