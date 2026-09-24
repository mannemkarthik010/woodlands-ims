"""
Adding yourself on the tablet without becoming two people.

A duplicate is not a cosmetic problem: it splits somebody's hours across two
names, and on payday one of the halves is easy to miss.
"""

from django.test import TestCase

from apps.core.models import Position, Role, User
from apps.core.people import AlreadyListed, LooksLike, PersonError, add_person, find_matches, name_key
from apps.core.pins import PinError, check_pin


class NameKeyTests(TestCase):
    def test_case_spacing_accents_and_order_do_not_make_a_new_name(self):
        same = ["Ravi Kumar", "ravi kumar", "  RAVI   KUMAR ", "Kumar Ravi", "Rávi Kumar"]
        keys = {name_key(*n.strip().split(None, 1)) for n in same}
        self.assertEqual(keys, {"kumar ravi"})


class AddPersonTests(TestCase):
    def setUp(self):
        self.ravi = User.objects.create_user(
            "ravi", first_name="Ravi", last_name="Kumar", display_name="Ravi Kumar"
        )
        self.owner = User.objects.create_user(
            "owner", first_name="Jaspinder", last_name="Kalra", role=Role.OWNER
        )

    def test_a_new_person_is_added_with_a_pin_and_marked_for_review(self):
        dosa = Position.objects.create(name="Dosa station")
        meera = add_person("  meera ", "NAIR", pin="4829", position=dosa)
        self.assertEqual((meera.first_name, meera.last_name, str(meera)), ("Meera", "Nair", "Meera Nair"))
        self.assertEqual((meera.role, meera.position), (Role.KITCHEN, dosa))
        self.assertTrue(meera.needs_review)
        self.assertFalse(meera.has_usable_password())
        self.assertTrue(check_pin(meera, "4829"))

    def test_the_same_name_in_any_form_is_refused_and_points_at_the_person(self):
        for first, last in [("Ravi", "Kumar"), ("kumar", "ravi"), ("RAVI", "kumar")]:
            with self.subTest(name=f"{first} {last}"), self.assertRaises(AlreadyListed) as caught:
                add_person(first, last, pin="4829")
            self.assertEqual(caught.exception.person, self.ravi)
        self.assertEqual(User.objects.count(), 2)

    def test_a_close_name_is_asked_about_first(self):
        for first, last in [("Ravi", "Kumaar"), ("Ravi", "K"), ("Ravi", "")]:
            with self.subTest(name=f"{first} {last}"), self.assertRaises(LooksLike) as caught:
                add_person(first, last, pin="4829")
            self.assertEqual(caught.exception.people, [self.ravi])

    def test_saying_i_am_different_adds_them_and_remembers_who_they_said_they_were_not(self):
        person = add_person("Ravi", "Kumaar", pin="4829", different_from=[self.ravi])
        self.assertEqual(person.possible_duplicate_of, self.ravi)
        self.assertTrue(person.needs_review)

    def test_a_different_name_goes_straight_through(self):
        add_person("Priya", "Sharma", pin="4829")
        with self.assertRaises(LooksLike):
            add_person("Priya", "Sharmaa", pin="5831")
        add_person("Arjun", "Das", pin="5831")  # nothing like anyone

    def test_owners_and_people_who_have_left_are_not_matched(self):
        add_person("Jaspinder", "Kalra", pin="4829")  # the owner is not on the tablet list
        self.ravi.is_active_staff = False
        self.ravi.save()
        self.assertEqual(find_matches("Ravi", "Kumar"), (None, []))

    def test_a_bad_pin_or_no_name_adds_nobody(self):
        with self.assertRaises(PinError):
            add_person("Meera", "Nair", pin="1234")
        with self.assertRaises(PersonError):
            add_person("  ", "Nair", pin="4829")
        self.assertFalse(User.objects.filter(last_name="Nair").exists())

    def test_two_people_with_the_same_spelling_get_different_logins(self):
        # An older login that happens to be spelled the same, for someone else.
        User.objects.create_user("meera-nair", first_name="Priya", last_name="Sharma")
        person = add_person("Meera", "Nair", pin="4829")
        self.assertEqual(person.username, "meera-nair-2")
