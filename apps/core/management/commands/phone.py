"""
Start the server and print the address to open on a phone.

The transfer screen is designed to be used one-handed at a storage-unit
door. How it feels in a desktop browser tells you almost nothing, so this
removes the fiddly part of testing it properly: finding the laptop's address
on the wi-fi and remembering to bind to 0.0.0.0 rather than localhost.

    python manage.py phone
"""

import socket

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


def lan_address() -> str | None:
    """
    The address this machine has on the local network.

    Opening a UDP socket towards a public address makes the OS choose the
    outbound interface; nothing is actually sent. More reliable than asking
    for the hostname, which on a Mac often resolves to 127.0.0.1.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


class Command(BaseCommand):
    help = "Run the dev server bound to the network, and print the phone URL."

    def add_arguments(self, parser):
        parser.add_argument("--port", default="8000")

    def handle(self, *args, **options):
        port = options["port"]
        ip = lan_address()

        line = "─" * 52
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(line))
        if ip:
            self.stdout.write("  On your phone, same wi-fi, open:")
            self.stdout.write(self.style.SUCCESS(f"      http://{ip}:{port}/"))
        else:
            self.stdout.write(self.style.WARNING("  Could not work out this machine's network address."))
            self.stdout.write("  Try:  ipconfig getifaddr en0")
        self.stdout.write("")
        self.stdout.write(f"  On this machine:  http://localhost:{port}/")
        self.stdout.write(self.style.MIGRATE_HEADING(line))

        if not settings.DEBUG:
            self.stdout.write(self.style.ERROR("  DEBUG is off — this command is for development only."))
            return

        self.stdout.write("")
        call_command("runserver", f"0.0.0.0:{port}", use_reloader=True)
