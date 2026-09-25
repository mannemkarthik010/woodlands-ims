# Runbook

What to do, and what to do when something is wrong. Written for whoever is
holding this next — which may be a different developer, or the same one a year
later with no memory of any of it (NFR-15).

Commands assume the virtual environment is active:

```bash
source .venv/bin/activate
```

---

## 1. Starting from nothing

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then fill in DJANGO_SECRET_KEY
python manage.py migrate
python manage.py seed                # units, categories, locations
python manage.py createsuperuser
python manage.py test apps           # everything must be green before you start
python manage.py runserver
```

`DJANGO_SECRET_KEY` is generated with:

```bash
python -c "import secrets;print(secrets.token_urlsafe(50))"
```

The application **refuses to start** with `DJANGO_DEBUG=0` and no secret key.
That is deliberate: the failure mode it prevents is a server quietly running on a
default key.

### Sample data to click around in

```bash
python manage.py seed_demo           # 24 DEMO- items with stock
python manage.py seed_demo --clear   # removes them again
```

Everything it creates is prefixed `DEMO-`, so it is always obvious what is real
and always safe to remove.

---

## 2. Testing on a phone

The transfer screen is meant to be used one-handed at a storage-unit door. How
it feels in a desktop browser tells you almost nothing.

```bash
python manage.py phone
```

It finds the laptop's address on the wi-fi, binds to `0.0.0.0` and prints the URL
to type into the phone. Both devices must be on the same network.

**If the phone cannot reach it**, in this order:

1. Are both devices on the same wi-fi? (Not one on guest wi-fi, not one on 5G.)
2. Is the macOS firewall blocking Python?
   *System Settings → Network → Firewall.*
3. Does the site load on the laptop itself at the same address? If the laptop
   cannot load it either, the problem is the application, not the network.

That third check matters. The one time this looked like a networking fault, the
actual cause was a missing database — the server was starting and then failing on
the first request. Check the simplest thing that distinguishes the two.

---

## 3. Setting up the hours tablet

Staff write in their own hours at the end of a shift: date, when they
started, when they left. It runs on one shared tablet at the kitchen.

1. **Positions and their hours.** Admin → Positions. One row per job (dosa
   station, prep, server…), each with a morning and an evening start and end.
   Add a row with a weekday only for a day that runs differently.
2. **A sign-in for the tablet itself.** Admin → Users → add a user such as
   `kitchen-tablet`, role *Kitchen staff*, with a long password. Sign in as it
   once on the tablet and leave it signed in. It is the tablet's account, not
   a person's.
3. **Each person.** Admin → Users → the person: set their *Position*, a
   *Display name* (what shows on the tablet), and a *New tablet PIN*. Somebody
   without a PIN does not appear on the tablet.
4. On the tablet, open **My hours** from the home screen.

A PIN is 4–6 digits; 1111, 1234 and the like are refused. Five wrong tries
lock that person out for five minutes. Owners have no PIN and never appear on
the tablet.

A worker can fill in any of the last 14 days. Anything older, or anything
wrong on an entry, is for an owner to add or correct; every correction keeps
the original and the reason.

### Somebody is not on the list

They tap **My name isn't on the list — add me** and give their name, job and a
PIN. The tablet refuses a name already on the list (whatever the spelling of
capitals, spacing or order) and asks "Is this you?" about a close one. Everybody
added this way appears at the top of **Staff hours** for an owner to look at:

- **Confirm — new person** if they are who they say.
- **Merge** into the right name if they are a second spelling of somebody. Every
  shift moves across and each move is kept with the shift's corrections. A merge
  is refused if both names hold the same hours; correct one of them first.

### Paying out

The owners run their own pay cycle. **Staff hours & pay → To pay** shows every
unpaid hour up to a day they choose, one line per person, starting from the
first day not yet paid. Tick who is being paid, **Mark as paid…**, check the
summary, confirm. Next time the screen starts from what is left.

- "Unpaid" is kept per shift, not by date. A shift written in late for a
  fortnight already paid is picked up by the next payment and marked
  *from a period already paid*. Pressing the button twice pays nothing twice.
- People added on the tablet cannot be paid until confirmed or merged. Shifts
  with no end time are never paid; they wait until an owner fixes them.
- A paid shift cannot be corrected. If a payment was marked by mistake, open it
  under **Payments** and **Undo this payment** with a reason: its hours become
  unpaid again and the payment stays in the list, marked undone.
- Each payment has a **Statement** per person to print or save as a PDF and hand
  over, and a CSV of the whole payment.

### Fixing a shift

Owners correct the record from **Staff hours & pay**:

- **No end time.** To pay lists every unfinished shift under *Needs fixing before
  paying* with a **Fix** button. Set when the person left; the shift then counts.
- **Anything else wrong.** Open a person (click their name) and press **Fix** on
  the shift: date, start, end, morning or evening, catering. A reason is always
  asked for and kept, with the old value, under the shift's *History*.
- **A shift nobody recorded,** or one more than two weeks back: **Add a shift** on
  the person's page. It is marked *added by an owner* with the reason.
- **A shift that should not exist** (entered twice, a day not worked): **Fix →
  Cancel this shift**. It stops counting everywhere, still shows on the person's
  page and the worker's tablet as cancelled, and is never deleted.

Owner changes follow the same rules as the tablet — no overlaps, nothing in the
future, no shift over 16 hours — and a paid shift cannot be changed until its
payment is undone.

### Hours for any period

**Any period** adds every shift in a range into one total per
person — morning, evening, the part of it that was catering, and the total in
both `41 h 35 m` and `41.58`. Totals are added in minutes, so they always equal
the shifts they came from. A shift with no end time is **not counted** and is
shown in red, so it is fixed before payday rather than paid as nothing.

Open a person for their statement: every shift, with anything written late or
corrected marked. **Print or save as PDF** gives the page to hand to them;
**Download (CSV)** gives the same in a spreadsheet. The week starts on Monday
(`LABOUR_WEEK_STARTS` in settings, 0 = Monday).

To try it with sample people: `python manage.py seed_demo` adds three demo
staff (PIN 2580) and a `demo-tablet` sign-in. `seed_demo --clear` deactivates
them.

---

## 4. Counting stock

Inventory is in three layers. **Layer 1** is what is bought — groceries,
vegetables, packaging. **Layer 2** is what the kitchen makes — batters, sambar,
chutneys, gravies. **Layer 3** is the dishes on the menu; they are cooked to
order and never counted, and what they used comes from the day's sales.

**Count stock** shows three counts, each with only its own items:

| Count | What is on it | Due |
|---|---|---|
| Daily | Layer 2 — what the kitchen makes | every day |
| Weekly | Vegetables | 7 days after the last one |
| Monthly | Other groceries and packaging, at the restaurant and at the storage unit | once a calendar month |

Each item's **Count every** is set in Admin → Items (editable straight from the
list, and filterable). A new item starts on the rule above.

Count in whatever the kitchen sees: pick *bucket*, *bag*, *case* or the base unit
beside the number, and the system converts. The measures come from the item's
*measures* in the admin — an item with none can only be counted in its base
unit, so give the prepared items their containers (a bucket of sambar is 32 lb).
The expected quantity is never shown while counting; the difference appears on
the review screen afterwards.

---

## 5. Everyday commands

| Task | Command |
|---|---|
| Run the tests | `python manage.py test apps` |
| Lint and format | `ruff check . && ruff format .` |
| Regenerate documentation | `python manage.py docs` |
| Check documentation is current | `python manage.py docs --check` |
| Import the menu from Shift4 | `python manage.py import_menu path/to/menu.csv` |
| See what a menu import would do | `python manage.py import_menu path/to/menu.csv --dry-run` |
| New migration after a model change | `python manage.py makemigrations` |

After **any** model change, `makemigrations` and `docs` belong in the same commit
as the change. CI runs `makemigrations --check` and `docs --check` and fails the
build otherwise, which is the only reliable way documentation stays true.

---

## 6. When something is wrong

### Stock on hand looks wrong

Do **not** edit a balance. There is no supported way to, and the admin will not
let you — `StockBalance` and `StockMovement` are read-only there for everybody,
superusers included.

1. Read the movements for that item and location in the admin. Every change has
   a who, a when, a why and a source document. The wrong number got there
   somehow, and the ledger says how.
2. If the movements are right and the displayed figure is wrong, the cache has
   drifted. Rebuild it:

   ```bash
   python manage.py shell -c "from apps.stock.services import rebuild_balances; print(rebuild_balances())"
   ```

3. If a movement itself is wrong, reverse it. The original stays; the correction
   is a new row.

   ```python
   from apps.stock.models import StockMovement
   from apps.stock.services import reverse_movement

   reverse_movement(StockMovement.objects.get(pk=123), reason="Counted the wrong shelf")
   ```

If rebuilding the cache changes the answer, something wrote to `StockBalance`
without going through `services.py`. Find it and fix it there — that is the
invariant the whole design rests on.

### A sales import will not post

By design. `SalesImport.is_safe_to_post` refuses while any line is unmapped.
Map the outstanding `PosItem` rows to dishes, or mark them `ignore` if they are
not stock-bearing (a service charge, a prix-fixe parent line), then post.

Do not "solve" this by mapping something to a near-enough dish. A wrong mapping
is silently wrong for months; an unmapped line is loudly wrong for an afternoon.

### The same day was imported twice

It cannot double-count. An import is keyed on business date and file hash, and
re-importing a date supersedes the earlier import rather than adding to it.
Check `SalesImport` for the date: exactly one row should be live, the rest
`SUPERSEDED`.

### Prix-fixe meals look doubled

They are not, if the parent lines are still ignored. `1Prix Fixe - Adult` at
$17.25 wraps courses that appear as separate $0.00 lines; counting both doubles
every prix-fixe meal. The import marks the parents `ignore` on first sight, and
re-importing never undoes a human decision. If somebody has since mapped a
parent line to a dish, unmap it.

### Migrations conflict after a merge

```bash
python manage.py makemigrations --merge
```

Read what it produces before committing it. If two branches changed the same
model, a merge migration is a patch over a disagreement that a person still has
to settle.

### `manage.py` refuses to start

Read the error rather than deleting anything.

- *"DJANGO_SECRET_KEY is not set"* — `.env` is missing or empty.
- *"no such table: …"* — migrations have not been run against this database, or
  the database file is gone. `python manage.py migrate`.
- `ModuleNotFoundError` — the virtual environment is not active.

---

## 7. The database

Development uses SQLite: a single file, `db.sqlite3`, in the project root. It is
git-ignored, so it is **not** recoverable from the repository.

**Nothing routine deletes it.** It holds the superuser account, the seeded
catalogue and any real data entered so far, and rebuilding it means recreating
all of that by hand. Back it up before anything that touches schema or data:

```bash
cp db.sqlite3 "db.sqlite3.$(date +%Y%m%d-%H%M)"
```

A data-only export, portable across database engines and worth taking before any
risky migration:

```bash
python manage.py dumpdata --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission --indent 2 > backup.json
```

Restoring into an empty, migrated database:

```bash
python manage.py loaddata backup.json
```

Production will use PostgreSQL with an automated daily backup and a **tested**
restore before go-live (FR-1206, NFR-13). Untested backups are not backups; the
restore gets rehearsed, not assumed.

### Forgotten password

```bash
python manage.py changepassword <username>
```

---

## 8. Branching and release

- `main` is always green. Work happens on `feat/…` or `fix/…` branches.
- A pull request runs lint, format check, Django system check, the missing
  migration check, `docs --check` and the tests. All of it has to pass.
- `CHANGELOG.md` gets a line per user-visible change, written in the language
  the owners use — "storage runs", not "transfer view refactor".
- Install the hooks once so the first check happens before the commit, not in
  CI:

  ```bash
  pre-commit install
  ```

  If that reports a `core.hooksPath` conflict, a global hooks directory is
  configured. Unset it, install, and set it back.

---

## 9. Deployment

Deliberately not configured yet. Hosting carries a monthly cost and the choice
is the client's to make, informed, in advance. Nothing has been committed on
their behalf.

What is already true, so that the decision is not blocked by engineering:

- Settings read from the environment; no secret is in the repository.
- `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` are env-driven.
- `requirements-prod.txt` adds only what a server needs (`psycopg`, `gunicorn`).
- The permissive host and CSRF settings used for phone testing apply **only**
  when `DJANGO_DEBUG=1`.

Before the first deploy: secret key generated fresh on the server, `DEBUG=0`,
HTTPS, daily backup configured, and a restore performed at least once into a
throwaway database.

---

## 10. Getting data out

The client can take their data at any time, without asking anybody (FR-1207):

```bash
python manage.py dumpdata --indent 2 > woodlands-data.json
```

Their data is theirs. That is not a feature to be negotiated at the end of an
engagement; it is the reason there is no vendor lock-in in the design (NFR-16).
