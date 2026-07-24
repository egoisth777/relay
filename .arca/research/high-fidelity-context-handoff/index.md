# High-fidelity context handoff

## Problem

Relay must carry a working session into a fresh session with less context, without losing the facts needed to continue correctly. A short summary is not enough if it drops constraints, decisions, exact identifiers, provenance, or unfinished work.

## Research question

How do agent frameworks, coding agents, and memory systems compress session state and reconstruct useful working context? In particular, what can Relay learn from OMP and pxpipe rendering old text into images for multimodal models?

## Evaluation lens

1. What exact input is compressed?
2. What representation is retained?
3. How does a fresh session reconstruct working state?
4. Which facts are lossless, lossy, or recoverable from retained source data?
5. What are the full token, latency, cost, safety, and provider constraints?
6. Does the reconstructed session complete the next task correctly?

Claims must use primary sources where possible. Direct evidence and inference stay separate. Research is advisory and does not change Relay product authority.

## Results

| File | Scope |
| :--- | :--- |
| `01-omp-pxpipe.md` | OMP Snapcompact and pxpipe image-based context packing |
| `02-agent-frameworks.md` | OpenAI Agents SDK, LangGraph, AutoGen, Semantic Kernel, CrewAI, and PydanticAI |
| `03-coding-agents.md` | Coding-agent compaction and reconstruction |
| `04-memory-systems.md` | Letta/MemGPT, Zep, Mem0, LlamaIndex, and related memory systems |
| `05-compression-methods.md` | Text compressors, visual packing, risks, and fidelity tests |
| `06-omp-autocompact-handoff.md` | OMP semantic autocompact and new-session handoff capture |
| `next-steps.md` | Designer synthesis and suggested next steps for Relay |

Research date: 2026-07-24.
