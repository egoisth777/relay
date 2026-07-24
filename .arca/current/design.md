# Relay delivered design

Relay keeps Markdown records as source of truth and treats indexes as replaceable derived state. The implementation follows the preserved detailed authorities rather than introducing a second design here.

## Conformance map

| Concern | Detailed authority |
| :--- | :--- |
| Runtime root, scan engine, cache generations, fidelity | [Architecture](../space/relay-sp/what/architecture.md) |
| Source/derived/coordination ownership and compatibility fields | [Artifact manifest](../space/relay-sp/what/manifest.md) |
| Warm reads, search tiers, mutation journal, resume/context, repair | [Operational flows](../space/relay-sp/what/flows.md) |

The Rust CLI owns all archive mutations and recovery. Readers use one fresh snapshot. Writers hold the shared lock, publish a complete transaction journal before record replacement, publish derived cache artifacts in order, and make the manifest the final commit point. Compatibility aliases are accepted only where the manifest specifies them; new output uses canonical names.
