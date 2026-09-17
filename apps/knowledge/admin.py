from django.contrib import admin
from django.utils import timezone

from apps.knowledge.models import Outcome, Passage, Question, Record


class PassageInline(admin.TabularInline):
    model = Passage
    extra = 0
    fields = ("ordinal", "heading", "text")
    readonly_fields = ("ordinal",)


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "item",
        "servings_per_batch",
        "source",
        "approved_by",
        "approved_at",
    )
    list_filter = ("source", "approved_at", "language", "servings_per_batch")
    search_fields = ("title", "body", "origin")
    autocomplete_fields = ("item",)
    fields = (
        "title",
        "item",
        "body",
        "source",
        "origin",
        "language",
        ("servings_per_batch", "serving_note"),
        "captured_on",
        ("approved_by", "approved_at"),
    )
    inlines = [PassageInline]
    actions = ["approve", "withdraw"]

    @admin.action(description="Approve — these become searchable")
    def approve(self, request, queryset):
        """
        Approving is the chef saying these words are his. Nothing else in the
        system publishes knowledge, and unapproved records answer nobody.
        """
        count = queryset.update(approved_by=request.user, approved_at=timezone.now())
        self.message_user(request, f"{count} record(s) approved and now searchable.")

    @admin.action(description="Withdraw — stop these answering questions")
    def withdraw(self, request, queryset):
        count = queryset.update(approved_by=None, approved_at=None)
        self.message_user(request, f"{count} record(s) withdrawn.")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    """
    Not for auditing the cooks. For showing the chef what his kitchen actually
    asks, and where the record is thin enough that the honest answer was
    "nobody has written this down".
    """

    list_display = ("text", "outcome", "answered_by", "asked_by", "was_wrong", "created_at")
    list_filter = ("outcome", "was_wrong", "answered_by")
    search_fields = ("text", "answer")
    readonly_fields = ("text", "answer", "outcome", "answered_by", "asked_by", "passages")
    fields = ("text", "answer", "outcome", "answered_by", "asked_by", "passages", "chef_note", "was_wrong")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("asked_by")

    def changelist_view(self, request, extra_context=None):
        gaps = Question.objects.filter(outcome=Outcome.NOT_RECORDED).count()
        extra_context = extra_context or {}
        extra_context["title"] = f"Questions asked — {gaps} had nothing recorded to answer them"
        return super().changelist_view(request, extra_context)
