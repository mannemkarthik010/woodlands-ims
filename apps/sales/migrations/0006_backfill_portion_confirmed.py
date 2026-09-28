"""
Mark the mappings made so far whose size per sale is known: every dish (one
sale is one dish) and every button whose name carries its size ("Sambar 16
oz"), which the mapping screen converted when it was mapped. The rest -- a
kitchen item sold with no size on the button, such as "Dosa Batter" -- stay
unconfirmed and take nothing out of stock until somebody says what one sale is.
"""

import re

from django.db import migrations

# The same pattern as apps.sales.naming.SIZE_RE; a migration must not import app code.
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[o0]z\b", re.I)


def backfill(apps, schema_editor):
    PosItem = apps.get_model("sales", "PosItem")
    for pos in PosItem.objects.filter(item__isnull=False).select_related("item"):
        known = pos.item.kind == "DISH" or bool(SIZE_RE.search(pos.pos_name))
        if known:
            pos.portion_confirmed = True
            pos.save(update_fields=["portion_confirmed"])


class Migration(migrations.Migration):
    dependencies = [("sales", "0005_portion_confirmed")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
