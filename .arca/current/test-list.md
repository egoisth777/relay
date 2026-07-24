# Relay delivered test list

This checklist points to the executable Rust and Python suites and to the preserved product authorities. A future goal must add contract-specific checks before implementation.

## Contract checks

| Check | Observable contract | Evidence source |
| :--- | :--- | :--- |
| T-ARCH | Runtime root and archive discovery never use the checkout or cwd; scan and cache behavior follows the architecture contract. | [Architecture](../space/relay-sp/what/architecture.md) and Rust tests |
| T-MAN | Source, derived, coordination, journal, and compatibility ownership match the manifest. | [Manifest](../space/relay-sp/what/manifest.md) and Rust tests |
| T-FLOW | Warm read, search-tier selection, mutation recovery, context trimming, and full repair follow the documented order. | [Flows](../space/relay-sp/what/flows.md) and Rust/Python tests |
| T-COMPAT | Canonical `relay_archive` output remains paired with deprecated `conversation_database`; glossary input alias remains accepted but is never emitted. | Manifest and integration tests |
| T-VERIFY | The repository suites remain runnable without changing runtime product code. | `cargo test`; `python -m pytest` |

## Required commands

```text
cargo test
python -m pytest
```

Failures must be investigated and reported; they are not converted into passing evidence by changing unrelated product code.
