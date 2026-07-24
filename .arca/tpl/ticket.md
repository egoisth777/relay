# Ticket: {{ticket-id}}

```yaml
ticket-id: {{ticket-id}}
behavior-refs:
  - {{goal-or-current-spec-anchor}}
design-refs:
  - {{goal-or-current-design-anchor}}
planned-test-refs:
  - {{planned-test-id}}
dependencies:
  - {{ticket-id-or-none}}
status: {{approved|in-progress|done|held}}
```

## P4 test plan

| Planned test ID | Goal contract ref | Fixture/ref setup | Executable target | Observable oracle |
| :--- | :--- | :--- | :--- | :--- |
| {{planned-test-id}} | {{contract-ref}} | {{fixture-or-none}} | {{test-target}} | {{expected-observation}} |

## P5 proof and review

- Test result: {{result-and-evidence}}
- Review note: {{short-review-note}}
- Residual reverse reference: {{residual-path}}
