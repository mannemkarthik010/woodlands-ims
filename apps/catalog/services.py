"""
Operations on the catalogue that are too consequential to be an admin edit.

Both of these exist because of what happened the first time 318 till names
were mapped by hand in one sitting: the menu spells "Chana Masala" two ways,
so one food became two items; and a tub of sambar sold over the counter looked
like a dish, so it became a separate thing from the sambar that goes into
Idly Sambar. It is the same pot. Neither mistake is avoidable by being careful
-- there were 250 decisions -- so both need a way back.

The rule both obey: **history is never rewritten.** An item that has stock
movements against it has a past, and merging or re-basing it would silently
change what the ledger says happened. Both operations refuse in that case and
say why.
"""

from __future__ import annotations

from django.db import transaction

from apps.catalog.models import Item, ItemAlias, ItemKind, Unit


class CatalogError(Exception):
    pass


def _has_history(item: Item) -> bool:
    from apps.stock.models import StockMovement

    return StockMovement.objects.filter(item=item).exists()


@transaction.atomic
def merge_items(source: Item, target: Item, *, user=None) -> dict:
    """
    Fold `source` into `target` and retire it.

    The source's name survives as an alias on the target, which is the point:
    somebody typed "Channa Masala" into the till for years and will type it
    into the search box too. Losing the spelling would make the item harder to
    find, not cleaner (FR-202).
    """
    if source.pk == target.pk:
        raise CatalogError("An item cannot be merged into itself.")
    if _has_history(source):
        raise CatalogError(
            f"{source.name} already has stock movements against it. Merging would "
            f"rewrite what the ledger says happened. Correct the movements instead."
        )

    from apps.catalog.models import ItemMeasure, ParLevel, Recipe, RecipeLine
    from apps.sales.models import PosItem

    moved = {
        "pos_items": PosItem.objects.filter(item=source).update(item=target),
        "recipe_lines": RecipeLine.objects.filter(component=source).update(component=target),
        "recipes": Recipe.objects.filter(item=source).update(item=target),
        "measures": ItemMeasure.objects.filter(item=source).update(item=target),
        "par_levels": ParLevel.objects.filter(item=source).update(item=target),
        "aliases": ItemAlias.objects.filter(item=source).update(item=target),
    }

    if source.name.lower() != target.name.lower():
        ItemAlias.objects.get_or_create(
            item=target, alias=source.name, defaults={"source": "Merged from a duplicate item"}
        )

    # Retired, not deleted. A code that was in use should not become available
    # again, and somebody will want to know where the item went.
    source.is_active = False
    source.notes = (source.notes + f"\nMerged into {target.name} ({target.code}).").strip()
    source.save(update_fields=["is_active", "notes", "updated_at"])

    return moved


@transaction.atomic
def convert_to_component(item: Item, unit: Unit, *, user=None) -> int:
    """
    Turn a dish into a prepared component measured in a real unit.

    A dish is a plate of food: one sale, one dish, quantities in its recipe. A
    prepared component is a quantity of something the kitchen made -- sambar,
    batter, chutney -- which is both sold over the counter by the tub and used
    inside other dishes. Anything sold in ounces is the second kind, and
    leaving it as the first means a 4 oz tub and a 32 oz tub deplete the same
    amount, and the tub sambar is a different thing from the dish sambar.

    Returns the number of POS lines whose quantity per sale was recomputed.
    """
    if _has_history(item):
        raise CatalogError(
            f"{item.name} already has stock movements in {item.base_unit.code}. Changing "
            f"the unit it is held in would change every one of them. This needs a data "
            f"migration, not a screen."
        )

    from apps.sales.models import PosItem
    from apps.sales.services import quantity_for

    item.kind = ItemKind.PREPARED
    item.base_unit = unit
    item.is_stocked = True  # unlike a dish, this is held on a shelf
    item.full_clean(exclude=["code"])
    item.save(update_fields=["kind", "base_unit", "is_stocked", "updated_at"])

    recomputed = 0
    for pos in PosItem.objects.filter(item=item):
        quantity = quantity_for(pos.pos_name, item)
        if quantity != pos.quantity_per_sale:
            pos.quantity_per_sale = quantity
            pos.save(update_fields=["quantity_per_sale", "updated_at"])
            recomputed += 1
    return recomputed


@transaction.atomic
def retire_item(item: Item, *, reason: str = "") -> None:
    """
    Take an item out of use without deleting it.

    Deletion is not offered anywhere in this system. A code that was in use
    should not become available again, and "where did that item go?" should
    always have an answer. An item with movements against it especially: the
    ledger refers to it, and the ledger is the record.
    """
    item.is_active = False
    if reason:
        item.notes = (item.notes + "\n" + reason).strip()
    item.save(update_fields=["is_active", "notes", "updated_at"])
