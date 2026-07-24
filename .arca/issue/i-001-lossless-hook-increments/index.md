# Lossless concurrent hook increments and reminders

```yaml
issue-id: i-001-lossless-hook-increments
provenance: failed hook_runtime::tests::concurrent_increments_are_not_lost regression
status: pending
```

## Summary

The hook runtime's concurrent-increment regression is reproducibly losing work: concurrent `UserPromptSubmit` invocations for one session can finish with a persisted counter below the number of invocations, and the fixed tenth-turn reminder can be absent. Observed targeted runs reported totals such as 15 of 16 and 12 of 16. The test is not the source of the loss and must remain a regression proof.

Source inspection identifies the likely root-cause area. [`process_input`](../../../src/hook_runtime.rs) converts every failed `CounterStore::increment` result into `None`. [`CounterStore::increment`](../../../src/hook_runtime.rs) uses a per-session lock with a fixed 32-attempt, 5 ms `try_lock` loop, then silently drops lock, read/parse, overflow, and atomic-write errors. Under contention, a waiter can exhaust that bounded retry window, return `None`, and therefore create neither a count nor a reminder. The exact corrective mechanism remains open until confirmed by deterministic failure instrumentation.

The issue requires lossless counting and reminder semantics for every valid concurrency level and session combination under the production rule that each resulting count divisible by 10 emits one reminder. Tests must vary initial counts and submission ranges across 10n boundaries, while lock and persistence outcomes remain explicit. It must not turn the regression into a passing test by weakening the test, hiding failures, or bypassing the installed production path.

## Routes

| Need | File |
| :--- | :--- |
| Issue terms | [Ubiquitous language](ubi-lang.md) |
| Requirements and decisions | [Specification](spec.md) |
| Proposed mechanics | [Design](design.md) |
| Verification and integration traces | [Test plan](test-plan.md) |
