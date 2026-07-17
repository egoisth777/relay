# Relay ubiquitous language

- **Relay** — the project, plugin, skills, and CLI.
- **Plugin installation root** — the resolved runtime directory, normally `~/.relay/`.
- **Relay archive** — recursive `convs/` below the installation root; source of truth.
- **Handoff record** — Markdown plus TOML frontmatter in the Relay archive.
- **RELAY HANDOFF** — hook reminder that triggers a save.
- **scan engine** — deterministic bounded-worker recursive snapshot/parser service.
- **index cache (index-v2)** — generation-published derived rows and postings.
- **fingerprint** — FNV-1a content identity stored on an index-v2 row.
- **postings** — random-access trigram-to-record-id search blocks.
- **context pack** — reconstruction-ordered output of `relay context`.
- **checkpoint** — completed resume milestone that a cold agent must not redo.
- **transcript weight** — durable 1–3 importance attached to a condensed exchange.
- **transaction journal** — authoritative in-flight record after-images at
  `.semble/txn.pending`.
