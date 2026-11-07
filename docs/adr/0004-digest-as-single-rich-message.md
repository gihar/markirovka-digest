---
status: accepted
---

# The Digest is published as a single Rich Message

## Context and decision

Telegram's Bot API 10.1–10.3 (June–August 2026) introduced Rich Messages:
`sendRichMessage` accepts a whole document — real headings, collapsible blocks,
32 768 characters in one message. Verified live on 2026-11-06 against
`api.telegram.org`: the method exists, accepts the `markdown` content field,
and a test message with headings, lists, `<details>` and a footer rendered
correctly in a private chat.

Until now the Digest pipeline converted the model's Markdown to Telegram's
HTML subset, folded theme bodies into `<blockquote expandable>` and split the
result into 4 096-character parts with continuation markers. The split is the
pain: parts, pacing, flood-waits, and an archive that has to rejoin them.

The Digest (daily and weekly review alike) is now published as **one Rich
Message**:

- **Content mode: `markdown`.** The model's Markdown passes nearly as-is; the
  renderer injects discussion links from `[[mN]]` references (URLs still never
  come from the model) and converts structure: issue title → `<h2>`, theme
  heading lines → `<h3>`, minor labels («Кратко», «📅 Сроки») stay bold
  paragraphs.
- **Reader UX unchanged (v1).** Theme heading and «👉 Что делать» stay visible;
  the theme body stays collapsed. The prompt is not touched: tables stay
  forbidden even though Rich Messages render them — a follow-up change, not a
  rider on this one.
- **Alerts stay on `sendMessage`.** They are short technical messages to a
  duty chat; rich structure buys them nothing.

## Delivery-failure ladder

An issue must come out every day, but a retry after an *unknown* outcome can
duplicate a published Digest — the worst failure mode in a public channel.

- **429** → wait `retry_after`, bounded retries (as today).
- **Validation 4xx from `sendRichMessage`** → fall back to the legacy
  HTML multi-part path **and raise a degraded alert**. The alert keeps the
  fallback from silently becoming the permanent path.
- **Timeout / 5xx** → outcome unknown: alert only. No retry, no fallback; a
  human checks the channel. A duplicate Digest is worse than a late one.

## Considered options

- **Keep `sendMessage`.** Rejected: the 4 096 limit, part pacing and marker
  rejoining are exactly the complexity this removes.
- **`blocks` JSON.** Rejected: deterministic but a heavy builder layer; the
  model already emits nearly-native Rich Markdown.
- **`html` mode.** Rejected: re-introduces a full Markdown→HTML conversion —
  the second source of truth we are deleting.

## Consequences

- **The old HTML render path is kept, deliberately, as the fallback.** Two
  render paths is the price of the reliability guarantee; the fallback's alert
  is what stops it from rotting unnoticed.
- **One message per issue.** The continuation marker is dead for new posts;
  `digest_archive` keeps rejoining it for old ones.
- **The archive blocker is confirmed; the fix is Digest-Service-side only.**
  Experiment (2026-11-06): a forwarded rich message carries **no `text`
  field at all** — only the `rich_message` block JSON. `fetch_channel_posts`
  (which filters `COALESCE(text, caption) IS NOT NULL`) would silently drop
  every rich day, blinding the weekly review and the Previous-Digest
  reference. Two facts keep the fix on our side of the boundary:
  - the Scraper already stores everything: python-telegram-bot's
    `Message.to_dict()` merges unknown API fields (`api_kwargs`) into its
    output, so `raw_message` keeps the rich blocks as shipped — verified
    2026-11-06 and pinned by a characterization test in the Scraper's suite.
    No Scraper change is needed and no deployment-ordering constraint
    exists;
  - the Digest Service flattens `raw_message → rich_message → blocks` into
    plain text at read time, so `digest_archive`'s title-line contract
    (ADR-0003) keeps working: the flattened heading text equals
    `digest_title` exactly.
- **Staging before production:** a full pipeline run renders and sends to a
  private chat with the bot first. Channel-feed behaviour (where Telegram
  collapses a long rich message itself) is accepted as untested until the
  first production post.
