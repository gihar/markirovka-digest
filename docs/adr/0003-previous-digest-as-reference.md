---
status: accepted
---

# The previous Digest is reference context, never material to summarise

## Context and decision

A Digest covers exactly one calendar day and, until now, knew nothing about the
day before. The 🔁 status ("the theme continues from an earlier day") could only
be set when the day's own messages happened to say so — almost never — and the
Digest could not say "asked yesterday, answered today", which is exactly the
continuity a daily reader wants.

The previous Digest is already in the Message Store: Telegram forwards every
Digest Channel post into the linked Monitored Chat, where the Scraper stores it
with `forward_from_chat_id` set to the Digest Channel. So it can be read without
the Digest Service keeping any state of its own (ADR-0001 stays intact).

This collides with the invariant in `CONTEXT.md`: **content originating in the
Digest Channel is never input to a Digest.** The invariant exists because a
Digest that summarises yesterday's Digest reproduces its themes indefinitely.

We keep the invariant for the **discussion input** and carve one narrow
exception: the **Previous Digest** is passed to the model in a separate,
explicitly labelled reference block, which the prompt forbids it to retell.

- **What is read:** only the Digest covering the day before the Digest Window,
  recognised by its title line ("🗓 Дайджест чатов по маркировке за DD.MM.YYYY"),
  plus the continuation parts that immediately follow it. Other Digest Channel
  posts (e.g. third-party monitoring posts) are never read.
- **How it is used:** to mark 🔁 on themes that continue, and to link today's
  answer to yesterday's open question. Themes still come only from the day's
  own messages; a theme absent from them does not appear.
- **When it is missing** (a skipped day, the first run, a failed run) the Digest
  is produced exactly as before.

## Why this does not bring the loop back

The loop needed the Digest's text to enter as *conversation*: then yesterday's
themes were indistinguishable from today's and got summarised again. Here the
discussion input is unchanged — still filtered by `forward_from_chat_id` — and
the reference block reaches the model under its own heading with an explicit
"do not retell" rule. Only one day back is read, so even a model that leaks
something from it cannot compound it over many days: a leaked phrase would have
to be re-leaked every single day to persist.

## Considered options

- **Keep the Digest stateless.** Rejected: 🔁 stays dead and every day reads as
  if it were the first.
- **Store previous Digests in our own table.** Rejected: ADR-0001 keeps the
  Digest Service a read-only consumer with no database of its own; the Store
  already holds the text.
- **Feed the previous Digest as ordinary input.** Rejected: that is precisely
  the loop the invariant forbids.

## Consequences

- The Digest title line is now a contract: renaming it breaks the lookup until
  the lookup's title is changed in step (both come from one function).
- The weekly review (`weekly.py`) reads the week's Digests the same way. Unlike
  the daily Digest it *does* take its material from them — a review of
  Digests is its whole purpose — so for it they are input, not reference. The
  loop still cannot form: the review carries its own title ("📊 Обзор недели…"),
  which the Digest lookup never matches, so neither the daily Digest nor the
  next review ever reads a review back.
