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

from decimal import Decimal

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
    servings = services.servings_wanted(text)

    # Ranked order, from the search itself. Never re-read from the
    # many-to-many: it comes back in database order and the best match is not
    # necessarily first.
    hits = getattr(answer, "hits", [])
    primary = hits[0].record if hits else None
    sections = recipe.parse(primary.body) if primary else []

    # Components are named and pointed at, never read out. Butter masala is
    # built from kadai sauce and basic gravy; printing both inside it turns a
    # one-page recipe into five and a cook stops reading.
    made_here = sorted({i.component for s in sections for i in s.ingredients if i.component})
    related = [
        h.record.title
        for h in hits[1:]
        if primary and h.record.pk != primary.pk and h.record.title not in made_here
    ]

    per_serving = recipe.per_serving(sections) if servings else None
    per_batch = primary.servings_per_batch if primary else None

    # Where there is no per-serving line but somebody has recorded how many
    # servings a batch gives, the whole recipe scales instead.
    batches = whole = None
    if servings and per_serving is None and per_batch:
        exact, batches = recipe.batches_for(servings, per_batch)
        whole = recipe.scale(sections, exact)

    return render(
        request,
        "knowledge/_answer.html",
        {
            "question": answer,
            "record": primary,
            "sections": sections,
            "has_method": recipe.has_method(sections),
            "servings": servings,
            "scaled": recipe.scale([per_serving], Decimal(servings))[0] if per_serving else None,
            "whole": whole,
            "batches": batches,
            "per_batch": per_batch,
            "batch_makes": recipe.yield_text(sections) if servings else "",
            "components": made_here,
            "related": list(dict.fromkeys(related)),
            **_context(),
        },
    )
