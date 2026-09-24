# Changelog

Notable changes per release. Newest first.

## [Unreleased]

### Added
- **My hours** on the shared tablet: a worker taps their name, enters their PIN and writes in
  the date, when they started and when they left — checked on a summary before it is saved
- The worker's last seven days, with the empty ones named and one tap from being filled in,
  because people forget; a second shift can be added to any day for a double
- Entered hours are refused when they are in the future, overlap another shift, run over
  16 hours (a typo), or are more than 14 days old (an owner adds those); an end time before
  the start time means past midnight
- Entered shifts are marked as entered by the worker, and when they were written down is kept
- Owners set each person's tablet PIN from the staff page, under the same rules
- Demo staff, positions and a tablet sign-in in `seed_demo`
- Clocking in and out, as the owners describe the day: a morning shift and an evening shift,
  each job with its own hours, and a few people working both (ADR 0008)
- Positions (dosa station, prep, server…) with morning and evening times, and a weekday
  override for days that run differently
- Clock-in works out which shift is starting from the position's schedule and keeps a copy
  of it on the shift, so later changes to the hours do not rewrite who was late
- An evening that runs past midnight can still be clocked out; a shift nobody closed is
  flagged as a missed clock-out and never blocks the next clock-in
- Owner corrections to a clock record need a reason, and every changed field is kept;
  clock records cannot be deleted, from the admin or from code
- Staff PINs: 4–6 digits, hashed, obvious ones refused, five wrong tries lock it for five
  minutes, and owners never have one
- `Vessel`: what the scoop, the spoon and the ladle hold — measured once, true of everything
  put in them, with per-ingredient weights kept for the bulk items where it matters
- The kitchen's note of 16 September fully imported: 13 measures, and the six ingredients it
  named that the catalogue was missing
- The kitchen's knowledge base: 15 base recipes ingested, searchable by dish, ingredient
  or step, answering in the chef's own words with the record named
- Retrieval runs entirely on the restaurant's own machine; only the wording of an answer
  can involve an outside model, and only with the client's recorded consent (ADR 0007)
- A dish with no record is named as missing rather than answered from a similar recipe
- Low-stock alerts: the system notices what is below its par level and tells the owners,
  once per item per day — a base "needs making", a raw material "needs ordering"
- Messages are recorded first and carried second, on a channel chosen by configuration;
  nothing is sent until text messaging is explicitly switched on (ADR 0006)
- Append-only stock ledger with reversal-based corrections (ADR 0001)
- Item master covering raw materials, prepared components and dishes (ADR 0002)
- Unit conversion with per-supplier purchase units
- Production batches: actual inputs, measured yield, maturity and expiry
- Recipes as a nested bill of materials with recursive explosion
- Sales import scaffolding with POS-name mapping (ADR 0003)
- Django admin, with the ledger read-only
- Seed command for units, categories and locations
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

### Changed
- The 3–5pm closure is no longer deducted as a standard break. Morning and evening are
  separate shifts and hours are the plain span of each (ADR 0008 supersedes FR-103/FR-104)

### Fixed
- Multi-line `{# … #}` template comments were being shown to the user rather than
  treated as comments — visible on the counting screen
