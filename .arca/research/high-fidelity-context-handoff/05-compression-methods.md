# Compression methods and fidelity tests

## Problem

A shorter prompt is not automatically a good handoff. Relay needs the receiver to recover operational state: goal, constraints, decisions, exact identifiers, work status, provenance, and safe next actions.

## What current compressors do

### LLMLingua and LongLLMLingua

They rank and delete contexts, sentences, and tokens to meet a token budget. LongLLMLingua uses the current question in ranking.

- Good at reducing input for a known query.
- Deleted text is not reconstructable.
- A future session may ask a different question, so query-based deletion can remove later-critical state.
- The compressor and receiver can tokenize differently, so target tokens are only an estimate.

### Selective Context

It removes statistically predictable text using token self-information.

- No task-specific training is required.
- Predictability is not importance. A repeated branch, `--force`, security rule, or earlier decision may be predictable and still essential.
- The result is lossy and has no built-in trust or state model.

### RECOMP

It provides learned extractive and abstractive compression for retrieved documents.

- Extractive mode keeps selected source sentences and can retain offsets for provenance.
- Abstractive mode creates new text and can omit or invent details.
- Its public QA and language-model benchmarks test answer usefulness for a known query, not complete session reconstruction.

## Visual packing

Rendering text into images shifts cost from text tokens to vision tokens; it does not remove cost.

- OpenAI meters images by model-specific patch or tile rules and detail settings.
- Claude uses visual patches and can resize images at provider limits.
- Tiny text, resizing, compression artifacts, Unicode, layout, page ordering, and model changes can hurt recall.
- Image packing only works for a verified vision endpoint.

Provider rules change. Relay must measure actual API usage instead of hard-coding one image-token formula.

## Better reconstruction path

1. Capture a typed checkpoint with source ranges, hashes, roles, host, and trust labels.
2. Keep raw transcript and artifacts addressable.
3. Render a provider-neutral text form first.
4. Under pressure, retain an exact state ledger, then add extractive evidence and an optional summary.
5. For a supported vision model, add a deterministic image derivative with page index and source hash.
6. Reconstruct a state object, validate required fields and provenance, then run a controlled next-step scenario.

Every lossy form is a derivative, never the only stored record.

## Security failure

Compression can erase the boundary between trusted instructions and untrusted prior chat, tool output, or retrieved text. Image text can also contain prompt injection.

Required controls:

- keep provenance and trust labels outside generated prose;
- validate schema, authorization, and hashes outside the model;
- quote untrusted content as data;
- require confirmation for privileged writes or disclosure;
- retain an audit path to source events.

A prompt delimiter alone is not a security boundary.

## Fidelity test

Use held-out Relay sessions with a reviewed gold state ledger and executable continuation tasks. Compare raw context, typed checkpoint, text compression, summary, and image derivative at equal end-to-end budgets.

| Measure | Required evidence |
| :--- | :--- |
| Critical-state recall | Exact paths, hashes, commands, IDs, versions, permissions, constraints, and completed/pending status. Gate lossy handoff on 100% critical-invariant recall. |
| State accuracy | Field precision/recall for goals, rationale, dependencies, and risks; contradiction and unsupported-claim rates. |
| Continuation | A fresh session performs scripted next steps correctly; record test/build outcome and human intervention. This is the primary metric. |
| Provenance and safety | Every material claim links to source; injected content causes zero forbidden actions or disclosure. |
| Economics | Compressor plus receiver tokens, image tokens, bytes, render time, latency, retries, and p50/p95 cost. |
| Robustness | Repeat across providers, models, tokenizers, Unicode/code, long identifiers, resolutions, page order, and adversarial text; report worst case. |

Do not use ROUGE or compressed character count as the acceptance test.

## Relay implication

The safest order is:

1. structured checkpoint;
2. exact-token ledger;
3. recent native-text tail;
4. extractive source snippets;
5. optional semantic summary;
6. optional image archive.

This order makes meaning explicit, preserves exact state, and still allows dense evidence when context is scarce.

## Sources

Accessed 2026-07-24.

- [LLMLingua paper](https://arxiv.org/abs/2310.05736) and [implementation](https://github.com/microsoft/LLMLingua/blob/e0e9d99beb94098bbd924aa53c2c112eac41c758/llmlingua/prompt_compressor.py)
- [LongLLMLingua paper](https://arxiv.org/abs/2310.06839)
- [Selective Context implementation](https://github.com/liyucheng09/Selective_Context/blob/3074343653bbf3559a87a588667e843744bc6f2a/selective_context.py)
- [RECOMP paper](https://arxiv.org/abs/2310.04408) and [implementation](https://github.com/carriex/recomp/tree/51d4432151efb3275257a9407dc71d1e5ec6634d)
- [OpenAI image input and token costs](https://developers.openai.com/api/docs/guides/images-vision)
- [Anthropic vision resolution and token costs](https://platform.claude.com/docs/en/build-with-claude/vision)
- [Anthropic jailbreak guidance](https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-jailbreaks)
