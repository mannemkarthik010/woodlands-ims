"""
How a POS name is read.

One copy of these rules, imported by the model, the service layer and the
import command. Two copies agree only until somebody edits one of them, and
the symptom of that is a group that looks right on one screen and wrong on
another.

These rules GROUP and they PRE-FILL. They never decide what something is.
"""

import re
from decimal import Decimal

# Prefixes that mark the same food sold under a promotion, a fixed-price
# night or a seasonal menu. Stripping them reveals the underlying dish.
PROMO_PREFIXES = [
    r"^\$10\s+",
    r"^dosanights\s*-\s*",
    r"^dosanights-\s*",
    r"^dosanights\s+",
    r"^nypf\s*-\s*",
    r"^new year's pf add on\s*-\s*",
    r"^weekday lunch\s+",
    r"^father's day\s+",
    r"^\d+\s*piece\s+",
]

PROMO_SUFFIXES = [
    r"\s*-?\s*diwali special.*$",
    r"\s*\(online\)$",
    r"\s*-\s*\d+\s*piece$",
    r"\s*\(dine-?in\)$",
    r"\s*\(take ?out\)$",
]

# "Coconut Chutney 16 oz" -> 16. The tub size, not the dish.
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*oz\b", re.I)

# What an ounce is, in the canonical units the Unit table converts to:
# millilitres for volume, grams for weight. Kept as constants rather than
# looked up, so that reading a label never depends on a row existing.
FLUID_OUNCE_ML = Decimal("29.5735")
OUNCE_G = Decimal("28.349523125")


def normalise(name: str) -> str:
    """
    Reduce a POS name to the food it is, for grouping only.

    Never used to decide what something IS -- only what to show next to what.
    """
    out = name.lower().strip()
    for pattern in PROMO_PREFIXES:
        out = re.sub(pattern, "", out)
    for pattern in PROMO_SUFFIXES:
        out = re.sub(pattern, "", out)
    out = SIZE_RE.sub("", out)
    out = re.sub(r"\bgrab and go\b|\bgrab-and-go\b", "", out)
    out = re.sub(r"[^a-z0-9 ]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    # A name made entirely of punctuation would reduce to nothing, and an
    # empty key is a group nobody can open. Fall back to the name itself.
    return out or re.sub(r"[/\\]+", " ", name.lower()).strip()


def size_in_name(pos_name: str) -> Decimal | None:
    """The number of ounces in the name, if there is one."""
    found = SIZE_RE.search(pos_name)
    return Decimal(found.group(1)) if found else None
