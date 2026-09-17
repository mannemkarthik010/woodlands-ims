"""
What the kitchen has not written down yet.

Every time the assistant says "ask the chef", that is a question somebody
needed answered and nobody had recorded. Left as it is, each refusal is a
small private failure. Collected, they are a work list -- and a short one.

Three kinds of gap, in the order they are worth closing:

  SERVINGS PER BATCH.  One number per recipe, and until it exists no base
      recipe can be scaled to a headcount at all. It is also the cheapest to
      collect: somebody counts bowls out of one chafer, once.

  THE METHOD.  Ramesh's document is quantities only. Every recipe in it can
      say what goes in and none of them can say what to do.

  MEASURES NOBODY HAS WEIGHED.  A cap, a pot, a bucket of something other
      than sambar, a large ladle. Each one blocks every recipe that uses it,
      so weighing one measure fixes many recipes at once.

The point of this module is that the chef should be asked once, from one
sheet, rather than interrupted twenty times over three weeks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from apps.knowledge import format as recipe
from apps.knowledge.models import Outcome, Question, Record

# Units that are already real -- an ounce needs nobody to weigh it.
REAL = {
    "oz",
    "lb",
    "g",
    "kg",
    "ml",
    "l",
    "gallon",
    "quart",
    "pint",
    "piece",
    "can",
    "cube",
    "packet",
    "bottle",
    "jar",
    "tray",
    "box",
    "bag",
    "case",
}

PLURALS = {"bunches": "bunch", "boxes": "box", "cases": "case", "inches": "inch"}


def kitchen_measure(quantity: str, ingredient: str = "") -> str:
    """
    The kitchen's own measure in a quantity, if there is one.

    "3 scoop" gives "scoop". "16 oz" gives nothing -- an ounce is already a
    unit and needs nobody to weigh it. "to taste" and "a pinch" give nothing
    either: they are judgements, not measures, and asking the chef to weigh a
    pinch would be the sort of question that makes somebody stop answering.
    """
    words = quantity.strip().split()
    if len(words) < 2:
        return ""
    first = words[0]
    if not (first[0].isdigit() or first[0] in "½¼¾⅓"):
        return ""

    word = words[-1].lower().strip(".,")
    word = PLURALS.get(word) or (word[:-1] if word.endswith("s") and not word.endswith("ss") else word)
    if word in REAL:
        return ""
    # "Chopped onion 15–20 onions" counts onions, it does not measure them in
    # some vessel called an onion. When the word is the ingredient, it is a
    # count and there is nothing to weigh.
    if word and word in ingredient.lower():
        return ""
    return word


@dataclass
class Gap:
    record: Record
    needs_servings: bool = False
    needs_method: bool = False
    unweighed: list = field(default_factory=list)

    @property
    def is_clear(self) -> bool:
        return not (self.needs_servings or self.needs_method or self.unweighed)


def survey() -> list[Gap]:
    """Every record, and what is missing from it."""
    out = []
    for record in Record.objects.order_by("title"):
        sections = recipe.parse(record.body)
        gap = Gap(record=record)

        # A recipe with a per-serving line does not need a batch figure to be
        # useful, though it still cannot say how many batches to make.
        gap.needs_servings = record.servings_per_batch is None
        gap.needs_method = not recipe.has_method(sections)

        for section in sections:
            for item in section.ingredients:
                measure = kitchen_measure(item.quantity, item.name)
                if measure and not item.weighed:
                    gap.unweighed.append(f"{measure} of {item.name.lower()}")

        gap.unweighed = sorted(set(gap.unweighed))
        out.append(gap)
    return out


def measures_wanted(gaps: list[Gap]) -> list[tuple[str, int]]:
    """Which vessels appear in the most lines we cannot yet convert."""
    counted = Counter(line.split(" of ")[0] for gap in gaps for line in gap.unweighed)
    return counted.most_common()


def pairs_wanted(gaps: list[Gap]) -> list[str]:
    """Every "measure of ingredient" still waiting to be weighed."""
    return sorted({line for gap in gaps for line in gap.unweighed})


def vessels_wanted(gaps: list[Gap]) -> list[tuple[str, int]]:
    """
    The vessels themselves, unmeasured, commonest first.

    There is one scoop and one spoon in that kitchen. Asking what a spoon of
    each of thirty-two ingredients weighs is a morning of somebody's life and
    will not happen; asking how much the spoon holds is one measurement with a
    jug, and it is true of everything that goes in it afterwards.
    """
    from apps.catalog.models import Vessel

    known = {v.name.lower() for v in Vessel.objects.exclude(volume_ml=None)}
    counted = Counter(line.split(" of ")[0] for gap in gaps for line in gap.unweighed)
    return [(name, count) for name, count in counted.most_common() if name.lower() not in known]


# Ingredients where the WEIGHT is worth asking for on top of the vessel's
# volume: the bulk of a recipe, where being ten per cent out is pounds of dal
# rather than grams of asafoetida.
BULK = {"scoop", "bar", "can", "bucket", "packet"}


def weights_wanted(gaps: list[Gap]) -> list[str]:
    """
    The per-ingredient weighings still worth somebody's time.

    Only the bulk measures. A scoop of chana dal decides whether the system
    thinks you used four pounds or six; a spoon of fennel decides nothing, and
    the vessel's volume covers it well enough.
    """
    return sorted({line for gap in gaps for line in gap.unweighed if line.split(" of ")[0].lower() in BULK})


def questions_nobody_could_answer(limit: int = 20) -> list[Question]:
    """
    What the kitchen actually asked and the records could not answer.

    A better prompt for the chef than any list we could write, because these
    are the things somebody needed while cooking.
    """
    return list(Question.objects.filter(outcome=Outcome.NOT_RECORDED).order_by("-created_at")[:limit])
