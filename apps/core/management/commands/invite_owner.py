"""
Make an owner's account and print their one-time "choose your password" link.

    python manage.py invite_owner pj --name "PJ"

Send the link to that owner privately (a direct message), never in a channel:
whoever opens it first chooses the password. It works once, and for three days.
Run it again for the same owner to get a fresh link -- for a lost password too.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.invites import invite_owner


class Command(BaseCommand):
    help = "Create an owner account and print a one-time link for them to choose their password."

    def add_arguments(self, parser):
        parser.add_argument("username", help="What they will sign in with, e.g. pj")
        parser.add_argument("--name", default="", help="Name shown in the app, e.g. PJ")

    def handle(self, *args, **options):
        try:
            user, link, created = invite_owner(options["username"], display_name=options["name"])
        except ValueError as e:
            raise CommandError(str(e)) from None
        days = settings.PASSWORD_RESET_TIMEOUT // 86400
        self.stdout.write(
            self.style.SUCCESS(f"{'Created' if created else 'Fresh link for'} owner {user.username}.")
        )
        self.stdout.write("")
        self.stdout.write(f"  {link}")
        self.stdout.write("")
        self.stdout.write(
            f"Works once, for {days} days. Send it to {user} privately — a direct message, not a channel."
        )
