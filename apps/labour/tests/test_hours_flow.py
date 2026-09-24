"""
The tablet, walked the way a worker uses it: name, PIN, their week, record
a shift, check it, save it, done.

The clock is not frozen here, so shifts are recorded for yesterday -- the
flow is what is under test; the date rules are in test_record.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Location, Role, User
from apps.core.pins import MAX_FAILED_ATTEMPTS, set_pin
from apps.labour.models import Shift, Source
from apps.labour.views import SESSION_KEY


class HoursFlowTests(TestCase):
    def setUp(self):
        Location.objects.create(code="restaurant", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.tablet = User.objects.create_user("kitchen-tablet", password="pw", role=Role.KITCHEN)
        self.client.force_login(self.tablet)
        self.ravi = User.objects.create_user(
            "ravi", display_name="Ravi", role=Role.KITCHEN, date_joined=timezone.now() - timedelta(days=30)
        )
        set_pin(self.ravi, "4829")
        self.owner = User.objects.create_user("owner", display_name="Owner", role=Role.OWNER)
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def sign_in(self, pin="4829"):
        return self.client.post(reverse("hours_start"), {"person": self.ravi.pk, "pin": pin})

    def shift_form(self, **extra):
        data = {"day": self.yesterday.isoformat(), "start": "10:00", "end": "15:00"}
        data.update(extra)
        return data

    # Covers: FR-101, FR-102, FR-109.
    def test_the_whole_visit_from_name_to_saved(self):
        names = self.client.get(reverse("hours_start"))
        self.assertContains(names, f'<option value="{self.ravi.pk}"')
        self.assertNotContains(names, "Owner")  # owners never appear on the tablet

        self.assertRedirects(self.sign_in(), reverse("hours_me"))
        week = self.client.get(reverse("hours_me"))
        self.assertContains(week, "Ravi, your last 7 days")
        self.assertContains(week, "Nothing recorded for")

        # Check first: nothing is written until the worker says it is right.
        check = self.client.post(reverse("hours_add"), self.shift_form(step="check"))
        self.assertContains(check, "Is this right?")
        self.assertContains(check, "5\u00a0h\u00a000\u00a0m")
        self.assertFalse(Shift.objects.exists())

        saved = self.client.post(reverse("hours_add"), self.shift_form(step="save"))
        shift = Shift.objects.get()
        self.assertRedirects(saved, f"{reverse('hours_me')}?saved={shift.pk}")
        self.assertEqual((shift.employee, shift.source), (self.ravi, Source.ENTERED))
        self.assertContains(self.client.get(saved.url), "Saved:")

        # Done ends the visit; the next person starts from the names.
        self.client.post(reverse("hours_done"))
        self.assertRedirects(self.client.get(reverse("hours_me")), reverse("hours_start"))

    def test_no_changes_it_goes_back_to_the_filled_in_form(self):
        self.sign_in()
        back = self.client.post(reverse("hours_add"), self.shift_form(step="edit"))
        self.assertContains(back, "Record a shift")
        self.assertContains(back, 'value="10:00"')
        self.assertFalse(Shift.objects.exists())

    def test_catering_survives_the_check_step(self):
        self.sign_in()
        check = self.client.post(reverse("hours_add"), self.shift_form(step="check", catering="on"))
        self.assertContains(check, 'name="catering" value="on"')
        self.client.post(reverse("hours_add"), self.shift_form(step="save", catering="on"))
        self.assertTrue(Shift.objects.get().is_catering_event)

    def test_a_mistake_is_explained_on_the_form(self):
        self.sign_in()
        response = self.client.post(
            reverse("hours_add"), self.shift_form(step="check", end="10:00", start="17:00")
        )
        self.assertContains(response, "Please check the start and end times.")
        self.assertFalse(Shift.objects.exists())

    def test_a_wrong_pin_lets_nobody_in_and_too_many_lock_it(self):
        response = self.sign_in("1357")
        self.assertContains(response, "That PIN is not right.")
        self.assertRedirects(self.client.get(reverse("hours_me")), reverse("hours_start"))

        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            response = self.sign_in("1357")
        self.assertContains(response, "Too many wrong tries")
        self.assertContains(self.sign_in("4829"), "Too many wrong tries")

    def test_choosing_nobody_is_explained(self):
        response = self.client.post(reverse("hours_start"), {"person": "", "pin": "4829"})
        self.assertContains(response, "Please choose your name from the list.")

    def test_the_name_stays_chosen_after_a_wrong_pin(self):
        response = self.sign_in("1357")
        self.assertContains(response, f'<option value="{self.ravi.pk}" selected>')

    def test_a_visit_left_idle_expires(self):
        self.sign_in()
        session = self.client.session
        session[SESSION_KEY] = {"pk": self.ravi.pk, "at": 0}
        session.save()
        self.assertRedirects(self.client.get(reverse("hours_me")), reverse("hours_start"))

    def test_the_tablet_itself_must_be_signed_in(self):
        self.client.logout()
        response = self.client.get(reverse("hours_start"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_the_reminder_is_about_past_days_not_today(self):
        self.sign_in()
        empty = [d.day for d in self.client.get(reverse("hours_me")).context["empty_days"]]
        self.assertNotIn(timezone.localdate(), empty)
        self.assertIn(self.yesterday, empty)

    def test_the_date_can_be_prefilled_from_the_week_view(self):
        self.sign_in()
        response = self.client.get(reverse("hours_add"), {"date": self.yesterday.isoformat()})
        self.assertContains(response, f'value="{self.yesterday.isoformat()}"')
