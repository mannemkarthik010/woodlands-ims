# Changelog

Notable changes per release. Newest first.

## [Unreleased]

### Added
- The chefs' approval of the recipes, recorded in their names ("Edwin and Anderson, passed on by
  Karthik") on each record; `ingest_recipes --leave-out` imports a replaced sheet without
  publishing it, so an old tomato chutney with garlic never answers a cook
- Ready for Vercel, the owners' choice of host: a serverless entrypoint (`api/index.py`,
  `vercel.json`), CSS served without a build step, `.vercelignore` keeping client data off the
  host, and a refusal to start there without PostgreSQL. Verified locally the way Vercel runs it
- The recipe assistant may word its answers with Claude (Opus 5.5) — the owners agreed on
  6 October 2026. It still answers only from the passages found; a declined or failed call falls
  back to the chef's own words
- The restaurant's week, as the owners gave it: closed Mondays, Tuesday–Thursday dinner, Friday–
  Sunday lunch and dinner. The sales page shows a Monday as Closed rather than a missing file
- What the kitchen told us on the 2 October visit, loaded: `manage.py import_visit` reads the
  typed-up visit form (every figure tagged with its box, C1, K03, O22.1) and sets one plate of
  each dosa, rava dosa, idly and vada dish (batter, filling, sambar and chutneys, in ounces as the
  chefs said them), the six menu buttons sold with no size, what a bucket of batter weighs, the
  scoop and ladle, which list each kitchen-made item is counted on, and make-more levels for the
  batters and sambar. A day's sales now take batter, sambar and chutney out of stock. Dry run
  first; running it again changes nothing; a changed plate is a new recipe version
- The chefs' handwritten recipe corrections (tomato chutney without garlic, the one-third
  Manchurian sauce, green and tamarind chutney amounts, Mysore and coconut chutney) are in the
  recipe assistant, unapproved until the chefs read them

- Daily sales from Shift4: upload the day's "Sales Summary by Item" CSV and say which day it
  is; the file's own total is checked, the same file is refused, a second file for a day asks
  before replacing and undoes the first. Every line is matched to its menu button; recording
  takes out what each sale uses and is traceable to its file. Missing days show (ADR 0009)
- A menu button sold with no size (Dosa Batter, Rasam…) takes nothing out of stock until an
  owner says how many ounces one sale is, on the day's page — no more silent one-pound guesses
- Twice-a-month pay, as the owners run it: To pay opens on the pay period just ended (1st–15th
  or 16th–month end), with one tap for that and for everything up to today; "Last pay period"
  and "This pay period" head the period choices, and Any period opens on the last one
- Ready to go online on any container host: a Dockerfile (gunicorn, migrations on start,
  non-root), the database from `DATABASE_URL`, CSS served compressed by WhiteNoise, HTTPS-only
  cookies, redirect and HSTS behind `DJANGO_HTTPS=1`, and `/healthz` for the host's health check.
  `check --deploy` passes clean
- Owners choose their own password: `manage.py invite_owner` makes the account and prints a
  one-time link (three days) that sets the password and signs them in. No password is ever
  typed by anybody else or sent in a message. Owners get an "Owners" admin group — staff, jobs,
  items, measures, suppliers; never delete, never the ledger
- Count lists, for the owners: every item under Daily, Weekly, Monthly or Not counted,
  moved between lists with one change; add an item (made in the kitchen, grocery, vegetable,
  packaging) with its list, unit and how it comes or is kept, so it can be counted in cases or
  buckets at once; stop using an item and bring it back. A name already on the system is
  never added twice; sample (DEMO-) items never block a real one
- Delivery received: groceries, vegetables and packaging counted off the truck in the packs
  they came in — "3 case" of toor dal goes into stock as 120 lb, with what was typed kept.
  Where it arrived (restaurant or storage unit) and the supplier, which is optional
- Made today: tap what was made, say how much in the kitchen's containers ("2 bucket"), add
  what went in if known ("3 scoop toor dal"), save. The made item goes up, what went in comes
  down, each batch gets a code. Nothing is written to stock until it is saved
- One item search for the storage run, deliveries and made-today, each finding only what it
  should: a delivery never offers sambar, a batch never offers itself
