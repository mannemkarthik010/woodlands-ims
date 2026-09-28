# ADR 0009 — What the real Shift4 export told us

**Status:** Accepted · **Date:** 2026-09-28 · **Follows:** ADR 0003, whose open
question this answers.

## Context

ADR 0003 chose Shift4's "Sales Summary by Item" as a daily CSV and left one
question open until a real export existed. On 28 September the restaurant
provided one: a full Sunday, 90 menu lines, 423 items, $4,378.33 net.

## What it showed, and what was decided

**The file has no date in it.** The PDF version prints the date range; the
CSV does not, and the number in its file name is when it was exported. So the
business date is always given by the person importing it, and never guessed.

**Add-ons are folded into the dish.** There is no line for "extra paneer": a
dish's average sale price sits above its default price, and "Net Sales w/o
Mods" is empty. Add-ons cannot be counted from this report. Accepted for now:
the stock they use shows up as a variance at the next count rather than going
unrecorded; a different report would be needed to do better.

**The report states its own total.** The lines are checked against it, so a
download cut off part-way is refused rather than half-imported.

**Every one of the 90 names matched** the 318 menu buttons mapped on Day 1.

**Mapped is not the same as sized.** Six buttons are sold with no size on
them — Dosa Batter, Idly Batter, Rasam, DosaNights-Rasam, Channa Masala,
DosaNights-Chana Masala — and were mapped with the default of one base unit
per sale: a 32 oz batter tub would have taken out one pound. `PosItem` now
carries `portion_confirmed`. A dish is confirmed by definition, a button with
its size in the name was converted when mapped, and the rest take nothing out
of stock until an owner says what one sale is, on the day's page.

## Consequences

- Uploading a day asks for its date; the same file is refused; a second file
  for a date asks before replacing, and replacing reverses what the first took
  out of stock.
- Until recipes exist, most dishes take nothing out and say so; tubs sold by
  size do from the first day.
