# OMP autocompact and handoff capture

## Problem

OMP has three different context-maintenance mechanisms. They must not be treated as one design:

1. semantic in-session compaction;
2. Snapcompact image archival;
3. explicit or automatic handoff into a new session.

This note focuses on what the first and third mechanisms capture.

Investigated `can1357/oh-my-pi` at revision `a38cd95d` on 2026-07-24.

## Automatic semantic compaction

### What remains verbatim

OMP walks backward through session entries and keeps about 20,000 recent tokens by default. It cuts only at valid message boundaries and never starts with an orphan tool result. This recent tail remains as native messages.

The original older entries also remain in the local session log. They are recoverable from the source session, but are no longer part of normal active model context.

### What the generated summary is asked to capture

- Goal
- Constraints
- Progress:
  - done;
  - in progress;
  - blocked
- Decisions
- Next steps
- Critical context
- Notes
- Exact file paths
- Symbol and function names
- Error messages
- Relevant tool outputs and command results
- Repository state, including branch and uncommitted changes

OMP also derives a filesystem-operation list, capped at 20 entries, covering files read, written, or both.

### What can be lost before summarization

- Tool results are capped at 2,000 characters.
- Tool results marked useless, and their matching calls, are omitted.
- Thinking is omitted for the Anthropic serialization path.
- The summary is generated prose; no schema validator proves that every requested field survived.
- Later compaction can summarize the previous summary again, compounding loss.

### Reconstruction

The active model receives:

1. the generated compaction summary;
2. the retained recent messages around the compaction boundary;
3. all later messages.

The older source conversation is not replayed automatically.

## Explicit `/handoff`

### Input to handoff generation

OMP sends a one-shot model request containing:

- the live system prompt;
- normalized tool definitions;
- transformed active message history;
- secret-obfuscated provider context;
- an optional user focus;
- the handoff-writing prompt.

Tools are disabled for the generation request. OMP keeps only text response blocks.

### Required handoff headings

- Goal
- Constraints & Preferences
- Progress:
  - Done;
  - In Progress;
  - Pending
- Key Decisions
- Critical Context
- Next Steps

The prompt also asks for exact:

- file paths and symbol names;
- commands run;
- test results and failures;
- decisions;
- partial work affecting the next step.

These are prompt requirements, not runtime-validated fields. Any non-empty generated document can be accepted.

### New-session state

OMP then:

1. flushes the old session;
2. creates a child session with a reference to the parent session file;
3. resets runtime, memory, tool, and checkpoint state;
4. inserts one persisted message:

```text
<handoff-context>
...generated handoff document...
</handoff-context>

The above is a handoff document from a previous session. Use this context to continue the work seamlessly.
```

That generated document is initially the successor model's only conversation context. The parent session remains on disk, but the successor does not automatically retrieve or cite it.

Automatic handoff uses the same document shape and adds this focus:

> Threshold-triggered maintenance: preserve critical implementation state and immediate next actions.

On overflow or generation failure, OMP can fall back to semantic in-session compaction.

## Snapcompact distinction

Snapcompact does not generate the semantic fields above. It serializes old history into retained text edges and PNG frames, while preserving normalized source text up to its archive budget. It is a different automatic strategy and still has frame, byte, glyph, and oldest-content loss.

## Comparison with Relay

| OMP asks for | Relay currently stores | Remaining gap |
| :--- | :--- | :--- |
| Goal | `resume.goal` | Covered |
| Constraints and preferences | `user-instructions`, summary, Q&A | Present, but no completeness check |
| Done work | `resume.checkpoints` | Covered |
| In-progress, pending, blocked work | next steps, open questions, summary | Present as prose/lists; state is not explicit per item |
| Decisions | decisions section | Covered but optional and trimmable |
| Next steps | `resume.next-steps` | Covered |
| Paths and symbols | artifacts, sources, transcript, summary | No dedicated exact-value field |
| Commands, tests, and failures | Q&A, insights, transcript, summary | No required structured capture |
| Repository state | environment references | Agent-authored and optional |
| Source recovery | Parent session keeps original entries | Relay stores only a condensed transcript; no native full-session recovery path |
| Handoff boundary | `<handoff-context>` plus continuation instruction | Relay has a pack banner and activation command, but no explicit generated-claim/source distinction |

Relay has better named state sections than OMP's generated Markdown. OMP has a stronger local recovery property because the old parent session retains its original entries.

## Lessons for Relay

1. Use OMP's headings as a **save completeness check**, not as a replacement summary format.
2. Require exact paths, symbols, commands, test outcomes, failures, and repository state in native text when present.
3. Preserve a resolvable source-session or artifact reference for critical claims. Do not let the condensed transcript become the only evidence.
4. Mark generated summaries as claims and identify where exact evidence can be recovered.
5. Keep a recent verbatim tail beside semantic state.
6. Report omissions and truncation explicitly.
7. Validate required capture fields before accepting a handoff; OMP currently relies on prompt compliance.
8. Keep image archival optional and derived.

The highest-value OMP lesson is not its summary wording. It is the combination of **generated task state + recent verbatim context + retained parent source**.

## Sources

- [Automatic compaction core](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/agent/src/compaction/compaction.ts)
- [Compaction summary prompt](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/agent/src/compaction/prompts/compaction-summary.md)
- [Compaction serialization and tool-result limits](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/agent/src/compaction/utils.ts)
- [Handoff document prompt](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/agent/src/compaction/prompts/handoff-document.md)
- [New-session handoff transition](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/coding-agent/src/session/session-handoff.ts)
- [Compacted-context reconstruction](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/coding-agent/src/session/session-context.ts)
- [Automatic maintenance strategy](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/coding-agent/src/session/session-maintenance.ts)
- [Handoff tests](https://github.com/can1357/oh-my-pi/blob/a38cd95d7d8c457a22f1b81c059b5491d78f79a3/packages/agent/test/handoff.test.ts)