- "What we count, and how often" (`tools/count_list_form.py`): the sheet to fill in at the
  restaurant — every item by layer, how often it is made or bought and counted, containers,
  and blank pages for vegetables and packaging
- Inventory in three layers: groceries and packaging (layer 1), what the kitchen makes —
  batters, sambar, chutneys (layer 2), and dishes (layer 3, never counted: they come from sales)
- Every item says which count it is on: prepared items daily, vegetables weekly, other
  groceries and packaging monthly, dishes never. Existing items were set by that rule; owners
  change any item from the item list
- Count stock shows the daily, weekly and monthly counts, each due or done, with who counted
  last and a Continue for one left half-way; each count lists only its own items
- Counting in the kitchen's own measures — buckets, bags, cases — converted to stock, with what
  was typed kept beside it. The count stays blind: the measure used last time is offered, the
  number never is
- Owners fix the record without the admin: set the end of an unfinished shift straight from
  To pay, correct any unpaid shift, add a shift somebody could not record (or one more than
  two weeks back), and cancel a shift entered by mistake — every change with a reason, kept
- Owner corrections and entries follow the same rules as the tablet: no overlaps, nothing in
  the future, nothing over 16 hours; paid shifts stay locked
- A cancelled shift is never deleted: it stops counting everywhere and is shown as cancelled
  on the person's page and on the worker's own week
- Paying out on the owners' own cycle: choose a day, every unpaid hour up to it is listed per
  person from the first day not yet paid, tick who is paid, check, mark as paid
- Unpaid is kept per shift, so a shift written in late for a period already paid is picked up
  by the next payment and says so; nothing is paid twice or falls between two periods
- Paid hours are locked: a paid shift cannot be corrected, and a duplicate holding paid hours
  cannot be merged, until the payment is undone — with a reason, the record kept
- People added on the tablet cannot be paid until an owner confirms or merges them; shifts
  with no end time are never paid
- Payments history, a pay statement per person to print or save as a PDF, and a CSV per payment
- Workers see "paid" against their own shifts
- The tablet asks for your name from a list, not a row of buttons, with the PIN on the same
  screen — so the same person is never spelled two ways
- "My name isn't on the list — add me": first name, last name, job and a PIN. The same name
  in any form (capitals, spacing, first and last swapped) is refused and points at the person;
  a close one ("Ravi Kumaar", "Ravi K") asks "Is this you?" first. The PIN never travels
  through the page
- Staff hours, for the owners: every shift in a period added into one total per person, in
  minutes so the total always equals its shifts; morning, evening and the catering part;
  unfinished shifts shown and not counted; late, corrected and new entries marked
- A statement per person, printable or saved as a PDF to hand over, and CSV downloads of the
  report and of each statement
- People added on the tablet are listed for an owner to confirm, or merge into the right
  name — every shift moves across and the move is kept with the shift
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
- Library floors raised to the versions Dependabot proposed (Django 5.2.17, Pillow 12.3,
  python-dotenv 1.2.3, psycopg 3.3.5, dj-database-url 3.1.2), and the CI actions to
  checkout v7 / setup-python v7. WhiteNoise moves into the base requirements
- The 3–5pm closure is no longer deducted as a standard break. Morning and evening are
  separate shifts and hours are the plain span of each (ADR 0008 supersedes FR-103/FR-104)

### Fixed
- Looking an item up by name found a retired copy before the one in use ("Chana Masala",
  "Masala Dosa"), so anything matched by name could land on the dead item
- Choices inside a form (the count and item forms) were shown stacked and oversized because
  the general form style reached them; they sit side by side again
- A person who added themselves on the tablet could seem missing from the name list: the
  browser was showing a kept copy of the page. Every hours page is now never stored, which
  also stops Back showing the last worker's week to the next person
- Count stock offered "Continue" on sheets left unfinished days ago, built before counts had
  their own items — so daily, weekly and monthly all showed the same old list. Only a sheet
  from this day, week or month is continued; starting afresh retires the old one, kept as void
- Multi-line `{# … #}` template comments were being shown to the user rather than
  treated as comments — visible on the counting screen
