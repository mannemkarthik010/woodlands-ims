"""
Read a recipe document into the knowledge base.

    python manage.py ingest_recipes data/from-client/recipes-ramesh.md
    python manage.py ingest_recipes <file> --approve   # only with the chef present

Each "## " heading becomes one record, and each record is split into passages
so that a question about one step does not return a whole page.

IMPORTED RECORDS ARE NOT PUBLISHED

Ingesting is not approving. Everything arrives unapproved, and unapproved
records are not searchable -- which means a freshly imported document answers
nothing at all until the head chef has read it and said yes (FR-1010).

That is not bureaucracy. This document was typed up by somebody else, some of
it is out of date, and parts of it disagree with the handwritten note from the
same week. Letting it answer a cook's question before he has read it would put
words in his mouth. `--approve` exists for the session where he sits down and
goes through them, and it names him on every record it touches.
"""

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Item
from apps.core.models import Role, User
from apps.knowledge.models import Passage, Record, Source


def split_records(markdown: str) -> list[tuple[str, str]]:
    """Every "## " heading and the text under it."""
    parts = re.split(r"^##\s+(.+)$", markdown, flags=re.M)
    out = []
    for index in range(1, len(parts), 2):
        title = parts[index].strip()
        body = parts[index + 1].strip()
        if body:
            out.append((title, body))
    return out


def split_passages(body: str, limit: int = 700) -> list[tuple[str, str]]:
    """
    Paragraphs, grouped up to a readable size.

    Bold run-in headings like "**Yield:**" start a new passage, because that is
    exactly the sort of thing somebody asks about on its own.
    """
    chunks, heading, buffer = [], "", []

    def flush():
        if buffer:
            chunks.append((heading, "\n\n".join(buffer).strip()))

    for paragraph in [p.strip() for p in body.split("\n\n") if p.strip()]:
        label = re.match(r"^\*\*(.+?):?\*\*", paragraph)
        if label or sum(len(b) for b in buffer) + len(paragraph) > limit:
            flush()
            buffer = []
            heading = label.group(1).strip() if label else heading
        buffer.append(paragraph)
    flush()
    return chunks or [("", body)]


class Command(BaseCommand):
    help = "Read a recipe document into the knowledge base, unapproved."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument(
            "--approve",
            action="store_true",
            help="Publish immediately. Only with the head chef reading along.",
        )
        parser.add_argument("--approver", default="", help="Username of the person approving.")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        approver = None
        if options["approve"]:
            username = options["approver"]
            approver = (
                User.objects.filter(username=username).first()
                if username
                else User.objects.filter(role__in=[Role.CHEF, Role.OWNER]).first()
            )
            if approver is None:
                raise CommandError("Approving needs a person to attribute it to. Pass --approver <username>.")

        created = updated = passages = 0
        for title, body in split_records(path.read_text()):
            record, made = Record.objects.update_or_create(
                title=title,
                origin=path.name,
                defaults={
                    "body": body,
                    "source": Source.DOCUMENT,
                    "item": Item.objects.filter(name__iexact=title).first(),
                },
            )
            created += made
            updated += not made

            if options["approve"]:
                record.approved_by = approver
                record.approved_at = timezone.now()
                record.save(update_fields=["approved_by", "approved_at", "updated_at"])

            record.passages.all().delete()
            for ordinal, (heading, text) in enumerate(split_passages(body)):
                Passage.objects.create(
                    record=record,
                    ordinal=ordinal,
                    heading=heading,
                    text=text,
                    length=len(text.split()),
                )
                passages += 1

        if options.get("verbosity", 1) == 0:
            return

        w = self.stdout.write
        w("")
        w(self.style.SUCCESS(f"{created} new records, {updated} updated, {passages} passages"))
        if options["approve"]:
            w(self.style.WARNING(f"Published, approved by {approver}."))
        else:
            w("")
            w(
                "Nothing is searchable yet. These are unapproved, on purpose — the chef\n"
                "has to read them before they answer anybody's question."
            )
