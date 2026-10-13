"""
Make the restaurant's kitchen-tablet login and print its one-time "choose the
password" link.

    python manage.py invite_tablet

Open the link on the tablet and let an owner choose the password there. The
login sees the staff screens only -- no admin, dashboard, sales, pay or
reports. Run it again for a fresh link, for a forgotten password too.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.invites import TABLET_USERNAME, invite_tablet


class Command(BaseCommand):
    help = "Create the kitchen tablet's login and print a one-time link to choose its password."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", default=TABLET_USERNAME)

    def handle(self, *args, **options):
        try:
            user, link, created = invite_tablet(options["username"])
        except ValueError as e:
            raise CommandError(str(e)) from None
        days = settings.PASSWORD_RESET_TIMEOUT // 86400
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Fresh link for'} the tablet login {user.username}."
            )
        )
        self.stdout.write("")
        self.stdout.write(f"  {link}")
        self.stdout.write("")
        self.stdout.write(
            f"Works once, for {days} days. Open it on the tablet and let an owner choose the password."
        )
