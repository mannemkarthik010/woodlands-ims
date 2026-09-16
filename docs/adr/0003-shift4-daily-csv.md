# ADR 0003 — Sales arrive as Shift4's own daily CSV, not an API

**Status:** Accepted · **Date:** 2026-09-16

## Context

The POS is Shift4 Dine (SkyTab). Inventory has to deplete against real sales.
Shift4 is payments-first; SkyTab order data via API sits behind a partner
programme requiring approval. Their Customer Hub already emails scheduled
reports, and "Sales Summary by Item" carries quantity sold per menu item, with
CSV, PDF and XLS export and daily/weekly/monthly subscription.

## Decision

Subscribe "Sales Summary by Item" to arrive daily as CSV. Import it, map POS
names to Items, deplete by recipe explosion. No live connection.

## Consequences

**Good.** No approval to wait for, nothing to buy, and the till cannot be
affected by anything this system does. Daily granularity is right for
inventory — nobody counts stock by the minute.

**Costs.** Yesterday's data, not this minute's. Depends on a report staying
subscribed, so a missing day must be visible rather than silent.

**Open.** Whether modifiers and add-ons appear as their own lines. If they are
rolled into the parent, every extra paneer vanishes from the arithmetic.
**To be confirmed against a real export before the parser is written.**
