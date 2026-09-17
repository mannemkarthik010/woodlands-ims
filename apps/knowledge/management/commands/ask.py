"""
Ask the kitchen's records a question from the command line.

    python manage.py ask "how do I make sambar"
    python manage.py ask "how do I make sambar" --why

--why shows the ranking: which passages matched, how much of the question each
covered, and what score it got. Worth looking at when an answer seems wrong,
because it usually shows that the question and the record share no words.
"""

from django.core.management.base import BaseCommand

from apps.knowledge import engines, retrieval, services
from apps.knowledge.models import Outcome, Record


class Command(BaseCommand):
    help = "Ask the knowledge base a question."

    def add_arguments(self, parser):
        parser.add_argument("question", nargs="+")
        parser.add_argument("--why", action="store_true", help="Show how the passages ranked.")

    def handle(self, *args, **options):
        question = " ".join(options["question"])
        w = self.stdout.write

        published = Record.objects.filter(approved_at__isnull=False).count()
        total = Record.objects.count()
        if not published:
            w(
                self.style.WARNING(
                    f"No approved records. {total} are waiting for the chef, and until he "
                    f"approves them nothing answers anything."
                )
            )
            w("Approve them in the admin under Knowledge → Records, or re-ingest with --approve.")
            w("")

        answer = services.ask(question)

        w("")
        w(self.style.MIGRATE_HEADING(question))
        w("")
        w(answer.answer)
        w("")

        if answer.outcome == Outcome.ANSWERED:
            sources = ", ".join(sorted({p.record.title for p in answer.passages.all()}))
            w(self.style.SUCCESS(f"From: {sources}"))
        engine = engines.current()
        w(
            f"Answered by: {answer.answered_by}"
            + ("  (nothing was sent outside)" if not engine.sends_externally else "")
        )

        if options["why"]:
            w("")
            w(self.style.MIGRATE_HEADING("How the passages ranked"))
            hits = retrieval.search(question, limit=8)
            if not hits:
                w("   Nothing matched a single word of the question.")
            for hit in hits:
                mark = "used" if hit.coverage >= services.COVERAGE else "  — "
                w(
                    f"   {mark}  {hit.score:6.2f}  covers {hit.coverage:.0%}  "
                    f"{hit.record.title} · {hit.passage.heading or 'record'}"
                )
                w(f"          matched: {', '.join(sorted(hit.matched)) or '—'}")
