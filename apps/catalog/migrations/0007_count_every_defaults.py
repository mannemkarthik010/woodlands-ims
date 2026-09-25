"""
Put every existing item on its count: prepared items daily, vegetables
weekly, other groceries and packaging monthly, dishes never. The same rule
new items get from Item.save(); written out here because a migration must
not import the model's methods.
"""

from django.db import migrations


def set_defaults(apps, schema_editor):
    Item = apps.get_model("catalog", "Item")
    for item in Item.objects.select_related("category").filter(count_every=""):
        category = (item.category.name if item.category_id else "").lower()
        if item.kind == "DISH":
            item.count_every = "NEVER"
        elif item.kind == "PREPARED":
            item.count_every = "DAILY"
        elif any(word in category for word in ("vegetable", "produce", "fresh")):
            item.count_every = "WEEKLY"
        else:
            item.count_every = "MONTHLY"
        item.save(update_fields=["count_every"])


class Migration(migrations.Migration):
    dependencies = [("catalog", "0006_count_every")]
    operations = [migrations.RunPython(set_defaults, migrations.RunPython.noop)]
