"""
Publishing the kitchen's recipes, and keeping the replaced ones quiet.

Two versions of one recipe that both answer is worse than one: the cook gets
whichever ranks higher, and the old sheet still has the garlic in it.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.core.models import User
from apps.knowledge.models import Record

DOC = """## Tomato Chutney
Whole peeled tomato 4 cans. No garlic.

## Tomato Chutney (older sheet)
6 cans, ginger-garlic paste.
"""


class IngestTests(TestCase):
    def setUp(self):
        self.karthik = User.objects.create_user("karthik")

    def ingest(self, **options):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "recipes.md"
            path.write_text(DOC)
            call_command("ingest_recipes", str(path), verbosity=0, **options)

    # Covers: FR-1010.
    def test_approval_names_the_chefs_and_leaves_the_replaced_sheet_unpublished(self):
        self.ingest(
            approve=True,
            approver="karthik",
            note="Edwin and Anderson, passed on by Karthik",
            leave_out=["Tomato Chutney (older sheet)"],
        )
        new = Record.objects.get(title="Tomato Chutney")
        old = Record.objects.get(title="Tomato Chutney (older sheet)")
        self.assertTrue(new.is_published)
        self.assertEqual(new.approved_by, self.karthik)
        self.assertEqual(new.approved_note, "Edwin and Anderson, passed on by Karthik")
        self.assertFalse(old.is_published)

    def test_leaving_out_a_title_that_is_not_there_is_refused(self):
        with self.assertRaises(CommandError):
            self.ingest(approve=True, approver="karthik", leave_out=["Tomato Chutny"])
        self.assertFalse(Record.objects.exists())

    def test_without_approve_nothing_is_published(self):
        self.ingest()
        self.assertFalse(Record.objects.filter(approved_at__isnull=False).exists())
