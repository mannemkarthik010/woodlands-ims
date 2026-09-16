"""
Fill in group_key for POS items imported before the field existed.

The normalising rules are copied here rather than imported from
apps.sales.services on purpose. A migration has to keep doing what it did on
the day it ran; if it imported live code, improving the grouping rules next
month would silently change history.
"""

import re

from django.db import migrations

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
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*oz\b", re.I)


def normalise(name):
    out = name.lower().strip()
    for pattern in PROMO_PREFIXES:
        out = re.sub(pattern, "", out)
    for pattern in PROMO_SUFFIXES:
        out = re.sub(pattern, "", out)
    out = SIZE_RE.sub("", out)
    out = re.sub(r"\bgrab and go\b|\bgrab-and-go\b", "", out)
    out = re.sub(r"[^a-z0-9 ]+", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def fill(apps, schema_editor):
    PosItem = apps.get_model("sales", "PosItem")
    rows = []
    for pos in PosItem.objects.all().only("id", "pos_name", "group_key"):
        key = normalise(pos.pos_name)
        if pos.group_key != key:
            pos.group_key = key
            rows.append(pos)
    PosItem.objects.bulk_update(rows, ["group_key"], batch_size=200)


def clear(apps, schema_editor):
    apps.get_model("sales", "PosItem").objects.update(group_key="")


class Migration(migrations.Migration):
    dependencies = [("sales", "0003_pos_item_group_key")]
    operations = [migrations.RunPython(fill, clear)]
