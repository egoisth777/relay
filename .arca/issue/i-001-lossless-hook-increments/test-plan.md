# Issue test plan

## Verification

| Check | Requirement refs | Expected evidence |
| :--- | :--- | :--- |
| HOOK-TEST-CONTENTION | HOOK-INC-001, HOOK-INC-006 | Deterministic contention over several valid invocation parameters completes one update per generated submission; the persisted total equals the generated submission count, with no timing-only sleeps or fixed expected count. Retain `concurrent_increments_are_not_lost` as a regression check. |
| HOOK-TEST-TENTH-TURNS | HOOK-INC-002, HOOK-INC-006 | With initial counts and submission ranges varied across several multiples of 10, each committed resulting count divisible by 10 yields exactly one reminder and no other committed count yields one. No configurable reminder threshold is introduced. |
| HOOK-TEST-SESSIONS | HOOK-INC-001, HOOK-INC-002, HOOK-INC-004 | Interleaved updates use representative session identifiers with distinct production counter and lock paths, producing independent durable counters and reminders; one session's lock or failure cannot change another session's result. Hash-collision redesign is out of scope. |
| HOOK-TEST-LOCK-RECOVERY | HOOK-INC-003, HOOK-INC-006 | A controlled lock holder is released and all waiting valid submissions eventually complete without loss. A permanently held or injected lock failure yields an explicit operational failure, not `None` interpreted as a successful no-op, and does not hang indefinitely. |
| HOOK-TEST-PERSISTENCE | HOOK-INC-003, HOOK-INC-004 | Injected failures known to occur before replacement preserve the last valid counter and expose terminal errors. Replacement followed by parent-directory durability-sync failure yields a distinct uncertain outcome; recovery reconciles production state before retry and never blindly duplicates the increment. A successful update remains parseable after the atomic replacement path. |
| HOOK-TEST-PLATFORM | HOOK-INC-005 | Unix lock/rename and parent-directory sync behavior are exercised on Unix; Windows lock sharing and `MoveFileExW` replacement behavior are exercised on Windows. Assertions cover committed, pre-replacement failure, and post-replacement uncertainty without platform-specific weakening or timing assumptions. |
| HOOK-TEST-INPUT | HOOK-INC-001, HOOK-INC-003 | Invalid event, unsupported agent, empty session, and oversized input remain silent non-updates, while a valid input whose operational update fails is distinguishable from those filtered inputs. |
| HOOK-TEST-INSTALLED-PATH | HOOK-INC-001, HOOK-INC-002, HOOK-INC-003, HOOK-INC-004, HOOK-INC-005, HOOK-INC-006 | Invoke the installed/production hook entry point with controlled contention and fault-injection seams; verify counters, reminders, and explicit failure/uncertainty outcomes through the real lock, counter, atomic replacement, and reminder paths. No `cfg(test)`, fake counter/lock path, test-only bypass, or behavior divergence is permitted. |

Test implementations should use deterministic barriers, injectable seams, or controlled lock holders rather than relying on a race that happens to reproduce on one machine. Expected totals and reminder sets must be computed from generated initial counts, submission parameters, and accepted outcomes, not embedded as a special-case implementation result. Fault injection may control the same production seams used by the installed path but must not replace them.

## Goal/test traces

| Product or test file | Status | Reverse issue refs |
| :--- | :--- | :--- |
| `.arca/goal/index.md` | not-active | none; no goal exists during intake |
| `.arca/goal/ubi-lang.md` | not-active | none; no goal exists during intake |
| `.arca/goal/spec.md` | not-active | none; no goal exists during intake |
| `.arca/goal/design.md` | not-active | none; no goal exists during intake |
| `.arca/goal/test-list.md` | not-active | none; no goal exists during intake |

## Authority traces

| Artifact | Status | Integration and reverse refs |
| :--- | :--- | :--- |
| `AGENTS.md` | unaffected | Repository guidance followed; no product-code edits. |
| `.arca/index.md` | unaffected | Issue route and exact five-file shape followed. |
| `.arca/ubi-lang.md` | unaffected | Existing Arca process vocabulary reused; issue terms are local here. |
| `.arca/tpl/issue/index.md` | unaffected | Index structure and status field followed. |
| `.arca/tpl/issue/ubi-lang.md` | unaffected | Issue-local vocabulary structure followed. |
| `.arca/tpl/issue/spec.md` | unaffected | Requirement dispositions recorded separately from issue status. |
| `.arca/tpl/issue/design.md` | unaffected | Proposed mechanics kept as incoming evidence. |
| `.arca/tpl/issue/test-plan.md` | unaffected | Verification and lifecycle trace tables retained. |
