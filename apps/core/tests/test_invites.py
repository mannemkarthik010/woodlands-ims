"""
Owners choose their own password from a one-time link. Nobody else ever
types it, and it never travels through Slack or email.
"""

from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.invites import OWNER_GROUP, invite_owner
from apps.core.models import Role, User


@override_settings(SITE_URL="https://woodlands.example.com")
class InviteTests(TestCase):
    def path_of(self, link: str) -> str:
        return link.removeprefix("https://woodlands.example.com")

    def test_a_new_owner_has_no_password_until_they_choose_one(self):
        user, link, created = invite_owner("PJ", display_name="PJ")
        self.assertTrue(created)
        self.assertEqual((user.username, user.role, user.is_staff), ("pj", Role.OWNER, True))
        self.assertFalse(user.has_usable_password())
        self.assertTrue(link.startswith("https://woodlands.example.com/welcome/"))
        self.assertTrue(user.groups.filter(name=OWNER_GROUP).exists())

    def test_the_link_sets_the_password_signs_them_in_and_then_stops_working(self):
        user, link, _ = invite_owner("pj")
        page = self.client.get(self.path_of(link), follow=True)  # Django swaps the token for a session step
        self.assertContains(page, "choose your password")
        response = self.client.post(
            page.redirect_chain[-1][0],
            {"new_password1": "saffron-dosa-2026", "new_password2": "saffron-dosa-2026"},
        )
        self.assertRedirects(response, reverse("home"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("saffron-dosa-2026"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

        self.client.logout()
        again = self.client.get(self.path_of(link), follow=True)
        self.assertContains(again, "already been used or has expired")

    def test_a_weak_password_is_refused(self):
        _, link, _ = invite_owner("pj")
        page = self.client.get(self.path_of(link), follow=True)
        response = self.client.post(
            page.redirect_chain[-1][0], {"new_password1": "12345678", "new_password2": "12345678"}
        )
        self.assertContains(response, "too common")

    def test_the_link_expires(self):
        _, link, _ = invite_owner("pj")
        with mock.patch("django.contrib.auth.tokens.PasswordResetTokenGenerator._now") as now:
            from datetime import datetime, timedelta

            now.return_value = datetime.now() + timedelta(days=4)
            page = self.client.get(self.path_of(link), follow=True)
        self.assertContains(page, "already been used or has expired")

    def test_asking_again_gives_a_fresh_link_and_leaves_the_password_alone(self):
        user, _, _ = invite_owner("pj")
        user.set_password("saffron-dosa-2026")
        user.save()
        again, link, created = invite_owner("pj")
        self.assertFalse(created)
        self.assertTrue(again.check_password("saffron-dosa-2026"))
        self.assertIn("/welcome/", link)

    def test_a_kitchen_account_is_not_turned_into_an_owner(self):
        User.objects.create_user("ravi", role=Role.KITCHEN)
        with self.assertRaisesMessage(ValueError, "not an owner"):
            invite_owner("ravi")

    def test_owners_can_manage_staff_and_items_in_the_admin_but_not_the_ledger(self):
        user, _, _ = invite_owner("pj")
        user = User.objects.get(pk=user.pk)  # fresh permission cache
        self.assertTrue(user.has_perm("core.change_user"))
        self.assertTrue(user.has_perm("catalog.add_item"))
        self.assertTrue(user.has_perm("labour.change_shifttemplate"))
        self.assertFalse(user.has_perm("core.delete_user"))
        self.assertFalse(user.has_perm("stock.change_stockmovement"))
        self.assertFalse(user.is_superuser)

    def test_the_command_prints_the_link_and_how_to_send_it(self):
        out = StringIO()
        call_command("invite_owner", "jaspinder", "--name", "Jaspinder", stdout=out)
        text = out.getvalue()
        self.assertIn("Created owner jaspinder.", text)
        self.assertIn("https://woodlands.example.com/welcome/", text)
        self.assertIn("privately", text)
        with self.assertRaises(CommandError):
            call_command("invite_owner", " ", stdout=StringIO())

    def test_an_owner_makes_a_fresh_link_for_another_owner_in_the_admin(self):
        """The live site's key signs links, so they are made there, not on a laptop."""
        pj, _, _ = invite_owner("pj")
        harash, _, _ = invite_owner("harash")
        cook = User.objects.create_user("cook", role=Role.KITCHEN)
        pj.set_password("a long kitchen phrase 42")
        pj.save()
        self.client.force_login(User.objects.get(pk=pj.pk))
        page = self.client.post(
            reverse("admin:core_user_changelist"),
            {"action": "password_link", "_selected_action": [harash.pk, cook.pk]},
            follow=True,
        )
        text = page.content.decode()
        self.assertIn("https://woodlands.example.com/welcome/", text)
        self.assertIn("staff sign in with a PIN", text)
        self.assertEqual(text.count("/welcome/"), 1)  # none for the cook


class HealthTests(TestCase):
    def test_the_health_check_answers_without_a_login(self):
        response = self.client.get("/healthz")
        self.assertEqual((response.status_code, response.content), (200, b"ok"))
