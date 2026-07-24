# Agent frameworks

## Problem

Routing work to another agent is easy. Giving that agent a small but sufficient working context is not. The framework must separate **who runs next** from **what state the receiver gets**.

## Comparison

| Framework | Receiver state | Compression and reconstruction |
| :--- | :--- | :--- |
| OpenAI Agents SDK | Full prior input, pre-handoff items, current-turn items, run context, or filtered input | Default sends full history. Optional nested history serializes a role-labelled transcript into one assistant message. A separate Responses session delegates semantic compaction to the provider. |
| LangGraph Supervisor | Whole graph state, normally including messages | `Command` or `Send` routes the destination with updated state. The examined handoff has no automatic compressor or fidelity check. |
| Microsoft AutoGen | Target plus a list of model-context messages, including selected tool calls/results | Receiver replays each context item, then the handoff message. This is transfer, not semantic compression. |
| Semantic Kernel | Shared chat history/thread | An LLM can replace older history with one summary while retaining system/developer messages and a recent tail. Function content is excluded by default. |
| CrewAI | Execution message list | On a context-limit error it can preserve system messages/files, summarize non-system chunks, clear old messages, and insert one summary. It terminates instead if context-window handling is disabled. |
| PydanticAI | Provider model-message history | `CompactionPart` stores provider-produced compaction. Anthropic exposes readable text; OpenAI uses encrypted provider data that must return to the same provider. |

## Important details

### OpenAI Agents SDK

`HandoffInputData` separates original history, items before handoff, new current-turn items, run context, and receiver-only replacements. This makes filtering explicit.

Its optional nested history is not an LLM summary by default. It formats transcript items into a numbered, role-labelled assistant message and can keep selected current items verbatim. A separate provider compaction session triggers after ten candidate items and attempts to restore storage if replacement fails.

### LangGraph and AutoGen

Both preserve explicit state rather than inventing a summary:

- LangGraph copies the graph state to the destination node.
- AutoGen passes an explicit context list and replays it.

They preserve only state that the application placed in those objects. Neither solves oversize, stale, missing, or non-serializable state automatically.

### Semantic Kernel and CrewAI

Both use generated summaries. A good prompt asks for continuity, but it does not prove completeness. Semantic Kernel's default exclusion of function content is a concrete risk for coding sessions. CrewAI reacts only after an over-limit error and can replace almost all non-system history with one generated message.

### PydanticAI

Provider-managed compaction may be efficient but is not portable. Opaque OpenAI compaction cannot serve as Relay's host-neutral handoff format.

## Failure modes

- A filter or summary drops a constraint, tool result, decision, or failure state.
- Full shared state grows without a size policy.
- The receiver gets data but no chronology, provenance, or trust boundary.
- Storage replacement partly fails.
- Provider-bound compaction cannot move to another host/model.
- Routing succeeds while operational reconstruction fails.

No examined framework reported a handoff-fidelity benchmark or token-reduction percentage.

## Relay implication

Relay should define a provider-neutral, typed handoff artifact with:

1. routing target and compatibility metadata;
2. task objective, plan, constraints, and decisions;
3. completed and failed actions with evidence;
4. unresolved questions and ordered next work;
5. recent verbatim turns;
6. exact references into durable source events/artifacts;
7. source range, hash, and trust labels.

Keep source evidence outside generated prose. Treat summaries and provider compaction as replaceable views. Validate required fields and references before activating the receiver.

## Sources

Accessed 2026-07-24.

- [OpenAI Agents SDK handoff data](https://github.com/openai/openai-agents-python/blob/5d62056/src/agents/handoffs/__init__.py)
- [OpenAI nested handoff history](https://github.com/openai/openai-agents-python/blob/5d62056/src/agents/handoffs/history.py)
- [OpenAI Responses compaction session](https://github.com/openai/openai-agents-python/blob/5d62056/src/agents/memory/openai_responses_compaction_session.py)
- [LangGraph Supervisor handoff](https://github.com/langchain-ai/langgraph-supervisor/blob/88859b3/langgraph_supervisor/handoff.py)
- [AutoGen handoff message](https://github.com/microsoft/autogen/blob/027ecf0/python/packages/autogen-agentchat/src/autogen_agentchat/messages.py)
- [Semantic Kernel summarization reducer](https://github.com/microsoft/semantic-kernel/blob/d003eb2/python/semantic_kernel/contents/history_reducer/chat_history_summarization_reducer.py)
- [CrewAI context handling](https://github.com/crewAIInc/crewAI/blob/b14d36b/lib/crewai/src/crewai/utilities/agent_utils.py)
- [PydanticAI compaction part](https://github.com/pydantic/pydantic-ai/blob/801814a/pydantic_ai_slim/pydantic_ai/messages.py)
