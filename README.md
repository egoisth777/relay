# Conversate

Conversate is an **agent-agnostic conversation recorder**. It persists topic-bound
conversations as distilled, resumable records — not transcripts — so that headspace
survives across sessions, context windows, and even across different agents.

A record captures the *mental model*: the agreed language, the resumption plan, the
standing user instructions, and a condensed exchange log — written so a cold agent (any
agent) can pick the thread back up without replaying the chat.

## Runtime Path Contract

Plugin source is this repo. The universal installation root (the installer `--target`,
also reported as the Plugin installation root) defaults to `~/.conversate/`. The
canonical installed plugin root is `~/.conversate/conversate/`; the prior `~/.conversate/conv/` is a legacy root the installer migrates from and removes on reinstall. The canonical hook root is
`~/.conversate/hooks/`. The Conversation database is `~/.conversate/convs/` and is the
source of truth for saved conversation records:

```
~/.conversate/
├── .gitignore      # ignores derived artifacts; records stay trackable
├── conversate/     # canonical installed Conversate plugin root (legacy: conv/)
│   ├── SKILL.md
│   ├── .claude-plugin/
│   ├── .codex-plugin/
│   └── skills/
├── convs/          # Conversation database: *.md records, source of truth
│   └── YYYY-MM-DD_<slug>.md
├── index.jsonl     # derived cache (rebuildable), one record per line
├── .semble/        # semantic-search cache
├── references/     # installed reference playbooks
├── hooks/          # canonical hook implementations
└── scripts/
    └── conv_cli.py # installed CLI
```

Records (`convs/`) are trackable in git; the index and caches are ignored.

## Source layout

The repository root **is** the plugin root (the canonical Claude Code layout):
the tree that ships the skills is the tree that gets packaged. There is no
nested legacy source tree — every component ships from the root.

```
/ (repo root == plugin root)
├── .claude-plugin/
│   ├── plugin.json              # plugin manifest
│   └── marketplace.json         # single-plugin marketplace (source ".")
├── .codex-plugin/
│   └── plugin.json              # Codex manifest
├── SKILL.md                     # plugin entrypoint (name: conversate)
├── skills/
│   ├── conversate/SKILL.md      # base skill (name: conversate)
│   └── {save,resume,list,park,sidekick,return,continue,regen}/SKILL.md  # eight verb skills
├── hooks/                       # canonical per-host hook sources (see hooks/README.md)
│   ├── claude/ codex/ pi/
│   └── README.md
├── references/                  # playbooks: save, resume, list, branching, cli
├── scripts/                     # conv_cli.py + install.py
├── tests/                       # pytest suite
├── tools/profiler/              # loading profiler + runtime budgets
├── README.md  LICENSE  .gitignore
```

### Native plugin install vs `install.py`

Conversate can be added as a **native Claude Code marketplace plugin** via
`.claude-plugin/marketplace.json` (a single-plugin marketplace whose source is
`.`). A native install registers the **skills only**: it does not wire the
auto-save turn-counter hooks, and it does not create the installed CLI or the
Conversation database that those skills delegate to.

The auto-save hooks are **installer-managed and per-host, not plugin-native**.
There is intentionally no `hooks/hooks.json` using `${CLAUDE_PLUGIN_ROOT}` at the
plugin root. `scripts/install.py` is what wires the per-host hook scripts
(`hooks/claude/`, `hooks/codex/`, `hooks/pi/`) into each host's own config
(`~/.claude/settings.json`, `~/.codex/hooks.json`, `~/.pi/agent/extensions/`,
`<target>/.omp/hooks/pre/`) and provisions the CLI + Conversation database the
hooks depend on. This is a deliberate deviation from the superpowers plugin
convention: Conversate's hooks must resolve to a real installation root, so they
are bound by the installer rather than by the plugin loader. See
`hooks/README.md` for the per-host wiring details.

## Supported agents

Conversate is packaged as a plugin whose identifier is `conversate`, with a group of skills backed by one
shared Conversation database. The Conversate plugin skill group is installed once at
`~/.conversate/conversate/`. Each real agent config surface, such as `~/.codex/` or
`~/.claude/`, holds scan entrypoints or hook config that point back to
`~/.conversate/conversate/` and `~/.conversate/hooks/`. Every skill delegates writes to the
installed CLI:
`python ~/.conversate/scripts/conv_cli.py`.

## Installation

Conversate installs runtime files under the universal installation root, installs the
Conversate plugin skill group (identifier `conversate`) at the canonical installed plugin root, and installs hook
implementations at the canonical hook root. From Plugin source:

    python scripts/install.py

This copies plugin files into `~/.conversate/`, initializes the Conversation database at
`~/.conversate/convs/`, and wires real agent config/link surfaces back to the canonical
roots:

