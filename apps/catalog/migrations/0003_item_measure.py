"""
PurchaseUnit becomes ItemMeasure.

The table always held "a named quantity of this item, worth this much in base
units". It was named for the only use we had then. The kitchen's recipes are
written in scoops and spoons, and those need exactly the same conversion --
per item, because a scoop of toor dal is 32 oz and a scoop of sambar powder is
14 oz. Calling that a "purchase unit" would have been a lie in the schema, and
a lie in the schema is the kind that lasts.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_initial"),
        ("stock", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="PurchaseUnit", new_name="ItemMeasure"),
        migrations.AlterField(
            model_name="itemmeasure",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="measures",
                to="catalog.item",
            ),
        ),
        migrations.AddField(
            model_name="itemmeasure",
            name="kind",
            field=models.CharField(
                choices=[("PURCHASE", "How it is bought"), ("KITCHEN", "How the kitchen measures it")],
                db_index=True,
                default="PURCHASE",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="itemmeasure",
            name="measured_on",
            field=models.DateField(
                blank=True, help_text="When this was last put on a scale.", null=True
            ),
        ),
        migrations.AlterField(
            model_name="itemmeasure",
            name="supplier",
            field=models.ForeignKey(
                blank=True,
                help_text="Only meaningful for a purchase pack. A scoop has no supplier.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="item_measures",
                to="core.supplier",
            ),
        ),
        migrations.AlterField(
            model_name="itemmeasure",
            name="is_approximate",
            field=models.BooleanField(
                default=False,
                help_text="A handful of curry leaves, where the weight is nominal rather than measured.",
            ),
        ),
    ]
