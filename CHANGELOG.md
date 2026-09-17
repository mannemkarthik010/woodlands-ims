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
- Ingredients: the kitchen's grocery sheet imported — 45 raw items with pack sizes and
  case conversions read from "4 lb bag" and "1 case = 10 bags"
- Kitchen measures: what a scoop and a spoon actually weigh, recorded per item, because
  a scoop of toor dal is 32 oz and a scoop of sambar powder is 14 oz
- `PurchaseUnit` is now `ItemMeasure`, with a kind — how it is bought, or how the kitchen
  measures it
- Mapping review ("second look"): finds tubs counted in "each", one food spelled two ways,
  and items left behind by a changed decision — and offers merge, convert and retire
- Merge keeps the old spelling as a searchable name on the surviving item
- Menu mapping screen: 311 POS lines grouped into 252 decisions, with the dish created
  from the group in one action, tub sizes read off the name and converted, and
  near-identical existing items offered as a question (ADR 0005)

### Fixed
- Multi-line `{# … #}` template comments were being shown to the user rather than
  treated as comments — visible on the counting screen
