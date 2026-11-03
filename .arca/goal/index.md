# Relay goal bundle

This is the active Relay target bundle for goal revision `g-001`. It starts as a byte copy of the delivered bundle and adds the requirements folded from issues `i-001-lossless-hook-increments` and `i-002-measured-context-fidelity`. `.arca/current/` remains the delivered authority until promotion.

## Routes

| Need | File |
| :--- | :--- |
| Product vocabulary | [Ubiquitous language](packs/projs/skills/relay/.arca/goal/ubi-lang.md) |
| Required behavior | [Specification](spec.md) |
| Conforming mechanics | [Design](design.md) |
| Verification | [Test list](test-list.md) |
| Delivered authority | [Current bundle](../current/index.md) |
| Current state | [Current status](../current/current.md) |
| History | [Append-only log](../current/log.md) |
| Detailed architecture | [Architecture authority](../space/relay-sp/what/architecture.md) |
| Artifact ownership | [Artifact manifest](../space/relay-sp/what/manifest.md) |
| Operational flows | [Relay flows](../space/relay-sp/what/flows.md) |

## Folded issues

| Issue | Outcome | Requirements routed here |
| :--- | :--- | :--- |
| [i-001-lossless-hook-increments](../issue/archive/i-001-lossless-hook-increments/index.md) | integrated | HOOK-INC-001 through HOOK-INC-006 |
| [i-002-measured-context-fidelity](../issue/archive/i-002-measured-context-fidelity/index.md) | integrated | HFC-001 through HFC-007 |

The three linked space documents remain the detailed Relay product authorities. This bundle routes to them and does not duplicate their content.
