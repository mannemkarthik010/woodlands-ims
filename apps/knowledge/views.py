"""
Asking the kitchen's records a question.

Built for the tablet on the pass, so it is one box and a button. There is no
history to scroll, no conversation to maintain and no follow-up: a cook has one
question, mid-service, with one hand, and wants the answer on the screen.

Two things on this screen are deliberate and are the whole point.

Every answer carries the record it came from, so a cook can read what the chef
actually wrote rather than trusting a paraphrase (FR-1008).

When there is no record, the screen says so plainly and offers to ask the chef
(FR-1009). It does not offer the nearest recipe, and it does not apologise. A
cook who is told "nobody has written this down" learns something true about the
kitchen; a cook given a plausible guess learns something false about the dish.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.knowledge import engines, services
from apps.knowledge import format as recipe
from apps.knowledge.models import Outcome, Question, Record


def _context():
    return {
        "published": Record.objects.filter(approved_at__isnull=False).count(),
        "waiting": Record.objects.filter(approved_at__isnull=True).count(),
        "sends_externally": engines.current().sends_externally,
    }


# Implements: FR-1006, FR-1007.
@login_required
def ask_page(request):
    context = _context()
    context["examples"] = [
        record.title for record in Record.objects.filter(approved_at__isnull=False).order_by("title")[:6]
    ]
    context["recent_gaps"] = Question.objects.filter(outcome=Outcome.NOT_RECORDED).order_by("-created_at")[:3]
    return render(request, "knowledge/ask.html", context)


# Implements: FR-1007, FR-1008, FR-1009.
@require_POST
@login_required
def ask(request):
    text = request.POST.get("q", "").strip()
    if not text:
        return render(request, "knowledge/_answer.html", {"blank": True, **_context()})

    answer = services.ask(text, user=request.user)

    # The same parse the text answer uses, so the tablet and the terminal show
    # the same recipe. One rendering, two surfaces.
    records, seen = [], set()
    for passage in answer.passages.select_related("record").all():
        record = passage.record
        if record.pk in seen:
            continue
        seen.add(record.pk)
        sections = recipe.parse(record.body)
        records.append({"record": record, "sections": sections, "has_method": recipe.has_method(sections)})
    records.sort(key=lambda r: r["record"].title)

    return render(
        request,
        "knowledge/_answer.html",
        {"question": answer, "recipes": records, **_context()},
    )
