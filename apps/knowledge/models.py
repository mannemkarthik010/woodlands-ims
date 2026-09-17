"""
What the kitchen knows, written down so it can be asked.

The purpose is stated plainly in the requirements report and is worth repeating
here, because it shapes every decision below: the head chef's knowledge exists
in one head, and the restaurant's ability to produce its own food consistently
depends on it. Capturing it is the point. A cook being able to ask a question
in plain language is how the captured knowledge gets used rather than filed.

FOUR RULES, ALL FROM THE CLIENT'S OWN REQUIREMENTS

1.  Every answer states its source, so the cook can read the underlying record
    and judge it (FR-1008). An answer without a citation is a rumour.

2.  The assistant says "this has not been recorded -- ask the chef" rather than
    guessing (FR-1009). A confident wrong answer about how to make a dish is
    worse than no answer: it will be followed.

3.  Nothing is published without the head chef's approval (FR-1010). A draft
    captured from a note is not knowledge until he says it is.

4.  Recipe content does not leave the building without the client's explicit,
    informed consent (FR-1013). Retrieval therefore runs entirely here; only
    the final wording of an answer involves an outside service, and only when
    that consent has been given and recorded.

Every question and every answer is kept, with what it was drawn from. That is
how the chef finds out what his cooks actually ask, and where the record is
thin.
"""

from django.db import models

from apps.core.models import TimeStamped


class Source(models.TextChoices):
    CHEF = "CHEF", "Written with the head chef"
    DOCUMENT = "DOCUMENT", "From a document the kitchen supplied"
    BATCH_NOTE = "BATCH_NOTE", "From a note made while cooking"
    SYSTEM = "SYSTEM", "Derived from the system's own records"


# Implements: FR-1001, FR-1010, FR-1012.
class Record(TimeStamped):
    """
    One piece of knowledge: how a dish is made, how a base is made, what to do
    when it goes wrong.
    """

    title = models.CharField(max_length=160)
    item = models.ForeignKey(
        "catalog.Item",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="knowledge",
        help_text="The dish or component this is about, where there is one.",
    )
    body = models.TextField(help_text="The chef's own words. Markdown.")
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.CHEF)
    origin = models.CharField(
        max_length=200, blank=True, help_text="Where it came from, so it can be traced back."
    )
    language = models.CharField(max_length=8, default="en")

    # How many plates one batch of this gives.
    #
    # The one number that turns "dhal fry for 150" from a refusal into an
    # answer. Every recipe here states a yield in the kitchen's own terms --
    # "2 buckets", "1 large chafer pot", "one 2.5 inch full pan" -- and none of
    # them says how many people that feeds. Without it a base recipe cannot be
    # scaled at all, because a hundred times two buckets is not a question
    # anybody is asking.
    #
    # Left empty until somebody in the kitchen says. A plausible guess here
    # would be indistinguishable from a measured figure, and would be believed.
    servings_per_batch = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Plates from one batch. Ask the chef — never estimate it.",
    )
    serving_note = models.CharField(
        max_length=200,
        blank=True,
        help_text="How that was arrived at, e.g. “one chafer fills 40 thali bowls”.",
    )

    captured_on = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["item"])]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        """
        Only approved knowledge is searchable.

        A draft transcribed from a photograph of a handwritten note is evidence,
        not instruction. Letting it answer questions would put words in the
        chef's mouth that he has not read.
        """
        return self.approved_at is not None


# Implements: FR-1006.
class Passage(models.Model):
    """
    A searchable piece of a record.

    Records are split because a cook asks about one step -- "why is my batter
    not rising" -- and handing back a whole recipe to answer it buries the
    answer in the middle of a page.
    """

    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name="passages")
    ordinal = models.PositiveSmallIntegerField()
    heading = models.CharField(max_length=160, blank=True)
    text = models.TextField()
    length = models.PositiveIntegerField(default=0, help_text="Tokens, for ranking.")

    class Meta:
        ordering = ["record", "ordinal"]
        constraints = [models.UniqueConstraint(fields=["record", "ordinal"], name="uniq_passage_ordinal")]

    def __str__(self) -> str:
        return f"{self.record.title} · {self.heading or self.ordinal}"


class Outcome(models.TextChoices):
    ANSWERED = "ANSWERED", "Answered from the records"
    NOT_RECORDED = "NOT_RECORDED", "Not recorded — ask the chef"
    REFUSED = "REFUSED", "Refused — sending was not permitted"


# Implements: FR-1007, FR-1008, FR-1009.
class Question(TimeStamped):
    """
    Every question asked and every answer given, kept.

    Not for auditing the cooks. For showing the chef what his kitchen actually
    asks, and where the record is thin enough that the honest answer was
    "nobody has written this down".
    """

    asked_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    text = models.CharField(max_length=400)
    answer = models.TextField(blank=True)
    outcome = models.CharField(max_length=14, choices=Outcome.choices, db_index=True)
    answered_by = models.CharField(max_length=40, blank=True, help_text="Which engine wrote the wording.")
    passages = models.ManyToManyField(Passage, blank=True, related_name="answers")

    # The chef's verdict, where he has given one.
    chef_note = models.CharField(max_length=300, blank=True)
    was_wrong = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.text[:60]
