# ADR 0006 — A message is recorded first and carried second

**Status:** Accepted · **Date:** 2026-09-17

## Context

The owners asked for two things that are the same thing underneath: tell us
when stock is running out, and tell the person who has to make a batter that
they have been asked to make it.

Both are easy to build badly. The obvious version calls a messaging service
from wherever the condition is noticed — the stock posting code sends a text
when a balance drops below par. That version has three problems, and all of
them show up in production rather than in testing.

A telephone network is unreliable, so a stock count would fail because Twilio
was slow. There is no record of what was said, so "I never got told" cannot be
answered. And the channel is welded in: choosing SMS today means rewriting the
domain to add Slack tomorrow.

There is also a cost. SMS is the client's money, monthly, forever.

## Decision

**Recording a message and delivering it are two separate steps.**

`notify()` writes a `Notification` row. That is all the stock and production
code ever does — it records that somebody should be told, and its
responsibility ends. A separate step, on a schedule, carries whatever has been
recorded on whichever channel is configured.

**The channel is configuration, not code.** `NOTIFY_CHANNEL` selects it. The
default is `RECORDED`, which writes the message and sends nothing.

**No channel is ever allowed to raise into the caller.** A failure is written
to the row, with the reason, and the message stays pending.

**Every message carries a `dedupe_key`, and the key is unique.** An item that
is low on Tuesday is still low on Wednesday.

## Consequences

**Nothing is sent until somebody turns it on.** Text messages cost the client
money every month, and a system that starts texting the day it is deployed has
spent somebody else's money without asking. Switching them on is one
environment variable, not a release — and until the owners have agreed to it,
every message is still recorded, visible, and complete.

**"I never got told" is answerable.** There is a row, with a status, a time, a
number and, if it failed, why.

**Alert fatigue is designed against rather than hoped against.** One message
per item per day. The alternative — a message every time the checker runs — is
how a system's alerts get muted in a week, and a muted alert is worse than no
alert, because everybody believes they are covered.

**The cost of adding Slack or email later is one class.** Each channel is an
object with `send()` and `address()`; nothing else in the system knows which
one is in use.

**What this costs.** Messages go out on a schedule rather than the instant the
condition occurs. For "the batter is running low", minutes do not matter. If
something ever genuinely needs to be instant, it can be delivered inline —
the design allows it, it is just not the default.
