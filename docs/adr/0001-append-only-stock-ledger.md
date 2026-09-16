# ADR 0001 — Stock is an append-only ledger

**Status:** Accepted · **Date:** 2026-09-16

## Context

Stock has to be tracked across two locations, through in-house production, and
against sales. The obvious design is a `quantity` column that gets updated.

The restaurant's real problem is that nobody can currently explain where stock
went. A design that cannot answer "how did we get to this number" fails at the
thing it was bought to do.

## Decision

`StockMovement` is append-only. Rows are never edited or deleted.

    on_hand(item, location) = SUM(quantity) FROM stock_movement

Everything goes through `stock.services.post_movement`. Corrections are opposing
rows via `reverse_movement`. `StockBalance` is a rebuildable cache.
`StockMovementAdmin` is read-only including for superusers.

## Consequences

**Good.** Audit trail is free. Variance analysis works, because theoretical and
actual depletion are movement types over one table. Historical balances are
answerable forever. Nothing drifts silently.

**Costs.** More rows. More code than `quantity += n`. Every future developer has
to be told the rule — hence this record, the module docstring, and the read-only
admin.

## Alternatives rejected

*Mutable quantity with an audit table alongside* — two sources of truth that
drift, and the audit table is the one nobody maintains.

*Event sourcing throughout* — right shape, far too much machinery for a
restaurant with two locations.
