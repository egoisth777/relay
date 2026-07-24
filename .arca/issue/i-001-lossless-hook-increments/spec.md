# Issue specification

## Requirement records

| Requirement ID | Requirement | Disposition | Rationale | Accepted forward authority refs |
| :--- | :--- | :--- | :--- | :--- |
| HOOK-INC-001 | Every valid `UserPromptSubmit` with a non-empty session must contribute exactly one durable increment, even when any valid number of invocations contend for the same session lock. | accepted | The observed regression loses submissions under contention; correctness cannot depend on a particular worker count or scheduler timing. | none yet; P1 must route this requirement into the active goal |
| HOOK-INC-002 | For each committed valid update whose resulting count is divisible by 10, the production hook emits the reminder exactly once; no reminder is emitted for other committed counts. Coverage varies initial counts and submission ranges across several 10n boundaries rather than configuring a threshold. | accepted | A dropped increment also drops a reminder, while the runtime's reminder rule is fixed at every tenth turn. | none yet; P1 must route this requirement into the active goal |
| HOOK-INC-003 | Lock and persistence outcomes have an explicit observable result and recovery contract. A known failure before replacement preserves the previously published counter and is distinguishable from filtered input; a replacement followed by failed durability synchronization is reported as uncertain, and callers must reconcile before any retry. No terminal failure is swallowed as a successful no-op. | accepted | Atomic replacement can make the new value visible before parent-directory synchronization reports an error, so all failures cannot be treated as preserving prior state. | none yet; P1 must route this requirement into the active goal |
| HOOK-INC-004 | Counter state remains session-isolated and durable: committed updates survive the atomic replacement path, known pre-replacement failures do not corrupt or silently reset a prior valid counter, and post-replacement uncertainty remains observable until recovery reconciles it. | accepted | Per-session files and [`write_atomic`](../../../src/atomic_io.rs) are the persistence boundary; its replacement and parent-sync stages have different observable outcomes. | none yet; P1 must route this requirement into the active goal |
| HOOK-INC-005 | The behavior is deterministic and valid across supported Unix and Windows lock, rename, and filesystem behavior; it must not rely on a magic retry count or timing accident, and it must be exercised through the installed production hook path. | accepted | The runtime uses platform-specific file operations, including Windows replacement retries, so platform behavior is a delivery risk rather than an implementation detail to ignore. | none yet; P1 must route this requirement into the active goal |
| HOOK-INC-006 | Regression coverage varies valid contention and tenth-turn boundary parameters, uses representative sessions with distinct production storage identities, and retains deterministic proof that concurrent increments and reminders are lossless without test-only behavioral paths. | accepted | The existing regression exposes the defect; boundary variation, scoped multi-session interference, installed-path coverage, and production-path failure injection establish the relevant contract without expanding into hash redesign. | none yet; P1 must route this requirement into the active goal |

Requirement dispositions above are intake decisions only. They do not change the `pending` issue status in `index.md`; status changes only when the issue is folded and integrated or explicitly rejected under the Arca lifecycle.

## Acceptance criteria

- A generated set of valid concurrent submissions produces one durable increment per submission; the assertion derives the expected total from the generated set rather than embedding a special worker count or counter value.
- For initial counts and submission ranges on both sides of several multiples of 10, each committed resulting count divisible by 10 produces exactly one reminder, and no other committed count produces one.
- Multi-session checks use representative session identifiers whose production counter and lock paths are distinct; one session's lock or failure cannot change another's result. Hash-collision redesign, including collision-proof session hashing, is out of scope.
- Lock acquisition and persistence failures are classified and observable. Recoverable contention eventually completes without loss; a known pre-replacement failure preserves the prior published state and is retryable only under its explicit contract; post-replacement durability failure is reported as uncertain and is not blindly retried.
- Atomic persistence remains intact: successful writes leave a parseable counter, known failures before replacement leave the prior published counter intact, and post-replacement sync failure does not claim that the prior state survived. No temporary artifact is treated as the counter.
- Unix and Windows-specific lock, replacement, and durability behavior are covered without weakening the common contract for either platform.
- The existing regression remains enabled, and installed/production hook-path checks fail on lost increments, missing or duplicate reminders, swallowed or misclassified failures, cross-session interference, or persistence corruption.

## Constraints and non-goals

- Do not weaken, remove, skip, or serialize only the regression test.
- Do not inflate a magic retry count, add an arbitrary sleep, or make correctness depend on a fixed concurrency level.
- Do not hardcode an expected count or reminder result as the implementation fix.
- Do not swallow lock acquisition, read, parse, overflow, replacement, or durability-sync failures; invalid input and operational failure must not be conflated. A post-replacement sync failure MUST remain an explicit uncertain outcome, not a claim that the prior state survived.
- Preserve the existing valid-input filtering and per-session isolation unless a requirement above demonstrates a necessary contract correction; use distinct production storage identities for multi-session interference coverage.
- Do not add `cfg(test)` behavior, fake counter or lock paths, test-only counters, or any other test-only behavioral divergence. Fault injection is permitted only through seams that exercise the same installed production path and production persistence protocol.
- This issue records incoming requirements; it is not delivered product authority until accepted through P1 and promoted through the Arca lifecycle.
