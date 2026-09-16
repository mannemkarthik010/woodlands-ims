# How we work on this

Two people, four weeks, a real client. The practices below are the ones that
earn their keep at that size. Anything heavier is ceremony, and ceremony is how
a small team gets slow while feeling organised.

## Branches

`main` is always deployable. Never commit to it directly — pre-commit blocks it.

    feat/transfer-screen
    fix/unit-conversion-rounding
    chore/ci-caching
    docs/adr-0005

Short-lived. A branch open more than two days is usually two changes.

## Commits

    <type>: <what changed, imperative>

    Why it changed. The problem, not the diff — the diff is visible.
    Anything surprising, and anything a future reader would otherwise
    have to work out.

Types: `feat` `fix` `refactor` `docs` `test` `chore` `perf`.

The body is the part that matters. "Fixed bug" tells nobody anything in March.

## Pull requests

Every change, including solo. Opening a PR and reading your own diff catches a
surprising amount — it is a different way of looking at the same code.

The template checklist is short on purpose. The line worth pausing on:

> Ledger invariant intact: nothing writes stock outside `stock.services`

That one is not a style preference. See ADR 0001.

## Definition of done

Not "it works on my machine":

- [ ] Tests cover it, and they fail if the change is reverted
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Migration included if a model changed
- [ ] Works on a phone-sized screen if a staff member will touch it
- [ ] Someone who is not you could work out how to use it
- [ ] README or an ADR updated if a decision changed

## Migrations

**Never edit a migration that has been applied anywhere but your laptop.** Write
a new one. CI runs `makemigrations --check` so a model change without a
migration fails the build rather than the deploy.

## Secrets

Never in the repo, never in Slack, never in a commit message. `.env` is ignored
and `detect-private-key` runs on every commit. If one does get committed, rotate
it — git history is forever, and removing it from the tip removes nothing.

## Decisions

Anything hard to reverse, or that will look wrong without context, gets an ADR
in `docs/adr/`. Two minutes now against an hour of archaeology later.

## Client-facing discipline

Requirements and decisions live in `docs/`, not in Slack. When something is
agreed in Slack, write it into the document and say so in the channel. A
requirement that exists only in a chat thread will be remembered differently by
both sides, and the client is not the one who will be blamed for that.

## What we deliberately do not do

Story points. Velocity tracking. Daily standups — there are two of us. Multiple
approval chains. A staging sign-off form. GitFlow. These solve coordination
problems that appear at thirty engineers and cost real time at two.
