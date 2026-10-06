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

The owners pay **twice a month**: the 1st to the 15th, and the 16th to the end
of the month. **Staff hours & pay → To pay** opens on the pay period that has
just ended (up to the 15th from the 16th on; up to the month's last day from the
1st to the 15th), with one-tap buttons for that and for everything up to today,
and the date can still be set to any day. It shows every unpaid hour up to that
day, one line per person, starting from the first day not yet paid. Tick who is being paid, **Mark as paid…**, check the
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
(`LABOUR_WEEK_STARTS` in settings, 0 = Monday). *Last pay period* and *This pay
period* are the half-months, and **Any period** opens on the last pay period.

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

**What is on each list is the owners' decision**, made on **Count stock → Change
what is on each list** (owners only):

- every item is shown under Daily, Weekly, Monthly or Not counted — change the
  list beside it and it moves at once; the next count uses the new list;
- **+ Add an item**: its name, what it is (made in the kitchen, grocery,
  vegetable or herb, packaging), which list, the unit it is kept in, and — if
  known — how it comes or what it is kept in (*case* of 40 lb, *bucket* of 32 lb),
  so it can be counted and received in cases or buckets from the first day.
  A name already on the system, in any spelling of capitals and spaces, is refused;
- **Stop using** takes an item off every list and keeps its history; it can be
  brought back from *No longer used*.

This is where the answers from the restaurant visit are entered.

Count in whatever the kitchen sees: pick *bucket*, *bag*, *case* or the base unit
beside the number, and the system converts. The measures come from the item's
*measures* in the admin — an item with none can only be counted in its base
unit, so give the prepared items their containers (a bucket of sambar is 32 lb).
The expected quantity is never shown while counting; the difference appears on
the review screen afterwards.

---

### Stock coming in, and what the kitchen makes

- **Delivery received** — search each thing as it comes off the truck and give the
  number in the pack it came in (case, bag, box). Only groceries, vegetables and
  packaging can be delivered. Choose where it arrived and, if known, the supplier
  (suppliers are added in Admin → Suppliers). **Record** puts it into stock.
- **Made today** — tap what was made, say how much in its container ("2 bucket"),
  and add what went in if the cook knows it. **Save** adds the made item and takes
  what went in out of stock. A batch started by mistake can be thrown away; nothing
  was written to stock until it was saved.

Stock can go below zero until opening balances are entered — the ledger records
what happened, and a count afterwards puts it right.

---

### Daily sales (owners)

**Daily sales** takes Shift4's report for one day:

1. In Shift4 Customer Hub: **Reports → Sales Summary by Item**, one day, **Export
   → CSV**. (Better still, subscribe it to arrive daily as CSV.)
2. **Daily sales → Read the file**: choose the day it is for — the file does not
   say — and the CSV. The same file twice is refused; a second file for a day
   asks before replacing, and replacing puts back what the first took out.
3. On the day's page: any menu button not yet matched to a dish (match it on
   **Menu mapping** — nothing is recorded until every line is); any button sold
   with no size, asking **how many ounces one sale is**; what recording will
   take out of stock; and which dishes have no recipe yet.
4. **Record these sales.** Each movement is dated the business day and says which
   file it came from.

The list shows the last three weeks; a day with no file says so.

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

**Not live yet.** Hosting costs the restaurant money every month, so it waits for
the owners' yes. Everything the code can do in advance is done, so that going
live is an hour, not a day.

### What is ready

- **One container** (`Dockerfile`) that any container host runs as-is — Render,
  Railway, Fly, DigitalOcean App Platform. It installs the server requirements,
  compresses the CSS at build time, runs as an ordinary user, applies database
  migrations on every start, and serves with gunicorn. `.dockerignore` keeps the
  database, `.env`, client data and outputs out of the image.
