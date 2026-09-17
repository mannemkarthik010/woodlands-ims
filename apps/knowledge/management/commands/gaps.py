"""
What the kitchen has not written down, as one sheet.

    python manage.py gaps
    python manage.py gaps --worksheet data/from-client/chef-worksheet.md

The chef should be asked once, from one page, rather than interrupted twenty
times over three weeks. --worksheet writes a printable version with blanks to
fill in.
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.knowledge import gaps as survey


class Command(BaseCommand):
    help = "List what the recipes are missing, and optionally write a sheet for the chef."

    def add_arguments(self, parser):
        parser.add_argument("--worksheet", default="", help="Write a printable sheet here.")
        parser.add_argument("--json", default="", help="Write the gaps as data, for tools/chef_form.py.")

    def handle(self, *args, **options):
        found = survey.survey()
        w = self.stdout.write

        if not found:
            w(self.style.WARNING("No records yet. Ingest a recipe document first."))
            return

        needs_servings = [g for g in found if g.needs_servings]
        needs_method = [g for g in found if g.needs_method]

        w("")
        w(
            self.style.MIGRATE_HEADING(
                f"{len(found)} recipes. {len(found) - len([g for g in found if g.is_clear])} "
                f"have something missing."
            )
        )

        w("")
        w(
            self.style.MIGRATE_HEADING(
                f"Servings per batch — missing on {len(needs_servings)} of {len(found)}"
            )
        )
        w("  One number each. Until it exists these cannot be scaled to a headcount.")
        for gap in needs_servings:
            w(f"   {gap.record.title}")

        w("")
        w(self.style.MIGRATE_HEADING(f"Method — missing on {len(needs_method)} of {len(found)}"))
        w("  The document gives quantities only.")
        for gap in needs_method[:8]:
            w(f"   {gap.record.title}")
        if len(needs_method) > 8:
            w(f"   … and {len(needs_method) - 8} more")

        vessels = survey.vessels_wanted(found)
        weights = survey.weights_wanted(found)

        if vessels:
            w("")
            w(self.style.MIGRATE_HEADING(f"{len(vessels)} vessels not measured"))
            w("  One measurement each, true of everything put in them afterwards.")
            for name, count in vessels:
                w(f"   the {name:<10} appears in {count} line{'s' if count != 1 else ''}")

        if weights:
            w("")
            w(self.style.MIGRATE_HEADING(f"{len(weights)} bulk quantities still worth weighing separately"))
            w("  A scoop of chana dal decides pounds. A spoon of fennel decides nothing.")
            for line in weights[:12]:
                w(f"   1 {line}")
            if len(weights) > 12:
                w(f"   … and {len(weights) - 12} more")

        asked = survey.questions_nobody_could_answer()
        if asked:
            w("")
            w(self.style.MIGRATE_HEADING("Asked, and nobody had written the answer down"))
            for question in asked:
                w(f"   “{question.text}”")

        if options["json"]:
            import json

            from apps.knowledge import format as recipe

            Path(options["json"]).write_text(
                json.dumps(
                    {
                        "vessels": [name for name, _ in survey.vessels_wanted(found)],
                        "weights": survey.weights_wanted(found),
                        "servings": [
                            {
                                "title": g.record.title,
                                "makes": recipe.yield_text(recipe.parse(g.record.body)) or "",
                            }
                            for g in found
                            if g.needs_servings
                        ],
                        "methods": [g.record.title for g in found if g.needs_method],
                        "totals": {"recipes": len(found)},
                    },
                    indent=1,
                )
            )
            w("")
            w(self.style.SUCCESS(f"Gaps written as data to {options['json']}"))

        if options["worksheet"]:
            path = Path(options["worksheet"])
            path.write_text(self.worksheet(found))
            w("")
            w(self.style.SUCCESS(f"Sheet for the chef written to {path}"))

    # ------------------------------------------------------------------
    def worksheet(self, found) -> str:
        """A page to put in front of the chef, with blanks rather than guesses."""
        lines = [
            "# Woodlands — what we still need from the kitchen",
            "",
            "Everything below is a blank we have deliberately left empty. A plausible",
            "number written here by anybody other than the kitchen would be believed,",
            "and would be wrong.",
            "",
            "## 1. What each vessel holds",
            "",
            "There is one scoop and one spoon. How much each HOLDS is one measurement with",
            "a jug, and it is true of everything put in them afterwards. What a scoopful",
            "WEIGHS depends on what is in it — toor dal 32 oz, sambar powder 14.6 oz — so",
            "that is asked only for the bulk ingredients, where being wrong costs pounds.",
            "",
            "| Vessel | It holds |",
            "|---|---|",
        ]
        for name, _count in survey.vessels_wanted(found):
            lines.append(f"| the {name} | |")
        lines += [
            "",
            "### And what a scoop of these weighs",
            "",
            "| Measure | It weighs |",
            "|---|---|",
        ]
        for line in survey.weights_wanted(found):
            lines.append(f"| 1 {line} | |")

        lines += [
            "",
            "## 2. How many servings a batch gives",
            "",
            "One number each. Without it we cannot answer “enough for 100 people”.",
            "",
            "| Recipe | One batch makes | Servings from that |",
            "|---|---|---|",
        ]
        from apps.knowledge import format as recipe

        for gap in found:
            if not gap.needs_servings:
                continue
            made = recipe.yield_text(recipe.parse(gap.record.body)) or "—"
            lines.append(f"| {gap.record.title} | {made} | |")

        lines += [
            "",
            "## 3. The method",
            "",
            "The document we have gives quantities only. For each of these, the order",
            "things go in, roughly how long, and what it should look like when it is",
            "right. Rough notes are fine — they will be typed up and brought back for",
            "checking.",
            "",
        ]
        for gap in found:
            if gap.needs_method:
                lines.append(f"- [ ] {gap.record.title}")

        lines += [
            "",
            "## 4. Still completely missing",
            "",
            "- [ ] Dosa batter — urad, rice, dalia, fenugreek per grind, and what one grind makes",
            "- [ ] Idly batter — the same",
            "- [ ] Coconut chutney, mint chutney",
            "- [ ] The dosas, uthappam, biryani, the paneer curries, vada, idly",
            "",
            "## 5. Serving sizes",
            "",
            "For the things sold both on a plate and in a tub — how many ounces is one",
            "serving of each?",
            "",
            "| Item | A plated serving is | A tub is |",
            "|---|---|---|",
            "| Rasam | | 16 oz |",
            "| Chana masala | | 4 / 8 / 16 oz |",
            "| Sambar | | 8 / 16 oz |",
            "| Dosa batter | | 32 oz |",
            "| Idly batter | | 32 oz |",
        ]
        return "\n".join(lines) + "\n"
