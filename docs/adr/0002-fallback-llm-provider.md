---
status: accepted
---

# A second LLM provider as fallback: a degraded Digest beats no Digest

## Context and decision

On 2026-08-08 the OpenRouter API key hit its per-key spend limit ($2). Every
subsequent request returned `403 Key limit exceeded`, and the Digest Service
published nothing for five days before anyone noticed. A single provider's
billing state was a single point of failure for the whole service.

We evaluated `https://api.neuraldeep.ru/v1` (a LiteLLM proxy, OpenAI-compatible)
as a **replacement** and rejected it on quality, then adopted it as a
**fallback**: the primary provider stays Claude via OpenRouter, and a failed
request falls through to the secondary provider rather than failing the run.

The evaluation, on the heaviest real day in 30 (2026-07-29, 17.4k chars of
messages after excluding machine posts):

| | `qwen3.6-unlim-noreason` | `qwen3.6-unlim` (reasoning) |
|---|---|---|
| latency | 11.7 s | 55.7 s |
| completion tokens | 1464 / 8192 | 6542 / 8192 |

The reasoning variant is unusable here: reasoning tokens are billed against the
same `max_tokens` budget as the answer, and on the same day with machine posts
still in the input it took **67 s against a 60 s client timeout** while burning
7105 of 8192 tokens — it would trip both the timeout and the truncation guard on
a busy day. The non-reasoning variant has four-fold headroom on both.

Neither Qwen variant matches Claude on content: it drops the concrete anchors
(participant names, ticket numbers, article references, prices) that let a
reader trace a topic back to the source conversation, and the reasoning variant
also writes the bureaucratic filler the prompt explicitly forbids.

## Considered options

- **Switch to neuraldeep entirely.** Rejected: cheaper and unmetered, but the
  digest loses the specificity that makes it worth reading.
- **Stay single-provider and just raise the key limit.** Rejected: fixes this
  outage, not the class. Any 403/402/429 puts the service dark again.
- **Primary + fallback** (chosen). Normal days get Claude's quality; a provider
  outage costs quality for that day instead of costing the day.

## Consequences

- **Fallback without visibility is worse than no fallback** — the service would
  quietly serve degraded digests indefinitely. The fallback ships together with
  Telegram alerts to a dedicated chat (`TELEGRAM_ALERT_CHAT_ID`), which fire both
  when a run fails outright and when a Digest came from a non-primary provider.
- The fallback's model is pinned to the **non-reasoning** variant. Anyone
  changing `LLM_FALLBACK_MODEL` to a reasoning model must first raise
  `_HTTP_TIMEOUT` and `MAX_OUTPUT_TOKENS` in `analyzer.py`, or peak days will
  fail.
- Two providers means two credentials to keep alive. A silently-expired fallback
  key is invisible until the day it is needed; the alerts are the only thing that
  surfaces it.
- The measurements above are specific to the current prompt and message volume
  (median ~9.5k chars/day, peak ~23k). A materially longer prompt or a busier set
  of chats invalidates the headroom figures and the choice should be re-measured.
