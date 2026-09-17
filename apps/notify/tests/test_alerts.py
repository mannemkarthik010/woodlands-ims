"""
Telling somebody something, once.

The failure this protects against is not a message that fails to send. It is a
message that sends every morning until somebody mutes it, and then a real one
arrives into a muted channel and nobody sees it.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.catalog.models import Item, ItemKind, ParLevel, Unit, UnitKind
from apps.core.models import Location, Role
from apps.notify.models import Channel, Notification, Status
from apps.notify.services import notify, send_pending
from apps.stock.alerts import raise_alerts, shortfalls
from apps.stock.models import MovementType
from apps.stock.services import post_movement

User = get_user_model()


class AlertTests(TestCase):
    def setUp(self):
        self.lb = Unit.objects.create(
            code="lb", name="Pound", kind=UnitKind.WEIGHT, to_canonical=Decimal("453.59237")
        )
        self.store = Location.objects.create(code="storage", name="Devonshire St", kind=Location.Kind.STORAGE)
        self.owner = User.objects.create_user("jaspinder", role=Role.OWNER, mobile="+18185550100")
        self.dal = Item.objects.create(code="raw-urad", name="Urad dal", kind=ItemKind.RAW, base_unit=self.lb)
        ParLevel.objects.create(item=self.dal, location=self.store, quantity=Decimal("40"))

    def stock(self, quantity):
        post_movement(
            item=self.dal,
            location=self.store,
            quantity=Decimal(quantity),
            movement_type=MovementType.RECEIPT,
        )

    # Covers: FR-1301.
    def test_an_item_below_its_par_level_is_found(self):
        self.stock("12")
        found = shortfalls()
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].is_out)
        self.assertIn("down to 12 lb", found[0].sentence())

    # Covers: FR-1301.
    def test_an_item_above_its_par_level_is_not(self):
        self.stock("60")
        self.assertEqual(shortfalls(), [])

    def test_an_item_with_no_par_level_is_never_reported(self):
        """
        Inventing a par level would be noisy for the spice bought once a
        quarter and silent for the dal that turns over twice a week. It is a
        judgement about how this kitchen runs, and it is theirs to make.
        """
        rice = Item.objects.create(code="raw-rice", name="Idly rice", kind=ItemKind.RAW, base_unit=self.lb)
        post_movement(
            item=rice,
            location=self.store,
            quantity=Decimal("1"),
            movement_type=MovementType.RECEIPT,
        )
        self.assertEqual([s.par.item for s in shortfalls()], [self.dal])

    # Covers: FR-1301.
    def test_running_out_and_having_run_out_are_different_sentences(self):
        self.stock("12")
        self.assertIn("needs ordering", shortfalls()[0].sentence())
        post_movement(
            item=self.dal,
            location=self.store,
            quantity=Decimal("-12"),
            movement_type=MovementType.WASTE,
        )
        self.assertIn("has run out", shortfalls()[0].sentence())

    # Covers: FR-1306.
    def test_a_base_needs_making_rather_than_ordering(self):
        batter = Item.objects.create(
            code="prep-dosa-batter", name="Dosa batter", kind=ItemKind.PREPARED, base_unit=self.lb
        )
        ParLevel.objects.create(item=batter, location=self.store, quantity=Decimal("60"))
        post_movement(
            item=batter,
            location=self.store,
            quantity=Decimal("10"),
            movement_type=MovementType.PRODUCTION_YIELD,
        )
        base = [s for s in shortfalls() if s.is_base][0]
        self.assertIn("needs making", base.sentence())

    # Covers: FR-1303.
    def test_the_same_shortage_is_not_reported_twice_in_a_day(self):
        self.stock("12")
        first = raise_alerts()
        second = raise_alerts()
        self.assertEqual(first["told"], 1)
        self.assertEqual(second["told"], 0)
        self.assertEqual(second["already_said"], 1)
        self.assertEqual(Notification.objects.count(), 1)

    # Covers: FR-1303.
    def test_it_is_said_again_the_next_day(self):
        self.stock("12")
        raise_alerts()
        raise_alerts(on=date.today() + timedelta(days=1))
        self.assertEqual(Notification.objects.count(), 2)

    # Covers: FR-1302.
    def test_somebody_who_has_turned_alerts_off_is_not_messaged(self):
        self.owner.receives_alerts = False
        self.owner.save()
        self.stock("12")
        raise_alerts()
        message = Notification.objects.get()
        self.assertEqual(message.status, Status.SUPPRESSED)

    def test_staff_are_not_sent_the_owners_alerts(self):
        User.objects.create_user("cook", role=Role.KITCHEN, mobile="+18185550111")
        self.stock("12")
        raise_alerts()
        self.assertEqual([n.recipient for n in Notification.objects.all()], [self.owner])


class DeliveryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("jaspinder", role=Role.OWNER, mobile="+18185550100")

    def record(self, key="k1"):
        return notify(recipient=self.owner, kind="LOW_STOCK", body="Urad dal is low.", dedupe_key=key)

    # Covers: FR-1302.
    def test_nothing_is_sent_until_a_channel_is_switched_on(self):
        """
        Text messages cost the client money every month. A system that starts
        texting the moment it is deployed has spent somebody else's money
        without asking, so the default channel records and sends nothing.
        """
        self.record()
        send_pending()
        message = Notification.objects.get()
        self.assertEqual(message.channel, Channel.RECORDED)
        self.assertEqual(message.status, Status.SENT)

    @override_settings(NOTIFY_CHANNEL="SMS", TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="")
    def test_a_channel_that_cannot_send_records_why_instead_of_raising(self):
        self.record()
        send_pending()
        message = Notification.objects.get()
        self.assertEqual(message.status, Status.FAILED)
        self.assertIn("not configured", message.error_detail)

    @override_settings(
        NOTIFY_CHANNEL="SMS", TWILIO_ACCOUNT_SID="x", TWILIO_AUTH_TOKEN="y", TWILIO_FROM_NUMBER="+1"
    )
    def test_a_person_with_no_number_fails_loudly_rather_than_silently(self):
        self.owner.mobile = ""
        self.owner.save()
        self.record()
        send_pending()
        message = Notification.objects.get()
        self.assertEqual(message.status, Status.FAILED)
        self.assertIn("No mobile number", message.error_detail)

    def test_the_number_it_was_sent_to_is_kept_as_it_was(self):
        """Changing a number later must not rewrite what happened on Tuesday."""
        self.record()
        send_pending()
        self.owner.mobile = "+18185550999"
        self.owner.save()
        self.assertEqual(Notification.objects.get().sent_to, "")
