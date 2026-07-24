# Relay ubiquitous language

| Term | Meaning |
| :--- | :--- |
| **Relay** | The project, plugin, skills, and CLI. |
| **Plugin installation root** | The resolved runtime directory, normally `~/.relay/`. |
| **Relay archive** | The recursive `convs/` tree below the installation root; source of truth. Its canonical machine-facing spelling is `relay_archive`; `conversation_database` is a deprecated compatibility alias only. |
| **Handoff record** | Markdown plus TOML frontmatter in the Relay archive. |
| **record glossary** | The per-record agreed-vocabulary section in a handoff record; its canonical machine spelling is `glossary`; `dict` is a deprecated input alias accepted indefinitely and never emitted. This is distinct from the project-level Arca glossary. |
| **RELAY HANDOFF** | The hook reminder that triggers a save. |
| **scan engine** | A deterministic bounded-worker recursive snapshot/parser service. |
| **index cache (index-v2)** | Generation-published derived rows and postings. |
| **fingerprint** | FNV-1a content identity stored on an index-v2 row. |
| **postings** | Random-access trigram-to-record-id search blocks. |
| **context pack** | Reconstruction-ordered output of `relay context`. |
| **checkpoint** | A completed resume milestone that a cold agent must not redo. |
| **transcript weight** | Durable 1–3 importance attached to a condensed exchange. |
| **transaction journal** | Authoritative in-flight record after-images at `.semble/txn.pending`. |
