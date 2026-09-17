# ADR 0007 — Retrieval stays in the building; only the wording can leave

**Status:** Accepted · **Date:** 2026-09-17

## Context

The client asked for an assistant a cook can ask in plain language — "how do I
make this", "why is my batter not rising" — answered from the kitchen's own
records. Retrieval-augmented generation is the right shape for that: find the
relevant passages, then have a model word an answer from them.

Two constraints come from the client's own requirements, not from us:

- **FR-1013.** Recipe content is not sent to any third-party service without
  the client's explicit, informed consent.
- **FR-1009.** The assistant says "this has not been recorded — ask the chef"
  rather than guessing.

And one from the engagement: a model API costs money, which is the client's,
monthly, forever.

The usual architecture sends everything outward — embeddings of the whole
corpus to a vector service, then each question and its context to a model. For
a restaurant whose recipes are among the few things it actually owns, that is a
lot of exposure to accept by default.

## Decision

**The two halves are split, and only one of them can ever leave.**

*Retrieval* runs here, on the restaurant's own machine, over its own records.
BM25 over passages, in memory, no dependency, no service, no monthly cost and
no second copy of the recipes to keep in step. The corpus is a few hundred
short records in one kitchen's vocabulary, which is what BM25 is good at.

*Generation* is an engine behind two gates. `KNOWLEDGE_ENGINE` selects it, and
`KNOWLEDGE_CONSENT` must be set independently. The code refuses an external
engine without consent whatever the engine is set to — configuration gets
changed by accident, consent is a decision the client made.

**The default engine sends nothing.** It answers with the chef's own words,
under their own headings. For "how do I make sambar" that *is* the answer; a
model rewording it adds a chance to reword it wrongly.

**Unapproved records answer nothing.** Ingesting a document is not approving
it (FR-1010).

**A dish the restaurant sells, named in the question, with no record, is named
as missing** rather than answered from the nearest-looking recipe.

## Consequences

**The assistant is useful before any AI decision is made**, which means the
decision can be made calmly, by the owners, with the thing in front of them.

**Where BM25 is weak is known.** Wording that shares no words with the record —
"my batter will not rise" against a passage on fermentation temperature — will
miss. The interface takes a second ranker without disturbing the caller, so
adding semantic search is a new class rather than a rewrite. It is not worth
it at a few hundred records.

**Switching the model on is one environment variable and one conversation.**
What leaves at that point is the question and the handful of retrieved
passages — not the corpus, not the catalogue, not sales, not staff.

**If the model is unreachable, the cook still gets the chef's words.** The
failure falls back rather than failing.

**The honest cost.** A hand-tuned ranker needs its thresholds justified, and
they are: coverage is a proportion rather than a score because a BM25 score
moves with the size of the corpus, and a fixed floor would have made the
assistant mute on its first day and chatty on its hundredth. That is the sort
of bug that gets blamed on "the AI" and never diagnosed.
