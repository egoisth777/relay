# Coding agents

## Problem

A coding-agent handoff must shrink model-visible history without losing task constraints, repository facts, edits and tests, recent turns, or the order needed to resume safely.

## OpenAI Codex CLI

Codex uses a generated **context checkpoint**.

- Automatic compaction asks a model to create a handoff summary “for another LLM.”
- The default prompt explicitly asks for progress, decisions, constraints or user preferences, remaining work, and critical references.
- The final assistant checkpoint replaces older live history while real user messages remain beside it.
- A persisted `CompactedItem` stores both the checkpoint message and the full replacement message sequence. Resume uses this replacement sequence instead of summarizing the raw transcript again.
- Mid-turn compaction reinjects initial context before the latest real user message. Pre-turn/manual compaction clears it so the next normal turn injects it once.
- If the context window fails during compaction, Codex can drop the oldest item, retry, and emit a data-loss warning.
- Source comments warn that repeated compaction and long threads reduce accuracy.

**Useful lesson:** the persisted unit is the exact reconstructed message sequence, not just summary prose. However, Codex does not publish a universal numerical fidelity score for that checkpoint.

## Cline

Cline supports a stronger separation between canonical history and a model-facing working view.

- A pre-turn wrapper compacts when estimated input reaches a configured share of the context window.
- Its agentic mode preserves recent tokens, combines a prior summary with newly old messages, extracts file operations, serializes selected conversation, and asks a model for a detailed continuation note.
- The compacted message list can be kept as a **working-context sidecar** while an immutable event/transcript log remains canonical.
- If agentic compaction fails, Cline falls back to a basic strategy.
- It skips compaction when there are too few messages, no useful data, no summary budget, or an empty result.

**Useful lesson:** keep audit/resume source data separate from the lossy model view, and make compaction failure visible and recoverable.

## Claude Code

Public documentation confirms automatic compaction near context exhaustion, manual `/compact`, optional preservation instructions, and `/context`. The inspected public repository did not expose the compaction engine. Exact prompt transformation, persistence, reconstruction, and error behavior are therefore not established here.

## OMP, pxpipe, and other tools

OMP/Snapcompact and pxpipe image old text; their detailed mechanisms are in `01-omp-pxpipe.md`.

No primary implementation evidence was completed here for OpenHands, Roo Code, or Cursor. This note makes no mechanism claim for them.

## Failure modes

- A prose checkpoint omits an exact path, identifier, negation, decision rationale, or unfinished item.
- Repeated summary-of-summary compaction compounds loss.
- Token estimates differ from provider billing and actual model input.
- Mid-turn and next-turn reinjection can duplicate or omit host instructions.
- Compaction can fail, time out, or stop after destructive trimming.
- A generated checkpoint can convert untrusted tool or session text into apparent instructions.

## Relay implication

Relay should persist a reconstruction envelope with:

1. schema and compactor version;
2. source session/event range;
3. goals and non-negotiable constraints;
4. decisions with rationale;
5. exact repository paths, revisions, commands, and test results;
6. completed, pending, and risky work;
7. an ordered next-action list;
8. destination host/model plus token budget;
9. parent checkpoint and content hashes.

Retain the original event stream for repair. Add a recent native-text tail. Reconstruct deterministically from the envelope, then test that exact sequence on each destination host. Treat compaction as fallible and expose skipped, failed, interrupted, or data-loss status.

## Sources

Accessed 2026-07-24.

- [Codex compact prompt](https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/compact/prompt.md)
- [Codex local compaction](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs)
- [Codex persisted checkpoint installation](https://github.com/openai/codex/blob/main/codex-rs/core/src/session/mod.rs)
- [Cline compaction preparation](https://github.com/cline/cline/blob/main/sdk/packages/core/src/extensions/context/compaction.ts)
- [Cline agentic compaction](https://github.com/cline/cline/blob/main/sdk/packages/core/src/extensions/context/agentic-compaction.ts)
- [Claude Code context management](https://docs.anthropic.com/en/docs/claude-code/manage-context)
- [Claude Code settings](https://code.claude.com/docs/en/settings)
