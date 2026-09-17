"""
Asking the kitchen's records a question.
"""

from __future__ import annotations

import logging

from django.db import models

from apps.knowledge import engines, retrieval
from apps.knowledge.models import Outcome, Question, Record

log = logging.getLogger(__name__)

# How much of the answerable question a passage has to cover before it is
# allowed to answer. Half is deliberately generous -- the check that stops the
# confident wrong answer is `named_but_unrecorded` below, and this one only
# has to keep out passages that share a single incidental word.
#
# A proportion rather than a score, because a BM25 score moves with the size of
# the corpus: the same passage scores less among two records than among two
# hundred, so a fixed floor would make the assistant mute on its first day.
COVERAGE = 0.5


def named_but_unrecorded(text: str):
    """
    A dish the restaurant sells, named in the question, with nothing written
    down about it.

    This exists because of one specific failure. Asked "how do I make a masala
    dosa", ranked search happily returns Butter Masala: the word "masala" is
    right there, the score is respectable, and the answer is completely wrong.
    A cook would follow it.

    The catalogue already knows what this kitchen sells, so it can be used as
    the check that ranking cannot make on its own: if the question names a real
    dish and that dish has no approved record, the answer is not the
    nearest-looking recipe. It is that nobody has written this one down —
    which is also the most useful thing the chef could be told.
    """
    from apps.catalog.models import Item, ItemKind

    asked = set(retrieval.tokenise(text))
    if not asked:
        return None

    best = None
    for item in Item.objects.filter(
        kind__in=[ItemKind.DISH, ItemKind.PREPARED], is_active=True
    ).select_related("base_unit"):
        words = retrieval.tokenise(item.name)
        if not words or not set(words) <= asked:
            continue
        # The longest name that fits the question wins: "masala dosa" beats
        # "dosa", which is what stops a two-word dish matching as a one-word one.
        if best is None or len(words) > len(retrieval.tokenise(best.name)):
            best = item

    if best is None:
        return None

    if (
        Record.objects.filter(approved_at__isnull=False)
        .filter(models.Q(item=best) | models.Q(title__iexact=best.name))
        .exists()
    ):
        return None

    # A record that matches the question MORE fully than the bare item name
    # wins. "What goes in butter masala" names an item called Masala, which has
    # no record -- but there is a record called Butter Masala, and two words of
    # the question beat one. Without this, having a dish named with a common
    # word would silence every question that happened to contain it.
    wanted = len(retrieval.tokenise(best.name))
    for record in Record.objects.filter(approved_at__isnull=False).only("title"):
        words = retrieval.tokenise(record.title)
        if words and set(words) <= asked and len(words) >= wanted:
            return None

    return best


def ask(text: str, *, user=None, limit: int = 4) -> Question:
    """
    Answer from the records, and keep both the question and the answer.

    When nothing relevant is found, the answer is that nothing has been
    recorded. That is a real answer and the most important one this module
    gives -- it is the difference between a system that admits the gap and one
    that invents a recipe for a dish nobody wrote down (FR-1009).
    """
    text = " ".join((text or "").split())

    missing = named_but_unrecorded(text)
    if missing is not None:
        return Question.objects.create(
            asked_by=user,
            text=text[:400],
            answer=(
                f"Nothing has been recorded for {missing.name} yet — ask the chef.\n\n"
                "There are records for other dishes, but answering from those would mean "
                "guessing, and a guess about how to make something will be followed."
            ),
            outcome=Outcome.NOT_RECORDED,
            answered_by="none",
        )

    hits = [hit for hit in retrieval.search(text, limit=limit) if hit.coverage >= COVERAGE]

    if not hits:
        return Question.objects.create(
            asked_by=user,
            text=text[:400],
            answer=engines.NOT_RECORDED,
            outcome=Outcome.NOT_RECORDED,
            answered_by="none",
        )

    engine = engines.current()
    try:
        answer = engine.answer(text, hits)
        outcome = Outcome.ANSWERED
        name = engine.name
    except Exception as problem:  # pragma: no cover - network
        # A model being unreachable must not mean a cook gets nothing. The
        # chef's own words are still right there.
        log.exception("Engine %r failed; answering from the passages instead.", engine.name)
        answer = engines.Passages().answer(text, hits)
        outcome = Outcome.ANSWERED
        name = f"records (after {type(problem).__name__})"

    question = Question.objects.create(
        asked_by=user, text=text[:400], answer=answer, outcome=outcome, answered_by=name
    )
    question.passages.set([hit.passage for hit in hits])
    return question
