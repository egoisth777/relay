# Issue ubiquitous language

These terms are local to this issue and describe the hook-runtime regression.

| Term | Meaning |
| :--- | :--- |
| **lossless hook increment** | A valid `UserPromptSubmit` for a non-empty session produces exactly one durable increment, regardless of concurrent valid submissions or transient lock contention. |
| **tenth-turn crossing** | The transition in which a committed session count becomes divisible by 10; that update produces exactly one reminder. The production rule is fixed and is not configurable by this issue. |
| **lock-failure propagation** | Making an inability to acquire or use a session lock observable to the hook operation rather than converting it into an indistinguishable successful no-op. |
| **committed update** | An increment whose replacement and required durability synchronization both succeed; the resulting count is consumed and reminder calculation may use it. |
| **failed-before-replacement** | An operation failure known to occur before the counter replacement; the previously published valid counter remains authoritative, and the explicit failure may be retried under its contract. |
| **uncertain-after-replacement** | A failure after replacement may have made the new counter visible, such as a parent-directory durability sync failure; the outcome is observable and MUST NOT trigger a blind retry. Recovery must reconcile the published state before another increment is attempted. |
