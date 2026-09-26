"""
"My name isn't on the list — add me", walked on the tablet.
"""

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Role, User
from apps.core.pins import check_pin, set_pin
from apps.labour.views import PENDING_KEY


class AddMeFlowTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("kitchen-tablet", password="pw"))
        self.ravi = User.objects.create_user(
            "ravi", first_name="Ravi", last_name="Kumar", display_name="Ravi Kumar", role=Role.KITCHEN
        )
        set_pin(self.ravi, "4829")

    def add(self, first, last, pin="5831", again=None):
        return self.client.post(
            reverse("hours_new"),
            {"step": "add", "first": first, "last": last, "pin": pin, "pin_again": again or pin},
        )

    # Covers: FR-1201.
    def test_a_new_person_adds_themselves_and_goes_straight_to_their_hours(self):
        response = self.add("Meera", "Nair")
        self.assertRedirects(response, f"{reverse('hours_me')}?welcome=1")
        self.assertContains(self.client.get(response.url), "You're added, Meera Nair")
        # Nobody is asked about days before they were on the list.
        self.assertEqual(self.client.get(response.url).context["empty_days"], [])
        meera = User.objects.get(last_name="Nair")
        self.assertTrue(meera.needs_review)
        # And from now on they are on the list.
        self.assertContains(self.client.get(reverse("hours_start")), "Meera Nair")

    def test_a_new_person_is_in_the_list_straight_away_and_no_page_is_kept_by_the_browser(self):
        self.add("Meera", "Nair")
        self.client.post(reverse("hours_done"))
        page = self.client.get(reverse("hours_start"))
        meera = User.objects.get(last_name="Nair")
        self.assertContains(page, f'<option value="{meera.pk}"')
        # Back on a shared tablet must not show an old list, or the last person's week.
        for name in ("hours_start", "hours_me", "hours_new"):
            self.assertIn("no-store", self.client.get(reverse(name))["Cache-Control"])

    def test_somebody_already_listed_is_sent_to_their_own_name(self):
        response = self.add("kumar", "RAVI")
        self.assertContains(response, "Ravi Kumar is already on the list.")
        self.assertContains(response, f"{reverse('hours_start')}?person={self.ravi.pk}")
        self.assertEqual(User.objects.filter(first_name__iexact="ravi").count(), 1)

    def test_a_close_name_asks_is_this_you_and_never_puts_the_pin_in_the_page(self):
        response = self.add("Ravi", "Kumaar", pin="5831")
        self.assertContains(response, "Is this you?")
        self.assertContains(response, "Yes, I'm Ravi Kumar")
        self.assertNotContains(response, "5831")
        self.assertNotIn("5831", str(self.client.session[PENDING_KEY]))
        self.assertFalse(User.objects.filter(last_name="Kumaar").exists())

        # "No, I'm a different person" adds them, with the PIN they chose.
        response = self.client.post(reverse("hours_new"), {"step": "different"})
        self.assertRedirects(response, f"{reverse('hours_me')}?welcome=1")
        person = User.objects.get(last_name="Kumaar")
        self.assertEqual(person.possible_duplicate_of, self.ravi)
        self.assertTrue(check_pin(person, "5831"))
        self.assertNotIn(PENDING_KEY, self.client.session)

    def test_the_two_pins_must_match(self):
        response = self.add("Meera", "Nair", pin="5831", again="5832")
        self.assertContains(response, "The two PINs are different.")
        self.assertFalse(User.objects.filter(last_name="Nair").exists())

    def test_an_easy_pin_is_refused_before_anything_else(self):
        response = self.add("Meera", "Nair", pin="1234")
        self.assertContains(response, "too easy to guess")

    def test_the_different_step_without_a_pending_person_starts_again(self):
        self.assertRedirects(
            self.client.post(reverse("hours_new"), {"step": "different"}), reverse("hours_new")
        )
