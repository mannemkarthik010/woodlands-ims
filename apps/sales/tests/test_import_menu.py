"""
Menu import, with the traps this particular menu contains.
"""

from decimal import Decimal

from django.test import TestCase

from apps.sales.management.commands.import_menu import normalise
from apps.sales.models import PosItem


class NormaliseTests(TestCase):
    """
    Grouping only. This decides which POS names are shown together for a
    person to confirm -- it never creates a mapping by itself.
    """

    # Covers: FR-610.
    def test_one_dish_sold_five_ways_groups_together(self):
        names = [
            "Masala Dosa",
            "$10 Masala Dosa",
            "DosaNights-Masala Dosa",
            "NYPF - Masala Dosa",
            "Weekday Lunch Masala Dosa",
        ]
        self.assertEqual({normalise(n) for n in names}, {"masala dosa"})

    # Covers: FR-610.
    def test_sizes_collapse_to_the_same_base(self):
        names = ["Coconut Chutney 4 oz", "Coconut Chutney 8 oz", "Coconut Chutney 16 oz"]
        self.assertEqual({normalise(n) for n in names}, {"coconut chutney"})

    def test_different_dishes_do_not_collapse(self):
        self.assertNotEqual(normalise("Masala Dosa"), normalise("Mysore Masala Dosa"))
        self.assertNotEqual(normalise("Onion Dosa"), normalise("Onion Masala Dosa"))
        self.assertNotEqual(normalise("Sada Dosa"), normalise("Paper Dosa"))


class ImportBehaviourTests(TestCase):
    def setUp(self):
        self.rows = (
            "Item Name,Item UPC,Item Price,Cost,Markup,Revenue Class,Department,Status\n"
            "Masala Dosa,,11.75,0,0,Food,Dosa,Active\n"
            "Corkage Fee,,6.25,0,0,Non-taxable,Beverages,Active\n"
            "1Prix Fixe - Adult,,17.25,0,0,Food,DosaNights,Active\n"
            "DosaNights-Masala Dosa,,0,0,0,Food,DosaNights,Active\n"
            "Samosa,,1.5,0,0,Food,Appetizers,Inactive\n"
        )

    def _run(self, tmpdir):
        from django.core.management import call_command

        path = tmpdir / "menu.csv"
        path.write_text(self.rows)
        call_command("import_menu", str(path), verbosity=0)

    # Covers: FR-610.
    def test_non_food_lines_are_ignored_and_never_deplete(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            self._run(Path(d))
        self.assertTrue(PosItem.objects.get(pos_name="Corkage Fee").ignore)

    # Covers: FR-610.
    def test_prix_fixe_parent_is_ignored_so_meals_are_not_counted_twice(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            self._run(Path(d))
        # The wrapper is ignored...
        self.assertTrue(PosItem.objects.get(pos_name="1Prix Fixe - Adult").ignore)
        # ...but the course inside it, priced at zero, still counts.
        course = PosItem.objects.get(pos_name="DosaNights-Masala Dosa")
        self.assertFalse(course.ignore)
        self.assertEqual(course.default_price, Decimal("0"))

    def test_zero_priced_items_are_kept(self):
        """Depletion follows quantity, not revenue. A free course still eats food."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            self._run(Path(d))
        self.assertTrue(PosItem.objects.filter(pos_name="DosaNights-Masala Dosa").exists())

    # Covers: FR-211, FR-610.
    def test_inactive_menu_items_are_recorded_but_flagged(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            self._run(Path(d))
        self.assertFalse(PosItem.objects.get(pos_name="Samosa").active_on_pos)

    # Covers: FR-610.
    def test_reimporting_does_not_undo_a_human_decision(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            self._run(Path(d))
            row = PosItem.objects.get(pos_name="Masala Dosa")
            row.ignore = True  # somebody decided this deliberately
            row.save()
            self._run(Path(d))  # menu re-imported next month

        self.assertTrue(PosItem.objects.get(pos_name="Masala Dosa").ignore)
