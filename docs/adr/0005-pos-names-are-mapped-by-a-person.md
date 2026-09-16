# ADR 0005 — POS names are mapped by a person, never inferred

**Status:** Accepted · **Date:** 2026-09-16

## Context

Sales arrive as Shift4's daily "Sales Summary by Item" CSV (ADR 0003). Every
line on it is a name typed into a till at some point over the years, and
nothing on the till knows about our catalogue. Before a day's sales can
deplete a gram of stock, each of those names has to point at something.

The menu has 318 of them. They are not 318 foods:

- **Promotions.** "Masala Dosa", "$10 Masala Dosa", "DosaNights-Masala Dosa",
  "Weekday Lunch Masala Dosa" and "NYPF - Masala Dosa" are one plate of food.
- **Sizes.** Coconut Chutney sells at 4, 8 and 16 oz as three POS lines for
  one chutney, and each sale counts as a quantity of 1 on the till.
- **Spelling.** "Chana Masala 16 oz" and "Channa Masala 4 oz" are the same
  food, written twice.
- **Wrappers.** A `1Prix Fixe - Adult` at $17.25 whose courses appear as
  separate $0.00 lines. Depleting both counts every prix-fixe meal twice.

String similarity would map most of these correctly. The temptation to let it
is considerable: 311 unmapped lines is an afternoon of somebody's life.

## Decision

**Grouping is automatic. Mapping is not, ever.**

Names that reduce to the same food once promotions and tub sizes are stripped
are shown together as one decision. Which dish a group refers to is confirmed
by a person, on a screen built for it, and recorded with who confirmed it and
when.

Three supports, none of which decide anything:

1. **Create the dish from the group** in one action, because most of the menu
   does not exist in the catalogue yet and 250 trips to the admin is how a
   mapping queue never gets finished.
2. **Tub sizes read off the name**, pre-filled in ounces, converted into
   whatever the item is measured in, and shown for confirmation.
3. **Near-identical existing items surfaced as a question** — "there is
   already a Chana Masala, is that this?" — because the specific failure worth
   preventing is one food becoming two items.

An unmapped line blocks its import from posting (`SalesImport.is_safe_to_post`).

## Consequences

**What this costs.** Roughly 250 decisions of the owners' time before sales
depletion works at all, and a screen that had to be built to make them
bearable. A morning, not an afternoon, but not nothing.

**What it buys.** A wrong mapping is invisible. It does not error, it does not
look odd, and it quietly poisons every variance figure that follows — for
months, until somebody notices the dal usage has never made sense and has no
way to find out why. An unmapped line is a number on a screen that says
something needs attention, and it is fixed in fifteen seconds.

The second failure is very much cheaper than the first, and the first is the
kind of thing that ends a system's credibility with the people using it.

**What would change this.** Nothing about scale. If the menu were 3,000 lines
the answer would be a better grouping screen, not a lowered bar for what
counts as knowing.
