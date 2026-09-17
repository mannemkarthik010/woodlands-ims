"""
Turning the passages that were found into an answer.

This is the G in RAG, and it is the only part that can involve anybody outside
the restaurant. Two gates stand in front of it, both from the client's own
requirements rather than from caution on our part:

    FR-1013  recipe content does not go to a third-party service without the
             client's explicit, informed consent
    FR-1009  the assistant says "this has not been recorded" rather than
             guessing

So: nothing is sent anywhere unless KNOWLEDGE_CONSENT is true, which somebody
sets deliberately after the owners have agreed and understood what leaves.
Until then the assistant still works -- it answers with the chef's own words,
which is what a cook wanted in the first place.

WHAT IS SENT WHEN IT IS SWITCHED ON

The question, and the handful of passages that were retrieved. Not the
corpus, not the catalogue, not sales, not staff. The model is given the
passages and told to answer from them alone, which is what makes this
retrieval-augmented rather than a model being asked what it thinks a masala
dosa is.
"""

from __future__ import annotations

import logging

from django.conf import settings

log = logging.getLogger(__name__)

NOT_RECORDED = (
    "This has not been recorded yet — ask the chef.\n\n"
    "If he explains it, add it to the records so the next person does not have to ask."
)

SYSTEM_PROMPT = """You answer questions for cooks in one South Indian restaurant's kitchen.

You are given passages from that kitchen's own written records. Answer ONLY from
those passages.

Rules, in order of importance:
1. If the passages do not contain the answer, say exactly: "This has not been
   recorded yet — ask the chef." Do not fill the gap from general knowledge of
   Indian cooking. A confident wrong answer about how to make a dish will be
   followed, and that is worse than no answer.
2. Quantities, timings and temperatures must be exactly as written in the
   passages. Never convert, round or estimate one that is not there.
3. Keep the kitchen's own words for its own measures — a scoop is a scoop.
4. Be brief and practical. Somebody is reading this standing up, mid-service.
5. Never invent a step that is not in the passages, even an obvious one."""


class Passages:
    """
    The default. Hands back what the records say, with their headings.

    Not a fallback and not a stub: for "how do I make sambar", the chef's own
    written method IS the answer, and a model rewording it adds nothing except
    a chance to reword it wrongly. It costs nothing, sends nothing, and works
    the day the records are in.
    """

    name = "records"
    sends_externally = False

    def answer(self, question: str, hits) -> str:
        from apps.knowledge import format as recipe

        parts, seen = [], set()
        for hit in hits:
            record = hit.record
            if record.pk in seen:
                continue
            seen.add(record.pk)
            parts.append(recipe.as_text(record.title, recipe.parse(record.body)))
        return "\n\n\n".join(parts)


class Claude:
    """
    Wording written by Claude, from the retrieved passages only.

    Off unless the client has consented and a key is configured. Costs a
    fraction of a cent per question -- the client's money, so it is their
    decision, made in advance and recorded, not ours to assume.
    """

    name = "claude"
    sends_externally = True

    def answer(self, question: str, hits) -> str:
        try:
            import anthropic
        except ImportError:  # pragma: no cover - depends on deployment
            raise RuntimeError("The anthropic package is not installed on this server.") from None

        context = "\n\n".join(
            f"--- {hit.record.title} · {hit.passage.heading or 'record'} ---\n{hit.passage.text}"
            for hit in hits
        )
        client = anthropic.Anthropic(api_key=settings.KNOWLEDGE_API_KEY)
        response = client.messages.create(
            model=settings.KNOWLEDGE_MODEL,
            max_tokens=700,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Passages from the kitchen's records:\n\n{context}\n\n"
                    f"The cook asks: {question}",
                }
            ],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip()


ENGINES = {"records": Passages(), "claude": Claude()}


def current():
    """
    The engine in use, and the consent gate in front of it.

    An engine that sends recipes outside is refused unless consent has been
    recorded, whatever the configuration says. Configuration is easy to change
    by accident; this is the part that should be hard.
    """
    engine = ENGINES.get(settings.KNOWLEDGE_ENGINE, ENGINES["records"])
    if engine.sends_externally and not settings.KNOWLEDGE_CONSENT:
        log.warning(
            "Engine %r wants to send recipe content outside and consent is not recorded. "
            "Falling back to the records themselves.",
            engine.name,
        )
        return ENGINES["records"]
    if engine.sends_externally and not settings.KNOWLEDGE_API_KEY:
        log.warning("Engine %r has no API key configured. Falling back.", engine.name)
        return ENGINES["records"]
    return engine
