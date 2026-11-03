# Measured context fidelity

```yaml
issue-id: i-002-measured-context-fidelity
provenance: user-requested elevation of the 2026-07-24 Designer synthesis from the high-fidelity context-handoff and active agent-memory research
status: integrated
```

## Summary

Relay needs measured evidence before changing context compression or reconstruction. This issue seeds a fidelity harness that measures compression efficiency, exact critical-fact recovery, fresh-session continuation, provenance, safety, truncation honesty, source recovery, and loss attribution across realistic budgets and at least two configured providers. Every measured case runs against a disposable Relay root so the source installation is never altered.

The harness must distinguish capture loss—a fact absent from the durable record—from trim loss—a recorded fact omitted from the context pack. If every decisive gate passes, Relay keeps the harness as a regression gate and stops feature work. If any decisive gate fails, the measured gap is recorded and every remedy must enter through a separate P1 issue; this issue does not pre-authorize context-output, record-schema, doctor, image, search, or storage changes.

The source synthesis remains in [Designer next steps](../../../research/high-fidelity-context-handoff/next-steps.md), as required by the research-study shape. This five-file bundle is the canonical issue representation; no extra `seed.md` is added because Relay issue folders have an exact shape.

## Routes

| Need | File |
| :--- | :--- |
| Issue terms | [Ubiquitous language](packs/projs/skills/relay/.arca/issue/archive/i-002-measured-context-fidelity/ubi-lang.md) |
| Requirements and decisions | [Specification](spec.md) |
| Proposed mechanics | [Design](design.md) |
| Verification and integration traces | [Test plan](test-plan.md) |
