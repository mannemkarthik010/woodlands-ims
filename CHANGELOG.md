# Changelog

Notable changes per release. Newest first.

## [Unreleased]

### Added
- Append-only stock ledger with reversal-based corrections (ADR 0001)
- Item master covering raw materials, prepared components and dishes (ADR 0002)
- Unit conversion with per-supplier purchase units
- Production batches: actual inputs, measured yield, maturity and expiry
- Recipes as a nested bill of materials with recursive explosion
- Sales import scaffolding with POS-name mapping (ADR 0003)
- Time and attendance with the 3–5pm closure as a standard break
- Django admin, with the ledger read-only
- Seed command for units, categories, locations and break policy
- CI: lint, format, Django checks, missing-migration check, tests
- Pre-commit hooks and Architecture Decision Records
- Storage run screen: search by any name an item is known by, post in one action
- Stock count screen: generated sheet, counted on a phone, variance reviewed before committing
- Menu import: 318 Shift4 lines grouped into roughly 60 mapping decisions
- Environment-based settings, demo data, and a command for testing on a phone
- Documentation: architecture and runbook written by hand; data model and requirement
  traceability generated from the code, with CI failing when they go stale