- **The database** comes from `DATABASE_URL` (the host's PostgreSQL). Without it,
  the local SQLite file is used, as on the laptop.
- **CSS** is served by the app itself (WhiteNoise), compressed.
- **HTTPS only** when `DJANGO_HTTPS=1` (the image sets it): redirect to HTTPS,
  secure cookies, HSTS for 30 days. `manage.py check --deploy` passes clean; the
  two silenced checks (HSTS subdomains and preload) are deliberately off on a
  host's shared address — see settings.
- **`/healthz`** answers `ok` when the app can reach its database, `503` when it
  cannot. Point the host's health check at it. No login, no data.

Tested on this machine: gunicorn with `DEBUG=0` and a `DATABASE_URL` serves the
pages, the compressed CSS and the health check. **The image itself has not been
built yet** (Docker was not running) — build it once before the first deploy:

```bash
docker build -t woodlands-ims .
```

### On Vercel (the owners' choice, 6 October 2026)

The owners chose Vercel. The container above stays as the fallback if Vercel
ever stops suiting.

- `api/index.py` hands Django to Vercel's Python runtime; `vercel.json` sends
  every address to it. `api/requirements.txt` is what Vercel installs.
- **`.vercelignore` keeps the client's data off Vercel** — `data/`, the scanned
  forms, outputs, the local database. Never delete it.
- **The database must be PostgreSQL** (Neon, from Vercel's Storage tab, or any
  PostgreSQL). The app refuses to start on Vercel without `DATABASE_URL`.
- **CSS** is served by WhiteNoise straight from `static/` on Vercel
  (`WHITENOISE_USE_FINDERS`, switched on by Vercel's own `VERCEL=1`), because
  there is no `collectstatic` step there.
- **Migrations do not run on Vercel.** Run them from this Mac, against the
  production database, before each deploy that adds one:

  ```bash
  pip install -r requirements-prod.txt
  DATABASE_URL='postgres://…' DJANGO_SECRET_KEY=x python manage.py migrate
  ```

  The same way, once, for `seed` and the data imports (step 4a below) — the
  client files never leave this machine; only the rows they produce do.
- **Plan:** Vercel's free Hobby plan is for non-commercial use only. A
  restaurant is commercial, so it is the **Pro plan** (about $20 a month per
  member), plus the database (Neon's free tier is enough to start).
- **Limits that matter here:** a request may run 30 seconds (a day's sales file
  takes about one) and upload up to 4.5 MB (a Shift4 CSV is about 10 KB).
  There is no lasting disk: photo uploads (invoices, clock-in pictures, not
  used yet) will need Vercel Blob or similar before they are switched on.

Set in the Vercel project's Environment Variables: `DJANGO_SECRET_KEY`,
`DATABASE_URL`, `DJANGO_HTTPS=1`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL`, and for the AI (next section)
`KNOWLEDGE_ENGINE=claude`, `KNOWLEDGE_CONSENT=1` (the owners agreed on
6 October 2026), `AI_PROVIDER=vertex`, `GCP_PROJECT_ID`, `GCP_REGION=global`
and `GCP_SERVICE_ACCOUNT_JSON`.

### The AI, on the restaurant's Google Cloud (the owners' choice)

Every AI step in the project — the recipe assistant's wording, matching the
thali — goes through `apps/core/ai.py`, which reaches Claude on **Vertex AI**
in the restaurant's own Google Cloud project, billed to its Google Cloud
account (credits included). `AI_PROVIDER=anthropic` with `KNOWLEDGE_API_KEY`
reaches the same model directly instead; nothing else changes. Without either,
or without consent, every screen still works without AI.

Once, in the Google Cloud console, signed in as the restaurant:

1. Create (or choose) a project, e.g. `woodlands-ims`, with billing on.
2. Enable the **Vertex AI API** (APIs & Services → Library).
3. In **Vertex AI → Model Garden**, open **Claude Opus 5.5** and enable it
   (accept Anthropic's terms there). Until this is done every call is refused.
4. **IAM → Service accounts → Create**: `woodlands-ims-ai`, role
   **Vertex AI User** — that role only.
5. On that account, **Keys → Add key → JSON**. Paste the whole file into
   Vercel as `GCP_SERVICE_ACCOUNT_JSON`, then delete the downloaded file.
   Never put it in git, Slack or email.
6. In Vercel set `AI_PROVIDER=vertex`, `GCP_PROJECT_ID=<the project id>`,
   `GCP_REGION=global`.
7. Optional, recommended: **Billing → Budgets & alerts**, a monthly budget
   (e.g. $20) with an email alert.

Later, the key file can be replaced by Workload Identity Federation (Vercel's
OIDC token trusted by Google Cloud), which needs no stored key at all.

On a laptop: `gcloud auth application-default login` instead of the key.

### Yesterday's sales, by email (no upload needed)

Once it is set up, nobody uploads the sales file: it arrives, is read as
yesterday's, and is recorded when every line is matched. Anything that needs
a person — a new menu button, a tub with no size, a day that already has a
file, a file that will not read — is kept and left for the owners, with a
message. A hand upload still works and always wins: the email never replaces
a day that has a file.

1. Generate the secret and set it in Vercel as `SALES_INBOUND_TOKEN`:
   `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
   The address is then `https://<site>/sales/inbound/<secret>/`.
2. An inbound email address that posts to it. Any of these work as they are:
   - **Postmark** inbound stream, webhook set to the address above (JSON);
   - **Mailgun** route, or **SendGrid** Inbound Parse, forwarding to it (form);
   - **Cloudflare** Email Routing with a small worker that posts the attachment.
3. In Shift4's back office, schedule the **Sales Summary by Item** report:
   yesterday, CSV, every morning, sent to that inbound email address.
   (Item O10 on the visit form — the owners have to switch this on.)
4. Test it with last Sunday's file before relying on it:

   ```bash
   curl -F "attachment-1=@sales-summary.csv" \
     "https://<site>/sales/inbound/<secret>/?date=2026-09-27"
   ```

   `?date=` is only for tests and back-filling; the email leaves it off.

### Going live, in order

1. The owners agree the host and the monthly cost.
2. Create the app from this repository's `Dockerfile`, with a PostgreSQL
   database. Set on the host:
   - `DJANGO_SECRET_KEY` — generated fresh, on the host, never reused:
     `python -c "import secrets;print(secrets.token_urlsafe(50))"`
   - `DATABASE_URL` — the host gives this with the database
   - `DJANGO_ALLOWED_HOSTS` — the address, e.g. `woodlands.onrender.com`
   - `DJANGO_CSRF_TRUSTED_ORIGINS` — the same with `https://`
   - `SITE_URL` — the same with `https://`, used in owners' password links
3. Turn on the host's **daily database backup**, and restore one into a throwaway
   database once, to know it works.
4. From the host's shell: `python manage.py seed`, then one owner link each —
   see below.
4a. Load what the kitchen has told us, in this order (the files are in
   `data/from-client/`, which is not in git — copy them up for this step only):
   `import_ingredients`, `import_measures`, `import_menu`, then
   `import_visit visit-2026-10-02.json --dry-run`, read it, and run it again
   without `--dry-run`. Every line it prints carries the code of the box on the
   visit form it came from, and it lists the questions still open at the end.
   Then the recipes, approved by the chefs (Edwin and Anderson, 6 October 2026);
   the versions a newer sheet replaced are imported but left unpublished:

   ```bash
   N="Edwin and Anderson (chefs), passed on by Karthik, 6 Oct 2026"
   python manage.py ingest_recipes data/from-client/recipes-ramesh.md --approve --approver karthik \
       --note "$N" --leave-out "Tomato Chutney" --leave-out "Manchurian Sauce"
   python manage.py ingest_recipes data/from-client/recipes-visit-2026-10-02.md --approve --approver karthik \
       --note "$N" --leave-out "Tomato Chutney (older sheet)"
   ```
5. Open the address on the kitchen tablet, sign in as the tablet account, leave
   it signed in.

Uploaded photos (invoices, clock-in pictures) are not used yet. When they are,
they need the host's persistent disk or object storage — a container's own disk
is wiped on every deploy.

### Owner accounts — each owner chooses their own password

Nobody types a password for anybody else, and none is ever sent in Slack or
email.

The owners asked for three logins (6 October 2026), with the links sent by Slack:

```bash
python manage.py invite_owner jaspinder --name "Jaspinder"
python manage.py invite_owner pawan --name "Pawan"
python manage.py invite_owner harash --name "Harash"
```

Each makes the owner's account and prints a one-time link. Send it to that owner
**privately** — a Slack direct message to them, never the channel: whoever opens it chooses the password. It works
once, for three days, and signs them in. Run it again for the same owner to get a
fresh link — for a lost password too. Owners get the *Owners* group: in the admin
they can manage staff and PINs, jobs and shift times, items, measures and
suppliers — never delete, and never edit the stock ledger.

---

## 10. Getting data out

The client can take their data at any time, without asking anybody (FR-1207):

```bash
python manage.py dumpdata --indent 2 > woodlands-data.json
```

Their data is theirs. That is not a feature to be negotiated at the end of an
engagement; it is the reason there is no vendor lock-in in the design (NFR-16).
