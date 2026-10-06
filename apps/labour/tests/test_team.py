"""
Today's team: the owner's plan for a day, next to the hours people record.

Today, for these tests, is Wednesday 7 October 2026, 6 pm -- dinner service.
"""

from datetime import date, datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Location, Position, Role, User
from apps.labour import team
from apps.labour.models import Assignment, Cover, Shift
from apps.labour.services import clock_in, record_shift

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=LA)
WED = date(2026, 10, 7)


class TeamTests(TestCase):
    def setUp(self):
        patcher = mock.patch.object(timezone, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.restaurant = Location.objects.create(code="r", name="Restaurant", kind=Location.Kind.RESTAURANT)
        self.dosa = Position.objects.create(name="Dosa station", sort_order=1)
        self.prep = Position.objects.create(name="Prep", sort_order=2)
        self.owner = User.objects.create_user("owner", display_name="Jaspinder", role=Role.OWNER)
        self.edwin = User.objects.create_user("edwin", display_name="Edwin", position=self.dosa)
        self.anderson = User.objects.create_user("anderson", display_name="Anderson", position=self.prep)
        self.ravi = User.objects.create_user("ravi", display_name="Ravi", position=self.prep)

    def plan(self, day=WED, **covers):
        people = {"edwin": self.edwin, "anderson": self.anderson, "ravi": self.ravi}
        return team.save_plan(
            day, [{"person": people[k], "cover": v} for k, v in covers.items()], user=self.owner
        )

    # --- the plan ----------------------------------------------------------

    def test_a_plan_puts_people_in_their_usual_job_unless_the_owner_chooses_another(self):
        team.save_plan(
            WED,
            [
                {"person": self.edwin, "cover": "EVENING"},
                {
                    "person": self.ravi,
                    "cover": "MORNING",
                    "position": self.dosa,
                    "note": "  catering   prep ",
                },
            ],
        )
        edwin, ravi = (Assignment.objects.get(employee=p) for p in (self.edwin, self.ravi))
        self.assertEqual(edwin.position, self.dosa)
        self.assertEqual(ravi.position, self.dosa)
        self.assertEqual(ravi.note, "catering prep")

    def test_off_takes_someone_off_the_plan(self):
        self.plan(edwin="EVENING", ravi="MORNING")
        self.plan(edwin="")
        self.assertEqual(list(Assignment.objects.values_list("employee__username", flat=True)), ["ravi"])

    def test_someone_with_no_job_cannot_be_planned_without_one(self):
        nobody = User.objects.create_user("new", display_name="New Person")
        with self.assertRaises(team.PlanError):
            team.save_plan(WED, [{"person": nobody, "cover": "MORNING"}])

    def test_copying_last_week_fills_the_gaps_and_keeps_what_the_owner_already_set(self):
        last_wed = date(2026, 9, 30)
        self.plan(day=last_wed, edwin="EVENING", anderson="BOTH")
        self.plan(edwin="MORNING")
        self.assertEqual(team.copy_plan(last_wed, WED), 1)
        self.assertEqual(Assignment.objects.get(business_date=WED, employee=self.edwin).cover, Cover.MORNING)
        self.assertEqual(Assignment.objects.get(business_date=WED, employee=self.anderson).cover, Cover.BOTH)

    # --- the board ---------------------------------------------------------

    def test_the_board_shows_the_plan_against_the_hours_recorded(self):
        self.plan(edwin="EVENING", anderson="BOTH", ravi="MORNING")
        record_shift(self.ravi, day=WED, start=time(10), end=time(15), location=self.restaurant, now=NOW)
        record_shift(self.anderson, day=WED, start=time(10), end=time(15), location=self.restaurant, now=NOW)
        clock_in(self.edwin, location=self.restaurant, at=datetime(2026, 10, 7, 16, 50, tzinfo=LA))

        board = team.board(WED)
        self.assertEqual([r.person.username for r in board.morning], ["anderson", "ravi"])
        self.assertEqual([r.person.username for r in board.evening], ["edwin", "anderson"])
        self.assertEqual(len(board.planned), 3)  # Anderson counted once
        self.assertEqual((board.working, board.recorded, board.waiting), (1, 3, 0))
        # Anderson's morning hours show in the morning; the evening is still to come.
        anderson_evening = board.evening[1]
        self.assertEqual(anderson_evening.status, "waiting")
        self.assertEqual(board.morning[0].minutes, 300)

    def test_hours_from_someone_not_on_the_plan_are_shown_apart(self):
        self.plan(edwin="EVENING")
        record_shift(self.ravi, day=WED, start=time(10), end=time(15), location=self.restaurant, now=NOW)
        board = team.board(WED)
        self.assertEqual([r.person for r in board.unplanned], [self.ravi])
        self.assertIsNone(board.unplanned[0].cover)

    def test_a_cancelled_shift_does_not_count_as_hours_in(self):
        self.plan(ravi="MORNING")
        shift = record_shift(
            self.ravi, day=WED, start=time(10), end=time(15), location=self.restaurant, now=NOW
        )
        Shift.all_objects.filter(pk=shift.pk).update(cancelled_at=NOW)
        self.assertEqual(team.board(WED).morning[0].status, "waiting")

    def test_monday_is_closed(self):
        self.assertFalse(team.board(date(2026, 10, 5)).is_open)

    # --- the screens -------------------------------------------------------

    def test_anyone_signed_in_sees_the_board_but_only_owners_plan(self):
        self.plan(edwin="EVENING")
        self.client.force_login(self.ravi)
        page = self.client.get(reverse("team_today"))
        self.assertContains(page, "Edwin")
        self.assertContains(page, "Dosa station")
        self.assertNotContains(page, "Plan this day")
        self.assertEqual(self.client.get(reverse("team_plan")).status_code, 403)

    def test_the_owner_plans_a_day_on_one_screen(self):
        self.client.force_login(self.owner)
        page = self.client.post(
            f"{reverse('team_plan')}?date=2026-10-08",
            {
                "date": "2026-10-08",
                f"cover_{self.edwin.pk}": "EVENING",
                f"position_{self.edwin.pk}": self.dosa.pk,
                f"cover_{self.anderson.pk}": "BOTH",
                f"position_{self.anderson.pk}": self.prep.pk,
                f"note_{self.anderson.pk}": "catering for 200",
                f"cover_{self.ravi.pk}": "",
            },
        )
        self.assertRedirects(page, f"{reverse('team_today')}?date=2026-10-08")
        board = team.board(date(2026, 10, 8))
        self.assertEqual(len(board.planned), 2)
        self.assertEqual(board.evening[-1].note, "catering for 200")

    def test_plan_defaults_to_tomorrow_once_the_day_is_under_way(self):
        self.assertEqual(team.default_plan_day(NOW), date(2026, 10, 8))
        self.assertEqual(team.default_plan_day(datetime(2026, 10, 7, 8, 0, tzinfo=LA)), WED)
