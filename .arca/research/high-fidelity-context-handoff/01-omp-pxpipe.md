# OMP and pxpipe

## Problem

Can old session text be rendered into dense images, read by a vision model, and still support a faithful handoff?

## Finding

Yes for **high-density recall**, but not for exact reconstruction. OMP and pxpipe independently use the same broad trick: turn old text into PNGs, keep recent or precision-critical data as text, then let the destination model read both. Neither project proves lossless cross-host handoff.

The project identities are:

- **OMP:** `can1357/oh-my-pi`, package `@oh-my-pi/snapcompact` 17.1.1, revision `90e0a8a`.
- **pxpipe:** `teamchong/pxpipe`, package `pxpipe-proxy` 0.10.0, revision `8a9175d`.

OMP has no pxpipe dependency or source reference. They are separate implementations.

## OMP Snapcompact

- Serializes discarded history, normalizes it, and renders the older middle into PNG frames.
- Keeps native-text edges around the image middle. Under pressure it uses denser center frames and can drop the oldest excess center.
- Persists the source text with the archive. Later compaction re-renders from source instead of taking screenshots of screenshots.
- Reconstructs provider input as ordered text head, image middle, and text tail. The model visually reads the frames; OMP has no OCR decoder.
- Chooses layout by provider/model. Current shapes commonly use 1568-pixel square frames; some models use 1932 or 2048 pixels.
- Limits tool data before rendering, rejects poorly renderable content, caps frame count/data, and can omit old frames under request-byte limits.

This is visual archival, not semantic understanding. Its strength is preserving much more source form than a prose summary while spending vision tokens instead of text tokens.

## pxpipe

- Proxies provider requests and renders static slabs, large tool results, or a closed old-history prefix into Base64 PNG blocks.
- Its dense profile is 1568×728 pixels: 312 columns × 90 rows of 5×8 cells, nominally 28,080 characters per page.
- Reflows short lines and marks hard newlines to avoid wasting image width.
- Leaves the newest four turns and open tool sequences as text.
- Adds role and absolute-turn tags so the model can place the image in session order.
- Adds a native-text **factsheet** for paths, URLs, hashes, IDs, versions, flags, and numbers. The prompt tells the model to trust this text rather than visually copy exact strings.
- Freezes completed chunks to keep image bytes stable for provider prompt caches.

pxpipe explicitly warns that verbatim recall from images is unreliable.

## Reported results

These are project claims, not independently reproduced results:

- OMP source comments report tool-result legibility F1 of `.806` for an Anthropic-oriented shape and `.934` for a Google-oriented shape, both better than older denser layouts.
- pxpipe reports 1,456 Anthropic input tokens for one 1568×728 page and 98.95% OCR accuracy on Opus 4.7.

The OMP test suite could not be run during this research because Bun was unavailable.

## Failure modes

- Tiny glyphs fail after provider resizing or on a different vision model.
- Exact names, paths, numbers, and code can be misread even when the gist survives.
- Unsupported glyphs can be normalized or dropped.
- Image count, bytes, latency, and vision-token billing vary by provider.
- OMP can truncate the oldest dense center or omit frames under byte limits.
- pxpipe drops thinking blocks from collapsed history and can render missing-atlas characters as blanks.
- A rendered instruction can still act as prompt injection when read by the model.

## Relay implication

Use the image as a **derived cache**, never as Relay's only handoff record.

A safe Relay experiment would combine:

1. retained source text or content-addressed source references;
2. a structured native-text state ledger for goals, constraints, decisions, work status, and provenance;
3. a pxpipe-like exact-token factsheet;
4. dense images only for bulky old evidence;
5. native-text chronology, role labels, and recent tail;
6. destination-specific image geometry and a plain-text fallback.

The important idea is not “image equals compression.” It is **tiered fidelity**: exact critical state in text, broad old evidence in images, and recoverable source behind both.

## Sources

Accessed 2026-07-24.

- [OMP Snapcompact package](https://github.com/can1357/oh-my-pi/blob/90e0a8af289d540fd3580d35606b5fa57212228c/packages/snapcompact/package.json)
- [OMP Snapcompact implementation](https://github.com/can1357/oh-my-pi/blob/90e0a8af289d540fd3580d35606b5fa57212228c/packages/snapcompact/src/snapcompact.ts)
- [OMP Snapcompact tests](https://github.com/can1357/oh-my-pi/blob/90e0a8af289d540fd3580d35606b5fa57212228c/packages/snapcompact/test/snapcompact.test.ts)
- [pxpipe renderer](https://github.com/teamchong/pxpipe/blob/8a9175d55054048b88c0d29aee05231d765e67f2/src/core/render.ts)
- [pxpipe history collapse](https://github.com/teamchong/pxpipe/blob/8a9175d55054048b88c0d29aee05231d765e67f2/src/core/history.ts)
- [pxpipe exact-token factsheet](https://github.com/teamchong/pxpipe/blob/8a9175d55054048b88c0d29aee05231d765e67f2/src/core/factsheet.ts)
- [pxpipe OpenAI history limits](https://github.com/teamchong/pxpipe/blob/8a9175d55054048b88c0d29aee05231d765e67f2/src/core/openai-history.ts)
- [pxpipe tests](https://github.com/teamchong/pxpipe/tree/8a9175d55054048b88c0d29aee05231d765e67f2/tests)
