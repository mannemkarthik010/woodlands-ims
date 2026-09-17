"""
Reading a recipe record into the shape a cook expects.

The records as written are runs of "ingredient quantity · ingredient quantity",
which is how somebody types up a list quickly and not how anybody reads one.
Handing that back verbatim is technically faithful and practically useless: a
cook mid-service is looking for one line in a paragraph.

So this parses what is there into ingredients, sections and notes. It does not
add anything. Two things follow from that, and both matter:

WHERE THERE IS NO METHOD, IT SAYS SO. Ramesh's document is quantities only --
no steps, no order, no timings, no temperatures. Every model ever trained
could write a plausible method for sambar, and every one of them would be
guessing at how THIS kitchen makes it. The gap is shown as a gap, because a
cook who is told "the steps are not written down" goes and asks; a cook given
an invented method cooks it wrong and does not know.

WHERE A MEASURE HAS BEEN WEIGHED, IT IS SHOWN ALONGSIDE. "3 scoop" is what the
chef wrote and stays what he wrote, but the kitchen weighed a scoop of toor dal
at 32 oz on 16 September, so the line can also say what that is in pounds.
Where nothing has been weighed, nothing is shown -- an unweighed scoop stays a
scoop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

FRACTIONS = {"½": Decimal("0.5"), "¼": Decimal("0.25"), "¾": Decimal("0.75"), "⅓": Decimal("1") / 3}

# "3 scoop", "16 oz", "½ pot", "7 pieces", "a pinch", "handful", "to taste"
QUANTITY = re.compile(
    r"\s+((?:\d+(?:\.\d+)?|[½¼¾⅓]|\d+\s*[–-]\s*\d+)\s*[a-z%\"'\.]*(?:\s+[a-z]+)?|"
    r"handful|a pinch|to taste|as needed|a few|as written)$",
    re.I,
)

# "**Tempering:**" and "**Yield:**" start a new section wherever they appear,
# including mid-paragraph -- which is where they actually appear, because the
# document was typed as prose rather than as a form.
SECTION = re.compile(r"\*\*([^*]+?):?\*\*")

# "(approx 6-8 oz)", "(2 pcs)" -- an aside about the ingredient, not its
# quantity. Held back while the quantity is read, then put back on the line.
ASIDE = re.compile(r"\s*\(([^)]*)\)\s*$")

# Lines that are instructions rather than ingredients.
STEPWORDS = re.compile(
    r"\b(heat|add|stir|cook|boil|simmer|grind|soak|fry|temper|steam|puree|"
    r"mix|pour|drain|roast|season|serve|reheat|until|then)\b",
    re.I,
)


@dataclass
class Ingredient:
    name: str
    quantity: str = ""
    weighed: str = ""  # what the kitchen measured that quantity to be
    aside: str = ""  # whatever was in brackets after it


@dataclass
class Section:
    heading: str = ""
    ingredients: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.ingredients and not self.notes


def split_quantity(text: str) -> tuple[str, str, str]:
    """ "Toor dal 3 scoop" -> ("Toor dal", "3 scoop", ""). Brackets come back separately."""
    text = " ".join(text.split())
    aside = ""
    bracket = ASIDE.search(text)
    if bracket:
        aside = bracket.group(1).strip()
        text = text[: bracket.start()].strip()

    found = QUANTITY.search(text)
    if not found:
        return text, "", aside
    return text[: found.start()].strip(" ,;"), found.group(1).strip(), aside


def readable(total: Decimal, unit: str) -> str:
    """
    A weight somebody can act on.

    0.0812 lb of mustard seed is arithmetic, not an instruction. Anything under
    a quarter of a pound reads better in ounces, and nothing needs more than
    two decimal places in a kitchen.
    """
    if unit == "lb" and total < Decimal("0.25"):
        total, unit = total * 16, "oz"
    rounded = total.quantize(Decimal("0.01"))
    text = f"{rounded:f}".rstrip("0").rstrip(".")
    return f"{text} {unit}"


def measured(name: str, quantity: str) -> str:
    """
    What "3 scoop" of this ingredient actually weighs, if anybody weighed it.

    Silent when they have not. A conversion the kitchen did not measure is a
    number somebody made up, and it would be indistinguishable on the page
    from the ones they did.
    """
    from apps.catalog.models import Item, ItemMeasure, MeasureKind

    parts = quantity.split(maxsplit=1)
    if len(parts) != 2:
        return ""
    amount, measure = parts
    try:
        count = FRACTIONS.get(amount) or Decimal(amount)
    except (InvalidOperation, TypeError):
        return ""

    item = (
        Item.objects.filter(name__iexact=name).first()
        or Item.objects.filter(aliases__alias__iexact=name).first()
    )
    if item is None:
        return ""

    row = (
        ItemMeasure.objects.filter(
            item=item, kind=MeasureKind.KITCHEN, name__iexact=measure.rstrip("s")
        ).first()
        or ItemMeasure.objects.filter(item=item, kind=MeasureKind.KITCHEN, name__iexact=measure).first()
    )
    if row is None:
        return ""

    return readable(count * row.quantity_in_base_units, item.base_unit.code)


def parse(body: str) -> list[Section]:
    """Turn a record into sections of ingredients and notes."""
    sections = [Section()]

    flat = " ".join(body.split())
    # Split wherever a bold label appears. re.split keeps the labels, so the
    # pieces alternate: text, label, text, label, text.
    pieces = SECTION.split(flat)

    for index, piece in enumerate(pieces):
        if index % 2:  # a label
            sections.append(Section(heading=piece.strip()))
            continue

        current = sections[-1]
        for part in [p.strip() for p in piece.split("·") if p.strip()]:
            if STEPWORDS.search(part) and len(part.split()) > 4:
                current.notes.append(part)
                continue
            name, quantity, aside = split_quantity(part)
            if not name:
                current.notes.append(part)
                continue
            current.ingredients.append(
                Ingredient(
                    name=name,
                    quantity=quantity,
                    weighed=measured(name, quantity),
                    aside=aside,
                )
            )

    return [s for s in sections if not s.is_empty]


def has_method(sections: list[Section]) -> bool:
    """Whether anything here actually says how to make it."""
    return any(len(note.split()) > 6 for section in sections for note in section.notes)


def as_text(title: str, sections: list[Section]) -> str:
    """
    The same thing as plain text, for the terminal and for a text message.

    One rendering, so the answer a cook reads on the tablet is the answer the
    chef sees quoted back at him.
    """
    lines = [title, "=" * len(title), ""]

    for section in sections:
        if section.heading:
            lines += [f"{section.heading}", "-" * len(section.heading)]
        elif section.ingredients:
            lines += ["Ingredients", "-----------"]
        width = max((len(i.name) for i in section.ingredients), default=0)
        for item in section.ingredients:
            tail = f"   ({item.weighed})" if item.weighed else ""
            if item.aside:
                tail += f"   [{item.aside}]"
            lines.append(f"  {item.name.ljust(width)}   {item.quantity}{tail}".rstrip())
        for note in section.notes:
            lines.append(f"  {note}")
        lines.append("")

    if not has_method(sections):
        lines += [
            "Method",
            "------",
            "  Not recorded. This document gives quantities only — no steps, no",
            "  order, no timings. Ask the chef, and write down what he says.",
            "",
        ]
    return "\n".join(lines).rstrip()
