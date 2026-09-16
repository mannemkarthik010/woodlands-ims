# Woodlands IMS — application

Operations & Inventory Management System for Woodlands Indian Cuisine, Chatsworth.

Django 5 + PostgreSQL. Server-rendered, responsive: one codebase serves the restaurant
tablet and the owners' laptop, with the interface changing by role.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill it in
python manage.py migrate
python manage.py seed         # units, categories, locations, break policy
python manage.py seed_demo    # sample items, so the screens have data
python manage.py createsuperuser
python manage.py test apps
python manage.py phone        # serves on the network, prints the phone URL
```

`manage.py phone` is the one to use while building screens. It binds to the
network and prints the address to open on a phone on the same wi-fi — the
transfer screen is designed for one hand at a storage-unit door, and a desktop
browser tells you almost nothing about whether it works.

`seed_demo` adds 24 sample ingredients with opening stock, all coded `DEMO-`.
Remove them with `python manage.py seed_demo --clear` before anything real
goes in.

`requirements.txt` is deliberately small and pure-Python, so a missing compiler
can never stop you running locally. The PostgreSQL driver, image handling and
the web server live in `requirements-prod.txt` and are only installed on the
server.

If you use conda, deactivate it first (`conda deactivate`) — having `(base)` and
`(.venv)` both active is a reliable source of confusion about which `pip` you
just used.

---

## The one rule

**Stock is never a number that gets edited. It is a list of movements that only gets
appended to.**

```
on_hand(item, location) = SUM(quantity) FROM stock_movement
```

Nothing — no receipt, no transfer, no count, no sale — updates a balance directly.
Everything writes movements through `apps.stock.services.post_movement`. A mistake is
corrected by writing its opposite (`reverse_movement`), never by deleting or editing the
original.

`StockBalance` is a cache for speed. It is derived and can be rebuilt from the ledger at
any time with `rebuild_balances()`. **The ledger is the truth.** There is a test that
deliberately corrupts the cache and rebuilds it, because that property is the reason to
accept the extra code.

What this buys, none of which is cheap to retrofit:

- The audit trail is free — every change already has a who, a when and a why
- Variance analysis works — theoretical and actual depletion are just movement types
- "What did we hold on the 3rd?" is answerable, forever
- Nothing drifts silently; if a balance looks wrong the movements say how it got there

## The second idea: everything is an Item

A sack of urad dal is an Item. Dosa batter is an Item. A masala dosa is an Item. They
differ only by `kind` — `RAW`, `PREPARED`, `DISH`.

This is what lets one routine (`services.explode`) walk a sale all the way down to raw
materials: a dish explodes into bases, a base explodes into raw materials, and recursion
stops at whatever has no active recipe. Without this you end up with three parallel
mechanisms that disagree.

## Units

Every quantity is stored in the item's **base unit**, as `Decimal`. Never float — not for
quantities, not for money.

Conversion happens only at the edges, when somebody types "6 sacks". `PurchaseUnit`
carries an optional supplier, because urad dal arrives in 25 lb sacks from one supplier
and 20 lb sacks from another. A system that assumes one number turns four sacks into
twenty pounds of stock that does not exist — one of the most common silent errors in food
inventory, and very hard to unpick later.

## Apps

| App | Holds |
|---|---|
| `core` | User + roles, Location, Area, Supplier, `TimeStamped` base |
| `catalog` | Unit, Item, ItemAlias, PurchaseUnit, ParLevel, Recipe, RecipeLine |
| `stock` | **StockMovement (the ledger)**, StockBalance, GoodsReceipt, Transfer, StockCount, WasteEvent, `services.py` |
| `production` | ProductionBatch, ProductionInput, BatchSplit |
| `sales` | PosItem mapping, SalesImport, SalesImportLine |
| `labour` | BreakPolicy, Shift, ShiftEdit |

## Things decided deliberately, not by accident

**`occurred_at` vs `created_at`.** A storage run made at 3pm and entered at 6pm is two
different times. Conflating them makes reconciliation impossible, so movements carry both.

**Production records what was actually used, not the formula.** `ProductionInput` is
separate from `Recipe` because batter is often judged by eye. Assuming the formula would
produce confident, wrong numbers.

**A batch can exist and be unusable.** `MATURING` vs `AVAILABLE`. Batter is not stock you
can sell until fermentation completes, and `expires_at` counts from `matured_at`, not from
production.

**Sales imports are idempotent.** Keyed on business date + file hash. Re-importing a date
supersedes the previous import rather than double-counting.

**Unmapped POS items block posting.** `SalesImport.is_safe_to_post` refuses while anything
is unmapped. An unmapped item is a visible problem; a wrongly guessed one is an invisible
wrong answer for months. No fuzzy string matching.

**Voids vs comps.** A voided order was never made — depletes nothing. A comped dish was
made and given away — depletes in full. See `SalesImportLine.quantity_to_deplete`.

**An owner cannot be reached by a tablet PIN.** `User.can_use_pin` returns False for
owners. The tablet is a shared device; the laptop is a personal one; they get different
security.

**Labour does not do payroll.** Clock in, clock out, owners see hours. That is the whole
scope, and it is deliberate.

## The admin

`/admin/` is a working back-office from day one — enough to load the item master, define
recipes, and record production before any custom screen exists.

Two things there are deliberately **read-only, including for a superuser**:

- **StockMovement** — the ledger. If it can be edited through the admin then it is not
  append-only, and every guarantee built on it quietly stops being true. Movements are
  created by `services.post_movement` and corrected by `services.reverse_movement`.
- **StockBalance** — a cache of the ledger, not a place to type.

`PosItem` has a **"Needs mapping"** filter. That is the work queue: every unmapped POS
name blocks its sales import from posting, on purpose.

## Documentation

| Document | Kept how |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | By hand. How the pieces fit, the three invariants, and what is deliberately absent. |
| [`docs/runbook.md`](docs/runbook.md) | By hand. Setup, everyday commands, and what to do when something is wrong. |
| [`docs/adr/`](docs/adr/) | By hand. One file per significant decision: what was decided, why, what it cost. |
| [`docs/data-model.md`](docs/data-model.md) | **Generated** from the models. |
| [`docs/traceability.md`](docs/traceability.md) | **Generated.** Every numbered requirement against the code and tests that name it. |
| [`docs/requirements.json`](docs/requirements.json) | The 148 numbered requirements, extracted from the Discovery & Requirements Report. |

```bash
python manage.py docs           # regenerate
python manage.py docs --check   # fail if stale — CI runs this
```

Generated documentation cannot drift, because drifting breaks the build. Code that
implements a requirement names it (`FR-403`) in a docstring or comment, and appears in
the traceability table on the next regeneration. Nothing is inferred from a function
looking roughly relevant — that would make the table reassuring and wrong.

## Built so far

- The ledger, recipe explosion, and every model in the six apps
- **Storage run (transfer)** — search by any name an item is known by, add lines, post
- **Stock count** — generated sheet, counted on a phone, variance reviewed, then committed
- **Menu import** — 318 Shift4 lines read in, grouped, and pre-classified
- **Menu mapping** — 311 unmapped lines as 252 decisions: create the dish from the group,
  read tub sizes off the name, confirm near-identical items rather than assuming (ADR 0005)
- Admin, with the ledger read-only to everybody
- 55 tests, CI, hooks, ADRs, generated documentation

## Not built yet

Goods receipt, waste, production and labour screens; the PWA manifest and offline
queue; the Shift4 CSV parser and its scheduler; PIN auth; the culinary knowledge
assistant; deployment config.

The parser waits on a real sample CSV — building it against a guessed column layout would
be wasted work. Deployment waits on the client, because hosting costs money and that
decision is theirs.
