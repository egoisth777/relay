# conversate — Flows & State Machines

Workflows and state machines for the conversate (`conv`) skill. The agent decides
*what* and *when*; the CLI (`conv_cli.py`) performs every store mutation.

## 0. High-level dispatch

How a user utterance becomes work. `SKILL.md` routes intent to a reference playbook,
which drives one or more CLI commands.

```mermaid
flowchart TD
    U[User utterance or auto-save hook] --> S{SKILL.md routing}
    S -->|"save / checkpoint / park / auto-save"| SAVE[references/save.md]
    S -->|"resume / continue where we left off"| RES[references/resume.md]
    S -->|"list / what's open"| LIST[references/list.md]
    S -->|"sidekick / return / continue"| BR[references/branching.md]
    S -->|"regen / drift / troubleshoot"| CLI[references/cli.md]

    SAVE --> C[conv_cli.py]
    RES --> C
    LIST --> C
    BR --> C
    CLI --> C

    C --> LOG[(conv/log/*.md\nsource of truth)]
    C --> IDX[(conv/index.jsonl\nderived cache)]
```

## 1. Conversation status state machine

Every conversation is `active`, `parked`, or `closed`. Transitions happen through
`upsert` (initial status) and `set-status`.

```mermaid
stateDiagram-v2
    [*] --> active: upsert (new, default status)
    [*] --> parked: conv:park (upsert --status parked)

    active --> parked: conv:park / parent parked for a sidekick
    parked --> active: conv:resume (set-status active)
    active --> closed: conv:return on a branch (set-status closed)
    parked --> closed: conclude without resuming
    closed --> active: reopened via resume (rare)

    note right of closed
        Returned branches end here,
        usually carrying a ## digest.
        Refs keep them discoverable
        from the parent.
    end note
```

## 2. Save / checkpoint flow (`conv:save`, `conv:park`)

```mermaid
flowchart TD
    A[Trigger: save/checkpoint/park or auto-save reminder] --> B[init: ensure store exists]
    B --> C[Infer topic + tags from conversation]
    C --> D[Extract state by priority]
    D --> D1[dict: coined/agreed terms — highest value]
    D --> D2[summary, qa open threads]
    D --> D3[sources / insights / decisions if any]
    D1 --> E[Assemble JSON: topic,status,tags,refs,sections]
    D2 --> E
    D3 --> E
    E --> F[pipe to: conv_cli.py upsert --stdin]
    F -->|park| F2[add --status parked]
    F --> G[CLI: validate mandatory summary/dict/qa]
    F2 --> G
    G --> H[write conv/log/YYYY-MM-DD_slug.md]
    H --> I[regen-refs: reconcile reverse links]
    I --> J[rebuild index.jsonl]
    J --> K{manual or auto?}
    K -->|manual| L[Present id/topic, invite rename]
    K -->|auto| M[Print: Auto-saved as id - rename anytime]
```

Key rule: `summary`, `dict`, and `qa` are mandatory; the CLI rejects a body missing
any of them. Extraction is written for a *cold* agent, excluding tool noise and chatter.

## 3. Resume flow (`conv:resume`)

```mermaid
flowchart TD
    A[User: resume the X discussion] --> B[conv_cli.py search "query"]
    B --> C{how many confident hits?}
    C -->|exactly one| D[show id --markdown]
    C -->|multiple| E[Present ranked ids/topics] --> F[User chooses] --> D
    C -->|none| G[Report nothing found / broaden query]
    D --> H[Reconstruct in order]
    H --> H1[1. frontmatter: id/status/tags/refs]
    H1 --> H2[2. summary]
    H2 --> H3[3. dict — ubiquitous language FIRST]
    H3 --> H4[4. qa — spine; Q open = live threads]
    H4 --> H5[5. sources — read only as needed]
    H5 --> H6[6. insights]
    H6 --> H7[7. decisions — do not relitigate]
    H7 --> H8[8. linked convs via refs]
    H8 --> I[set-status id active]
    I --> J[Present short summary + open threads]
```

## 4. Search cascade (inside `conv_cli.py search`)

The cascade short-circuits at the first layer with a confident result. A single hit at
any layer returns immediately.

```mermaid
flowchart TD
    Q[query] --> T[tokenize, drop stopwords]
    T --> L1[Layer 1: filename/id substring score]
    L1 -->|exactly 1 hit| R[return hit]
    L1 -->|>1 hits| RL[return ranked]
    L1 -->|0 hits| L2[Layer 2: rg over index.jsonl]
    L2 -->|rg missing| L2b[pure-Python index field scorer]
    L2 -->|hits| RL
    L2b -->|hits| RL
    L2 -->|0 hits| L3[Layer 3: semble over conv/log]
    L3 -->|semble or uvx semble available + hits| RL
    L3 -->|unavailable / 0 hits| L4[Layer 4: built-in body scorer]
    L4 --> RL
```

