# Relay development guidance

This page is explanatory guidance only. The sole authority for agent process, routing, lifecycle, and rule precedence is [`../../index.md`](../../index.md); delivered Relay behavior is authoritative in [`../../current/`](../../current/). This page cannot override either authority.

## Source and runtime

- The checkout is Relay source: the Rust CLI (`../../../src/`), installer (`../../../scripts/install.py`), playbooks, and skills. It is not a runtime root.
- Runtime operations use the Plugin installation root, defaulting to `~/.relay/` (or an explicit `--relay-root PATH`); discovery and storage must not use the checkout or current working directory. See the linked [`architecture.md`](../relay-sp/what/architecture.md) authority.
- Root [`AGENTS.md`](../../../AGENTS.md) and [`CLAUDE.md`](../../../CLAUDE.md) are source-only entry-point wrappers. Do not copy or install them (or this wiki) into `~/.relay/`, where an agent harness may load files as runtime skills.

## Archive and cache ownership

The installed Rust CLI (`~/.relay/bin/relay` or `relay.exe`) owns every mutation of the Relay archive and derived cache. Never write, edit, or delete `~/.relay/convs/**/*.md`, `~/.relay/index.jsonl`, or Semble cache artifacts directly; invoke the CLI. The current artifact ownership and recovery model is authoritative in [`architecture.md`](../relay-sp/what/architecture.md), [`manifest.md`](../relay-sp/what/manifest.md), and [`flows.md`](../relay-sp/what/flows.md).

The legacy `~/.conversate/` directory is a read-only compatibility/import source. Do not mutate it; carry records forward only through `relay import --from ~/.conversate`. The product manifest states the same protection and is the authority for runtime behavior.

## Terminology and routing

Use the process vocabulary in [`../../ubi-lang.md`](../../ubi-lang.md) and the product vocabulary in [`../../current/ubi-lang.md`](../../current/ubi-lang.md). Route working rules, issue lifecycle, exact Arca shapes, and precedence through [`../../index.md`](../../index.md). Route shipped behavior through the exact [`current` bundle](../../current/index.md), especially its [`spec.md`](../../current/spec.md), [`design.md`](../../current/design.md), and [`test-list.md`](../../current/test-list.md); link the [`Relay product authorities`](../relay-sp/what/architecture.md) rather than copying their detail.

## Verification commands

For code changes, use the repository's established checks: `cargo test` for Rust and `python -m pytest` (for example, `python -m pytest tests/test_install.py`) for installer integration. Documentation-only Arca migrations do not require either suite; use the migration-specific shape, link, ignore-rule, and diff checks described by the active issue instead.
Code changes must reconcile affected `.arca/current/` product-authority records with affected facts in `.arca/space/relay-sp/what/architecture.md`, `.arca/space/relay-sp/what/manifest.md`, and `.arca/space/relay-sp/what/flows.md`; update those authorities when behavior changes.

## Knowledge authorities

- Arca process and precedence: [`../../index.md`](../../index.md)
- Process terms: [`../../ubi-lang.md`](../../ubi-lang.md)
- Delivered product: [`../../current/index.md`](../../current/index.md), [`../../current/spec.md`](../../current/spec.md), [`../../current/design.md`](../../current/design.md), [`../../current/test-list.md`](../../current/test-list.md)
- Relay product facts: [`architecture.md`](../relay-sp/what/architecture.md), [`manifest.md`](../relay-sp/what/manifest.md), [`flows.md`](../relay-sp/what/flows.md)
- Host hook integration: [`relay-hooks.md`](relay-hooks.md) and [`hooks/README.md`](../../../hooks/README.md)
