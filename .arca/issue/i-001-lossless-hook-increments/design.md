# Issue design

## Proposed mechanics

### Evidence and root-cause area

[`process_input`](../../../src/hook_runtime.rs) accepts only a valid prompt-submit event and then uses `store.increment(&session)?`; `None` means both “no reminder” and “the increment did not happen.” [`CounterStore::increment`](../../../src/hook_runtime.rs) derives one lock and one counter path per session, calls `try_lock_exclusive`, reads the counter, computes the next value, and calls [`write_atomic`](../../../src/atomic_io.rs). The lock helper performs a bounded non-blocking loop: a waiter sleeps 5 ms between attempts and returns `WouldBlock` after the bound. The increment method turns that error, malformed/missing counter input, overflow, and write errors into `None`.

This explains the observed below-total counters and missing reminder: a contending invocation can exhaust the lock loop, return `None`, and be filtered out by the existing regression's `filter_map`. The source establishes the error-loss path; deterministic fault injection or scheduling control must confirm which failure occurs in each reproduction before implementation chooses a remedy. A second risk is that the current read/parse fallback to zero could reset an existing counter when state is malformed, so persistence/error semantics need to be made explicit rather than hidden.

### Implementation decision left open

The fix may use a blocking or fairly coordinated lock, a bounded retry with a defined recoverable-failure contract, or another atomic per-session update protocol. The issue intentionally does not select among those options. The selected design must:

1. keep one update linearization point per valid submission;
2. make the result of that update available to reminder calculation under the fixed every-tenth-turn rule;
3. distinguish retryable contention, known pre-replacement failure, and uncertain post-replacement durability failure;
4. preserve session isolation for representative distinct production storage identities and the atomic replacement guarantees already provided by `write_atomic`;
5. behave independently of scheduler timing, one chosen worker count, or a magic retry constant; and
6. run through the installed production hook path rather than a test-only substitute.

If the public hook entry point must remain quiet for invalid input, that policy must remain distinct from reporting an operational failure. An error channel, explicit result type, or equivalent mechanism is acceptable only if it makes those states testable and prevents a failed update from masquerading as a successful no-op.

### Update outcome and recovery contract

The update operation must expose one of these states to its caller:

- **committed:** counter replacement and the required durability synchronization both succeeded. The resulting count is consumed exactly once, and reminder calculation uses that count; a result divisible by 10 emits the fixed reminder.
- **failed-before-replacement:** the operation failed before attempting to replace the published counter (for example, lock/read/parse/overflow, temporary-file, write, or temporary-file sync failure). The prior published valid counter remains authoritative. The explicit failure may be retried only under a contract that knows no replacement occurred.
- **uncertain-after-replacement:** replacement completed but a later durability step, such as parent-directory synchronization, failed, or replacement status cannot be proven. The new value may already be visible, so the result is distinct from pre-replacement failure and MUST NOT be collapsed into success or prior-state preservation. The caller MUST NOT blindly retry. Recovery must reacquire the same session lock, inspect and reconcile production state using the attempted update's identity or equivalent, and either establish that the submission was consumed or report the unresolved uncertainty without issuing another increment.

The same outcome contract applies to injected failures. Fault injection may control production lock, replacement, and durability seams, but it must not replace the production counter path or introduce `cfg(test)` behavior.

### Deterministic verification seam

Tests should be able to hold a session lock, release it at a controlled point, and inject lock/read/replacement/durability failures without sleeping for an arbitrary race window. Contention tests must derive invocation and expected totals from parameters, exercise initial counts and submission ranges across multiple 10n boundaries, and include multiple sessions whose production counter and lock paths are distinct. The installed hook executable or equivalent production entry point must be exercised in addition to focused unit coverage. Fault injection may control production seams, but every assertion must traverse the real counter, lock, atomic replacement, and reminder paths; no `cfg(test)`, fake counter/lock path, or test-only behavior may stand in for them.
## Dependencies and risks

- The lock API and error model must be reconciled with the hook's current `Option<String>` result without changing unrelated event filtering.
- Atomic replacement has platform-specific behavior: Unix syncs the parent directory, while Windows uses `MoveFileExW` with write-through and bounded sharing-error retries. A hook fix must not assume Unix rename semantics on Windows or treat Windows timing as proof of correctness.
- Existing counter files may be absent, malformed, or concurrently observed after an interrupted write. The chosen policy must preserve valid prior state and report unrecoverable state rather than silently resetting it.
- A lock held by another process can outlive the operation's normal retry window. Tests must cover both eventual release and a terminal failure so that recovery does not become an unbounded hang and failure does not become data loss.
- Reminder output must be computed from the same committed resulting count as the counter result under the fixed every-tenth-turn rule; tests vary initial counts and submission ranges across 10n boundaries rather than introducing threshold configuration.

## Forbidden bypasses

The implementation must not weaken or remove `concurrent_increments_are_not_lost`, serialize only that test, hide lock or persistence errors, hardcode a counter or reminder expectation, inflate a magic retry count, replace a lost update with an unbounded sleep, add `cfg(test)` behavior, use fake counter/lock paths, or create test-only behavioral divergence. Any change to the test must increase parameterized coverage, use representative distinct storage identities, exercise the installed production path, and preserve its ability to fail on a dropped invocation. Fault injection is allowed only when it traverses the production seams.

This file is incoming evidence. Only the accepted goal and delivered bundle define Relay behavior.
