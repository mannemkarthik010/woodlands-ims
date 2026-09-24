"""
The tablet PIN. What matters is not that a PIN can be checked -- that is one
library call -- but that it cannot be guessed, cannot be read back, and can
never reach an owner's account.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Role, User
from apps.core.pins import LOCKOUT, MAX_FAILED_ATTEMPTS, PinError, check_pin, is_locked, set_pin


class PinTests(TestCase):
    def setUp(self):
        self.cook = User.objects.create_user("ravi", password="pw", role=Role.KITCHEN)
        self.owner = User.objects.create_user("owner", password="pw", role=Role.OWNER)

    # Covers: FR-102, NFR-09.
    def test_the_right_pin_works_and_is_never_stored_in_clear(self):
        set_pin(self.cook, "4829")
        self.cook.refresh_from_db()
        self.assertNotIn("4829", self.cook.pin)
        self.assertTrue(check_pin(self.cook, "4829"))
        self.assertFalse(check_pin(self.cook, "4828"))

    def test_obvious_and_malformed_pins_are_refused(self):
        for pin in ("1111", "1234", "4321", "123456", "987654", "999", "1234567", "12a4", ""):
            with self.subTest(pin=pin), self.assertRaises(PinError):
                set_pin(self.cook, pin)

    # Covers: FR-102.
    def test_an_owner_can_never_have_or_use_a_pin(self):
        with self.assertRaises(PinError):
            set_pin(self.owner, "4829")
        # Even a PIN written straight into the row does not open the account.
        from django.contrib.auth.hashers import make_password

        self.owner.pin = make_password("4829")
        self.owner.save()
        self.assertFalse(check_pin(self.owner, "4829"))

    def test_five_wrong_tries_lock_the_pin_even_against_the_right_one(self):
        set_pin(self.cook, "4829")
        now = timezone.now()
        for _ in range(MAX_FAILED_ATTEMPTS):
            self.assertFalse(check_pin(self.cook, "0000", now=now))
        self.assertTrue(is_locked(self.cook, now))
        self.assertFalse(check_pin(self.cook, "4829", now=now + timedelta(minutes=1)))

        # The lock lifts on its own, and a right answer clears the count.
        later = now + LOCKOUT + timedelta(seconds=1)
        self.assertTrue(check_pin(self.cook, "4829", now=later))
        self.cook.refresh_from_db()
        self.assertEqual(self.cook.pin_failed_attempts, 0)
        self.assertIsNone(self.cook.pin_locked_until)

    def test_a_right_answer_resets_the_count_of_wrong_ones(self):
        set_pin(self.cook, "4829")
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            check_pin(self.cook, "0000")
        self.assertTrue(check_pin(self.cook, "4829"))
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            check_pin(self.cook, "0000")
        self.assertFalse(is_locked(self.cook))

    # Covers: FR-1208.
    def test_somebody_who_has_left_cannot_use_their_pin(self):
        set_pin(self.cook, "4829")
        self.cook.is_active_staff = False
        self.cook.save()
        self.assertFalse(check_pin(self.cook, "4829"))


class PinAdminTests(TestCase):
    """The owners set PINs from the staff page. It must hold the same rules."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", password="pw")
        self.client.force_login(self.admin)
        self.cook = User.objects.create_user("ravi", role=Role.KITCHEN)

    def post(self, pin, role=Role.KITCHEN):
        url = f"/admin/core/user/{self.cook.pk}/change/"
        data = self.client.get(url).context["adminform"].form.initial
        form = {k: v for k, v in data.items() if v is not None and k not in ("groups", "user_permissions")}
        form.update(
            {
                "role": role,
                "new_pin": pin,
                "date_joined_0": self.cook.date_joined.strftime("%Y-%m-%d"),
                "date_joined_1": self.cook.date_joined.strftime("%H:%M:%S"),
            }
        )
        form.pop("date_joined", None)
        form.pop("last_login", None)
        form.pop("pin", None)
        form.pop("position", None)
        return self.client.post(url, form)

    def test_an_owner_sets_a_pin_from_the_staff_page(self):
        response = self.post("4829")
        self.assertEqual(response.status_code, 302, response.content.decode()[:2000])
        self.cook.refresh_from_db()
        self.assertTrue(check_pin(self.cook, "4829"))

    def test_an_easy_pin_is_refused_on_the_page_and_nothing_changes(self):
        response = self.post("1234")
        self.assertContains(response, "too easy to guess")
        self.cook.refresh_from_db()
        self.assertEqual(self.cook.pin, "")

    def test_an_owner_account_cannot_be_given_a_pin(self):
        response = self.post("4829", role=Role.OWNER)
        self.assertContains(response, "Owners sign in with a password")