| Runtime surface | Role |
|-----------------|------|
| `~/.conversate/conversate/` | Canonical installed plugin root |
| `~/.conversate/hooks/` | Canonical hook root |
| `~/.codex/skills/conversate` | Codex scan entrypoint resolving to the canonical plugin |
| `~/.claude/skills/conversate` | Claude Code scan entrypoint resolving to the canonical plugin |
| `~/.codex/hooks.json` | Codex hook config pointing to the canonical hook root |
| `~/.claude/settings.json` | Claude hook config pointing to the canonical hook root |
| `~/.pi/agent/extensions/conv-turn-counter.ts` | pi extension hook pointing to the canonical hook implementation |

The installed plugin contains the base `conversate` skill plus the verbs `save`,
`resume`, `list`, `park`, `sidekick`, `return`, `continue`, and `regen`. Agent-visible
plugin skills route back to the installed reference playbooks and CLI under the Plugin
installation root.

### Options

    python scripts/install.py [--target DIR] [--source DIR]
          [--agents claude,pi,omp,codex|all] [--hooks claude,pi,omp,codex|all|none]
          [--update] [--repair|--doctor-fix] [--force] [--uninstall] [--status] [--dry-run]

- `--target` - explicit Plugin installation root; default is `~/.conversate/`.
- `--agents` (default `all`) - which real agent scan surfaces receive entrypoints to the canonical Conversate plugin (`conversate`).
- `--hooks` (default `none`) - install per-agent auto-save turn-counter hooks (see `hooks/README.md`); without it the installer prints wiring instructions.
- `--update` - refresh canonical plugin and hook files in place and remove stale installer-owned artifacts; never touches `convs/`, `index.jsonl`, or `.semble/`.
- `--repair` / `--doctor-fix` - explicit lifecycle repair path. It refreshes installer-owned plugin files, prunes stale/cache artifacts, initializes missing lifecycle files, and rewires selected hooks. With no `--hooks`, it rewrites already-wired hooks; pass `--hooks codex` or `--hooks all` to restore missing hook wiring.
- `--force` - overwrite differing plugin files and replace foreign plugin dirs (backs up first).
- `--status` - report install state for the target.
- `--uninstall` - remove installer-owned plugin entrypoints, legacy installer-created skill links, and installer-wired hooks. The Conversation database under `~/.conversate/convs/` is never touched.
- `--dry-run` - print planned actions, change nothing.

Re-running is idempotent. The installer refuses to install into the Conversate
checkout itself unless you pass an explicit `--target`.

## Plugin skills

The installer registers the same Conversate plugin skill group (identifier `conversate`) for Claude Code, pi,
oh-my-pi, and Codex. Codex metadata points at the same `./skills/` directory and
names the same visible verb inventory. The verb skills are thin wrappers that delegate to the shared
Conversation database and the installed `~/.conversate/references/*.md` playbooks:
`/conversate:save`, `/conversate:resume`, `/conversate:list`, `/conversate:park`, `/conversate:sidekick`,
`/conversate:return`, `/conversate:continue`, and `/conversate:regen`.

Claude Code must be launched from the project root for a project-scope plugin copy, and
after install you must run `/reload-plugins` (or restart Claude Code) before the Conversate
skills (the `/conversate:*` commands) appear. Project `@skills-dir` plugins do not walk up from subdirectories.

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
# initialize the Plugin installation root and Conversation database
python ~/.conversate/scripts/conv_cli.py init

# save a conversation (pipe conversation JSON; see references/save.md for the shape)
python ~/.conversate/scripts/conv_cli.py upsert --stdin < conversation.json

# find and resume
python ~/.conversate/scripts/conv_cli.py search "auth redesign"
python ~/.conversate/scripts/conv_cli.py show <id> --markdown
python ~/.conversate/scripts/conv_cli.py set-status <id> active

# list, repair, diagnose
python ~/.conversate/scripts/conv_cli.py list --limit 10
python ~/.conversate/scripts/conv_cli.py regen-refs
python ~/.conversate/scripts/conv_cli.py doctor
```

To operate on a non-default compatibility root, pass `--conv-root PATH` explicitly. That
is not the normal plugin model. Full resolution rules, commands, and the conversation
JSON shape are in `references/cli.md`.

## Requirements

- **Python 3.11+** (the engine uses `tomllib`). No third-party Python dependencies.
- Optional external tools improve search: `rg`, `fff`, `semble` (or `uvx semble`). Absent
  those, the CLI degrades gracefully to a built-in body scorer.

The CLI (`scripts/conv_cli.py`) is a single dependency-free file and owns every
Conversation database mutation. See `.arca/space/conversate-sp/what/` for the
architecture, file manifest, and flow diagrams.

## Notes

- **Never place an `AGENTS.md` inside the Plugin installation root.** pi mis-detects
  `AGENTS.md` files inside skill dirs as skills (pi issue #2473), so keep
  `~/.conversate/` clear of agent instruction files.
