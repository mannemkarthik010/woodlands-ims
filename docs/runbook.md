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
python manage.py seed                # units, categories, locations, break policy
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

## 3. Everyday commands

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

## 4. When something is wrong

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

## 5. The database

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

## 6. Branching and release

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

## 7. Deployment

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

## 8. Getting data out

The client can take their data at any time, without asking anybody (FR-1207):

```bash
python manage.py dumpdata --indent 2 > woodlands-data.json
```

Their data is theirs. That is not a feature to be negotiated at the end of an
engagement; it is the reason there is no vendor lock-in in the design (NFR-16).