| layer label | engine | over what |
|-------------|--------|-----------|
| `fff` | substring score | id + file path |
| `rg-index` / `rg-index-fallback` | `rg` or Python | `index.jsonl` fields |
| `semble` | `semble` / `uvx semble` | `conv/log` bodies (semantic) |
| `semble-body-fallback` | built-in | conversation bodies |

## 5. Branching — sidekick / return (`conv:sidekick`, `conv:return`)

A protected side exploration that does not pollute the parent. The parent is parked,
a peer is spawned, and on return a digest flows back.

```mermaid
sequenceDiagram
    participant U as User
    participant A as Agent
    participant CLI as conv_cli.py
    participant P as Parent conv
    participant B as Branch conv

    U->>A: conv:sidekick "explore Y"
    A->>A: pick mode (probe vs sidekick)
    alt probe mode
        A->>A: run subagent, return digest into parent only
        Note over A,B: no file created unless user opts in
    else sidekick mode (default)
        A->>CLI: set-status parent parked
        CLI->>P: status = parked
        A->>CLI: upsert branch (ref spawned-from: parent)
        CLI->>B: write branch, status = active
        CLI->>P: add reverse ref spawned-to (regen-refs)
        CLI->>CLI: rebuild index
    end

    Note over U,B: ... exploration happens in the branch ...

    U->>A: conv:return
    A->>A: generate digest (explored, conclusions, files, contradictions, next)
    A->>CLI: upsert branch with ## digest (contradictions -> qa as Q open)
    A->>CLI: set-status branch closed
    A->>CLI: regen-refs
    alt parent live
        A->>U: inject digest into current context
    else parent parked
        Note over A,P: digest surfaces on parent's next resume via refs
    end
```

## 6. Continue in a clean session (`conv:continue`)

Same topic, fresh session — distinct from a sidekick (which is a side exploration).

```mermaid
flowchart LR
    A[conv:continue] --> B[Save + park current conversation]
    B --> C[upsert new conv\nref continued-from: parent]
    C --> D[CLI adds reverse continued-as on parent]
    D --> E[Seed new conv from parent's\ndict / qa / sources / insights / decisions]
    E --> F[Carry clear continued-from marker]
    F --> G[Rebuild index]
```

## 7. Bidirectional ref reconciliation (`regen-refs`, also run inside `upsert`)

The store is a graph; the CLI guarantees every forward ref has its reverse.

```mermaid
flowchart TD
    A[regen-refs] --> B[read all convs tolerant]
    B --> C[build desired ref set per conv]
    C --> D[for each forward ref, add reverse on target\nusing REL_REVERSE map]
    D --> E{old refs == new refs?}
    E -->|same| F[no write — byte-stable]
    E -->|differ| G[write conv with reconciled refs + bump updated]
    G --> H[rebuild index.jsonl]
    F --> H
```

Reverse map: `spawned-from↔spawned-to`, `continued-from↔continued-as`,
`informed-by↔informed`.

## 8. Auto-save loop (turn-counter hook)

```mermaid
flowchart TD
    A[Each user turn] --> B[conv-turn-counter.ps1]
    B --> C[increment TEMP/conv-session-PID.count]
    C --> D{count > 10?}
    D -->|no| E[do nothing]
    D -->|yes| F[inject: CONV AUTO-SAVE: threshold reached]
    F --> G[Agent runs save flow silently]
    G --> H[Print: Auto-saved as id - rename anytime]
    H --> I[reset/continue counter]
```

## 9. Write invariants (cross-cutting, enforced by the CLI)

These hold for *every* mutating command:

```mermaid
flowchart LR
    W[any write] --> V1[validate: topic present]
    V1 --> V2[validate: status in active/parked/closed]
    V2 --> V3[validate: summary + dict + qa present]
    V3 --> V4[normalize: sorted unique tags + refs, ISO-UTC dates]
    V4 --> V5[write file only if bytes changed]
    V5 --> V6[regen-refs]
    V6 --> V7[rebuild index.jsonl]
```

- **Source of truth = `conv/log/*.md`.** `index.jsonl` is always rebuildable.
- **Idempotent writes.** Unchanged content is never rewritten (stable timestamps).
- **`## decisions` is effectively append-only** — never mutated unless the user
  explicitly asks; branch contradictions go to `## qa` as `Q (open)`, not into decisions.
