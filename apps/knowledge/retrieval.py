"""
Finding the right passage, here, without sending anything anywhere.

This is the R in RAG, and it is deliberately the half that never leaves the
building. The chef's recipes are the restaurant's own; searching them is our
problem to solve, not a reason to hand them to somebody else (FR-1013).

WHY BM25 AND NOT A VECTOR DATABASE

The corpus is a few hundred short records in one kitchen's vocabulary. BM25 --
the ranking behind most search engines before neural retrieval, and still the
baseline everything is measured against -- handles that well, runs in
milliseconds over the whole corpus in memory, adds no dependency, no service,
no monthly cost and no second copy of the recipes to keep in step.

Where it is weak is wording that shares no words: "my batter will not rise"
against a passage about fermentation temperature. That is what embeddings are
for, and the interface below takes a second ranker without disturbing anything
-- `search()` is the only thing the rest of the system calls. Adding semantic
search when the corpus justifies it is a new class, not a rewrite.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from apps.knowledge.models import Passage

# Words that carry no signal in a kitchen's own corpus. Kept short on purpose:
# an over-eager stop list is how "how much water" becomes "water".
STOP = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "its",
    "me",
    "of",
    "on",
    "or",
    "that",
    "the",
    "then",
    "this",
    "to",
    "we",
    "what",
    "when",
    "which",
    "with",
    "you",
    "your",
}

K1 = 1.5  # term-frequency saturation
B = 0.75  # length normalisation
TITLE_WEIGHT = 3  # how many times a record's own title counts


def tokenise(text: str) -> list[str]:
    """
    Lowercase words, lightly singularised.

    "scoops" and "scoop" are the same word to a cook, and stemming properly
    would need a library and would mangle "dosa" and "idli" on the way.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for word in words:
        if word in STOP:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


@dataclass
class Hit:
    passage: Passage
    score: float
    matched: frozenset = frozenset()
    askable: frozenset = frozenset()

    @property
    def record(self):
        return self.passage.record

    @property
    def coverage(self) -> float:
        """
        How much of the answerable question this passage actually covers.

        A raw BM25 score cannot be compared against a fixed threshold, because
        it moves with the size of the corpus -- the same passage scores less in
        a corpus of two records than in a corpus of two hundred, which would
        make the assistant mute on its first day and chatty on its hundredth.
        Coverage is a proportion, so it means the same thing throughout.

        Terms that appear nowhere in the corpus are excluded from the
        denominator: "how do I make sambar" is a question about sambar, and the
        word "make" not appearing anywhere says nothing about whether the
        question was answered.
        """
        if not self.askable:
            return 0.0
        return len(self.matched) / len(self.askable)


class Index:
    """
    The whole published corpus, in memory.

    Rebuilt per search rather than cached. A few hundred passages is a few
    milliseconds, and a cache that can go stale while a cook is reading it is
    a worse problem than the milliseconds.
    """

    def __init__(self, passages=None):
        self.passages = list(
            passages
            if passages is not None
            else Passage.objects.filter(record__approved_at__isnull=False).select_related(
                "record", "record__item"
            )
        )
        # The title counts three times. Somebody asking "how do I make sambar"
        # wants the record called Sambar, not the paragraph elsewhere that
        # happens to list sambar among eight other things. Weighting by
        # repetition keeps the ranking function itself unchanged.
        self.tokens = [
            tokenise(p.record.title) * TITLE_WEIGHT + tokenise(f"{p.heading} {p.text}") for p in self.passages
        ]
        self.counts = [Counter(t) for t in self.tokens]
        self.lengths = [len(t) for t in self.tokens]
        self.average = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

        seen = Counter()
        for tokens in self.tokens:
            seen.update(set(tokens))
        self.documents = len(self.passages)
        self.seen = seen

    def idf(self, term: str) -> float:
        n = self.seen.get(term, 0)
        if not n:
            return 0.0
        return math.log(1 + (self.documents - n + 0.5) / (n + 0.5))

    def search(self, query: str, limit: int = 4) -> list[Hit]:
        terms = tokenise(query)
        if not terms or not self.passages:
            return []

        # Terms the corpus has any chance of answering. A word that appears
        # nowhere is evidence of a gap, not of a bad match.
        askable = frozenset(term for term in terms if self.seen.get(term))

        hits = []
        for index, counts in enumerate(self.counts):
            score = 0.0
            matched = set()
            for term in terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                matched.add(term)
                normalised = 1 - B + B * (self.lengths[index] / (self.average or 1))
                score += self.idf(term) * (frequency * (K1 + 1)) / (frequency + K1 * normalised)
            if score > 0:
                hits.append(
                    Hit(
                        passage=self.passages[index],
                        score=round(score, 4),
                        matched=frozenset(matched),
                        askable=askable,
                    )
                )

        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


def search(query: str, limit: int = 4) -> list[Hit]:
    return Index().search(query, limit=limit)
