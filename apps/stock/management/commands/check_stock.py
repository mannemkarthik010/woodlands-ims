"""
Notice what is running out, and tell the owners.

    python manage.py check_stock            # record the messages
    python manage.py check_stock --send     # record them and carry them

Meant to run on a schedule -- once in the morning, before anybody orders, and
once in the afternoon, in time to make a batter for the evening. Running it
more often costs nothing and says nothing new: a message is recorded once per
item per day, so a second run is quiet.
"""

from django.core.management.base import BaseCommand

from apps.notify.services import send_pending
from apps.stock.alerts import raise_alerts, shortfalls


class Command(BaseCommand):
    help = "Check stock against par levels and record alerts for the owners."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send", action="store_true", help="Also carry the messages on the configured channel."
        )

    def handle(self, *args, **options):
        loud = options.get("verbosity", 1) > 0
        short = shortfalls()
        result = raise_alerts()

        if loud:
            w = self.stdout.write
            w("")
            if not short:
                w(self.style.SUCCESS("Nothing is below its par level."))
            for item in short:
                style = self.style.ERROR if item.is_out else self.style.WARNING
                w("   " + style(item.sentence()))
            w("")
            w(f"{result['told']} message(s) recorded, {result['already_said']} already said today.")

        if options["send"]:
            carried = send_pending()
            if loud:
                self.stdout.write(f"{carried['sent']} sent, {carried['failed']} failed.")
