"""
The recipe assistant.

Nearly every test here is about the assistant NOT answering. That is the
point of the thing: a cook mid-service will follow what it says, so a
confident wrong answer about how to make a dish is worse than no answer at
all, and "nobody has written this down" is the most useful sentence it has.
"""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalog.models import Item, ItemKind, Unit, UnitKind
from apps.knowledge import engines, services
from apps.knowledge.models import Outcome, Passage, Record


def record(title, body, *, approved=True, item=None):
    row = Record.objects.create(
        title=title, body=body, item=item, approved_at=timezone.now() if approved else None
    )
    Passage.objects.create(record=row, ordinal=0, text=body, length=len(body.split()))
    return row


class AskTests(TestCase):
    def setUp(self):
        self.each = Unit.objects.create(
            code="each", name="Each", kind=UnitKind.COUNT, to_canonical=Decimal("1")
        )
        record(
            "Sambar",
            "Toor dal 3 scoop. Moong dal 1 scoop. Tamarind water 16 oz. "
            "Sambar powder 1 scoop. Yield 2 buckets.",
        )
        record("Tempering", "Mustard seeds 16 oz, dried red chilli 16 oz, urad gota 32 oz. 1:2:4 ratio.")

    def dish(self, name):
        return Item.objects.create(
            code=f"dish-{name.lower().replace(' ', '-')}",
            name=name,
            kind=ItemKind.DISH,
            base_unit=self.each,
            is_stocked=False,
        )

    # Covers: FR-1006, FR-1007.
    def test_a_question_is_answered_from_the_records(self):
        answer = services.ask("how do I make sambar")
        self.assertEqual(answer.outcome, Outcome.ANSWERED)
        self.assertIn("Toor dal 3 scoop", answer.answer)

    # Covers: FR-1008.
    def test_every_answer_says_where_it_came_from(self):
        answer = services.ask("tempering ratio")
        self.assertIn("Tempering", [p.record.title for p in answer.passages.all()])

    # Covers: FR-1009.
    def test_a_question_about_nothing_in_the_records_is_refused(self):
        answer = services.ask("how do I fix a gearbox")
        self.assertEqual(answer.outcome, Outcome.NOT_RECORDED)
        self.assertEqual(answer.passages.count(), 0)

    # Covers: FR-1009.
    def test_a_dish_we_sell_with_no_record_is_named_rather_than_approximated(self):
        """
        The failure this exists for: asked how to make a masala dosa, ranked
        search returns Butter Masala. The word "masala" is there, the score is
        respectable, and a cook would follow it.
        """
        self.dish("Masala Dosa")
        record("Butter Masala", "Kadai sauce 1 large ladle. Heavy cream half gallon. Paprika 1 spoon.")
        answer = services.ask("how do I make a masala dosa")
        self.assertEqual(answer.outcome, Outcome.NOT_RECORDED)
        self.assertIn("Masala Dosa", answer.answer)

    # Covers: FR-1006.
    def test_a_dish_named_with_a_common_word_does_not_silence_other_questions(self):
        """
        There is an item on this menu called simply "Masala". Without care that
        one name would refuse every question containing the word — including
        questions about Butter Masala, which is written down.
        """
        self.dish("Masala")
        record("Butter Masala", "Kadai sauce 1 large ladle. Heavy cream half gallon.")
        answer = services.ask("what goes in butter masala")
        self.assertEqual(answer.outcome, Outcome.ANSWERED)

    # Covers: FR-1010.
    def test_an_unapproved_record_answers_nothing(self):
        """
        Transcribed from a photograph of somebody's handwriting, half of it out
        of date. Until the chef has read it, answering from it would put words
        in his mouth.
        """
        Record.objects.update(approved_at=None)
        self.assertEqual(services.ask("how do I make sambar").outcome, Outcome.NOT_RECORDED)

    def test_the_question_and_its_answer_are_kept(self):
        services.ask("how do I make sambar")
        self.assertEqual(services.Question.objects.count(), 1)

    # Covers: FR-1002.
    def test_quantities_come_back_exactly_as_the_chef_wrote_them(self):
        answer = services.ask("how much tamarind water in sambar")
        self.assertIn("16 oz", answer.answer)
        self.assertNotIn("453", answer.answer)  # nothing converted behind his back


class ConsentTests(TestCase):
    """
    FR-1013: recipe content does not go to a third-party service without the
    client's explicit, informed consent.
    """

    def setUp(self):
        record("Sambar", "Toor dal 3 scoop. Yield 2 buckets.")

    def test_the_default_engine_sends_nothing_anywhere(self):
        self.assertFalse(engines.current().sends_externally)

    @override_settings(KNOWLEDGE_ENGINE="claude", KNOWLEDGE_CONSENT=False, KNOWLEDGE_API_KEY="k")
    def test_an_external_engine_is_refused_without_consent(self):
        """
        Configuration gets changed by accident. Consent is the client's
        decision, so the code refuses whatever the configuration says.
        """
        self.assertEqual(engines.current().name, "records")
        self.assertEqual(services.ask("how do I make sambar").answered_by, "records")

    @override_settings(KNOWLEDGE_ENGINE="claude", KNOWLEDGE_CONSENT=True, KNOWLEDGE_API_KEY="")
    def test_consent_without_a_key_still_answers_from_the_records(self):
        self.assertEqual(engines.current().name, "records")
        self.assertEqual(services.ask("how do I make sambar").outcome, Outcome.ANSWERED)

    @override_settings(KNOWLEDGE_ENGINE="claude", KNOWLEDGE_CONSENT=True, KNOWLEDGE_API_KEY="k")
    def test_with_consent_and_a_key_the_external_engine_is_selected(self):
        self.assertEqual(engines.current().name, "claude")

    @override_settings(KNOWLEDGE_ENGINE="claude", KNOWLEDGE_CONSENT=True, KNOWLEDGE_API_KEY="k")
    def test_a_model_that_fails_falls_back_to_the_chefs_own_words(self):
        """A model being unreachable must not mean the cook gets nothing."""
        answer = services.ask("how do I make sambar")
        self.assertEqual(answer.outcome, Outcome.ANSWERED)
        self.assertIn("Toor dal 3 scoop", answer.answer)
        self.assertTrue(answer.answered_by.startswith("records ("))
