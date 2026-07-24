# Memory systems

## Problem

A high-fidelity handoff needs both:

1. an authoritative, replayable session/state record;
2. a small working view for the receiving model.

Memory products usually optimize the second. Their summaries, facts, graphs, or retrieval results are not complete reconstruction by themselves.

## Comparison

| System | Retained form | Prompt reconstruction | Main loss |
| :--- | :--- | :--- | :--- |
| Letta / MemGPT | Editable in-context blocks and permanent archival entries | Core blocks are injected; archive is searched semantically | Its automatic conversation summary is capped at 100 words; retrieval can miss entries. |
| Mem0 | Vector records plus session messages | Similar memories are returned under filters, score threshold, and result cap | Default LLM inference extracts and updates facts; wording, order, and qualifications can disappear. |
| Zep Graphiti | Raw episode plus extracted entities and fact edges | Hybrid text/vector/graph search returns ranked facts | The graph cannot recreate omitted wording, order, or extraction errors. |
| LlamaIndex Memory | SQL chat store with active and archived messages plus memory blocks | Recent FIFO messages and selected blocks are injected | Block output may be summarized or truncated even though archived messages remain available. |
| LangGraph checkpoints | Versioned channel state, parent links, and pending writes | Graph state is loaded or replayed | Delta state is not reconstructable if ancestors or writes are pruned. |

## Mechanisms

### Letta

Core memory is explicitly model-visible block state. Archival entries are permanent and searched by semantic similarity. A separate summarizer asks for fewer than 100 words, so that summary is deliberately lossy.

### Mem0

`Memory.add` defaults to LLM inference: find similar memories, extract facts, and decide which records to add, update, or delete. `infer=False` stores raw individual message content, but later search is still selective.

### Graphiti

`add_episode` retains an episode body while extracting entities and edges. The source episode supports audit and recovery; graph facts support targeted recall. They serve different purposes.

### LlamaIndex

A bounded FIFO holds recent messages. Old messages are archived in a SQL chat store and passed to memory blocks. At prompt time, blocks are read by priority and may be truncated. This is a useful separation of durable transcript from compact working memory.

### LangGraph

A checkpoint stores versioned channel values and writes rather than prose meaning. It can reconstruct stored graph state, but a delta checkpoint depends on its complete ancestor/write chain. Copying only the head can silently produce empty state.

## Failure modes

- Generated summaries lose negations, failed attempts, chronology, or decision rationale.
- Embedding search misses a critical record due to query wording, rank, threshold, or result cap.
- Entity extraction merges distinct facts or creates a wrong edge.
- Prompt-budget truncation removes a low-ranked but essential block.
- Delta checkpoint pruning breaks replay.
- Stored transcript/state does not capture unrecorded model internals, provider state, filesystem changes, or external side effects.

No examined system published a controlled session-handoff fidelity benchmark.

## Relay implication

Use two layers.

### Authoritative manifest

A deterministic, versioned record containing:

- session and ordered event IDs;
- messages and tool calls/results;
- host/runtime metadata;
- repository revision and dirty-file hashes;
- checkpoint and content-addressed artifact references;
- schema version and integrity hashes.

### Derived briefing

A small, readable view containing objective, constraints, decisions and rationale, changed artifacts, tests/results, unresolved work, and exact manifest references. Mark it as derived and record source range, omitted categories, and generator/model version.

On resume: restore exact state first, inject the briefing second, retrieve by deterministic IDs/ranges third, and use semantic retrieval only as an extra recall channel. The receiver should cite source IDs for critical facts.

Images can be another derived view. They cannot replace the manifest unless Relay also retains source bytes and validates deterministic reconstruction.

## Sources

Accessed 2026-07-24.

- [Letta core memory](https://github.com/letta-ai/letta/blob/main/letta/schemas/memory.py)
- [Letta archival tools](https://github.com/letta-ai/letta/blob/main/letta/functions/function_sets/base.py)
- [Letta summarizer](https://github.com/letta-ai/letta/blob/main/letta/prompts/gpt_summarize.py)
- [Mem0 memory ingestion and search](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py)
- [Graphiti episode and search implementation](https://github.com/getzep/graphiti/blob/main/graphiti_core/graphiti.py)
- [LlamaIndex memory waterfall](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/memory/memory.py)
- [LangGraph checkpoint contract](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/langgraph/checkpoint/base/__init__.py)
