# Current state template

```yaml
phase: {{idle|P1|P2|P3|P4|P5}}
status: {{running|waiting|idle}}
current_revision: {{revision-or-bootstrap}}
goal_revision: {{revision-or-none}}
active_refs:
  - {{relative-artifact-path}}
waiting_on: {{none-or-question-and-dependent-slice}}
```

{{Short state note. State `idle` only when no goal is active; record the dependent slice only for `waiting`.}}
