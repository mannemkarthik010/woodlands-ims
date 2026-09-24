# ADR 0008 — Morning and evening are separate shifts

**Status:** Accepted · **Date:** 2026-09-24 · **Supersedes:** the break-deduction
design in the first labour model, and requirements FR-103 and FR-104 as written.

## Context

The first labour model treated a working day as one long shift with the 3–5pm
closure deducted as a standard unpaid break. The reasoning was sound on paper:
the restaurant closes, everyone breaks together, so why make each person clock
out and back in.

The owners have since described how the day actually runs, and it is not that.

- There is a **morning shift and an evening shift**, and they are different
  shifts, not one shift with a hole in it.
- **Each job role keeps its own hours.** The dosa station does not start when
  the servers do.
- A few people work **both** shifts on the same day. Most work one.

Under the old model a person who works only the morning, clocking in at 10:30
and out at 3:00, would have had two hours deducted for a break they never took
while on the clock — or, worse, only if somebody remembered to tick an
exception. The deduction was a rule about the building being applied to people.

## Decision

**Morning and evening are recorded as separate shifts.** Somebody who works
both clocks out at the closure and back in afterwards, and gets two `Shift`
rows that day. Hours worked are the plain span of each shift. Nothing is
deducted, because nothing needs to be.

**A job role (`Position`) owns the schedule.** Each role has a `ShiftTemplate`
for its morning and for its evening — start and end time — with an optional
weekday override for days that run differently. The owners edit these; the
system does not guess them.

**Clock-in decides which shift it is.** The shift whose scheduled start is
nearest the moment of clocking in is the one being started. Scheduled starts
of 10:30 and 17:00 split the day at 13:45, which matches how people actually
arrive: early or late, but near their own start.

**The schedule is copied onto the shift.** The period and the scheduled start
and end are written onto the `Shift` when it opens. Changing a role's hours
next month must not rewrite whether somebody was late last month.

**A role with no schedule still clocks in.** The shift is recorded with the
period taken from the time of day (from 3pm it is the evening) and no
scheduled times, which is visible to the owners as "no schedule set" rather
than invented.

## Consequences

- The `BreakPolicy` model and the break fields on `Shift` are removed. There
  were no real shifts recorded when this changed, so nothing was migrated.
- Somebody working a double shift taps four times a day instead of two. That
  is the cost, and it buys a record that matches what happened.
- "Worked through the closure" is no longer an exception to be ticked; it is
  simply a shift that ran across 3–5pm, and the times show it.
- FR-103 and FR-104 are kept in `requirements.json` as the report wrote them,
  since that file is the client's document; this record is where the change
  of mind lives.
