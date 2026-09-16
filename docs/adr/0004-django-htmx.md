# ADR 0004 — Django with server-rendered pages

**Status:** Accepted · **Date:** 2026-09-16

## Context

One codebase must serve a shared tablet in the restaurant and the owners'
laptops remotely. Two maintainers, a four-week first delivery, near-zero running
cost, and a retrieval-based culinary assistant planned later.

## Decision

Django 5 + PostgreSQL, server-rendered with HTMX for interactivity, installable
as a PWA on the tablet. SQLite locally.

## Consequences

**Good.** The Django admin is a working back-office from day one — for the item
master alone that is close to a week not spent. Auth, roles, migrations and the
ORM come with it. One language, which matters at two people. The assistant will
live in the same codebase. Django is the most boring, best-documented choice
available, which is what makes handover credible.

**Costs.** Less slick than a React SPA. For "tap item, type number, save" on a
wet tablet that is the right trade, and an API can be added under a richer
front end later without touching the data model.
