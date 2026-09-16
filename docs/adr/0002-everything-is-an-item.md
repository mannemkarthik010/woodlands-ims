# ADR 0002 — Raw materials, prepared components and dishes are all Items

**Status:** Accepted · **Date:** 2026-09-16

## Context

This kitchen makes a great deal of what it cooks with: both batters, sambar,
rasam, the chutneys, ground spice blends, curry bases, potato masala. These sit
between raw materials and finished dishes, and several feed more than one dish.

Modelling `Ingredient`, `Preparation` and `MenuItem` separately means three
tables, three sets of recipe logic, and three places for the same bug.

## Decision

One `Item` table with a `kind` of `RAW`, `PREPARED`, `DISH`, `PACKAGING` or
`CONSUMABLE`. One `Recipe` model, whose lines point at other Items. One
recursive routine, `stock.services.explode`, walks a dish down to whatever has
no active recipe.

## Consequences

**Good.** A sale explodes to raw materials in one pass, however deeply nested.
Adding a new layer — a base made from another base — needs no new code.

**Costs.** `Item` carries fields not every kind uses; `is_stocked` distinguishes
dishes. Explosion is recursive, so it needs cycle protection and a depth limit.
Both are implemented and tested.
