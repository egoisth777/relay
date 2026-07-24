# Residual record template

```yaml
residual-id: {{residual-id}}
goal-requirement-ref: {{goal-requirement-ref}}
frozen-goal-revision: {{revision}}
implementation-revision: {{revision}}
status: {{missing|partial|satisfied}}
concrete-evidence-refs:
  - {{evidence-path-or-test-ref}}
required-test-refs:
  - {{test-ref}}
classification-rationale: {{why-this-status-is-supported}}
```

A residual is one record for one requirement. Missing evidence can never support `satisfied`.
