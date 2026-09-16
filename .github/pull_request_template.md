## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Why

<!-- The problem, or the requirement reference (FR-xxx, D-xx). -->

## How to check it

<!-- Steps for someone else to verify it works. Not "run the tests". -->

## Checklist

- [ ] Tests cover the change, and they fail without it
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Migrations included if models changed
- [ ] No secrets, keys or client data in the diff
- [ ] README or ADR updated if a decision changed
- [ ] Ledger invariant intact: nothing writes stock outside `stock.services`
