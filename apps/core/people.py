"""
Adding a person from the tablet, without adding the same person twice.

A worker who is not on the list can add themselves, so nobody is ever stuck
at the end of a shift unable to record it. The risk that comes with that is
duplicates: "Ravi Kumar" on Monday, "Kumar Ravi" on Wednesday, "ravi kumaar"
on Friday -- three people as far as the system knows, and three partial
totals on payday instead of one. So before anybody is added:

- the same name, ignoring capitals, spacing and which name comes first, is
  refused outright and the person is sent back to pick themselves;
- a name that is merely close ("Ravi K", "Ravi Kumaar") is shown back as
  "Is this you?", and only an explicit "No, I'm a different person" goes on;
- everybody added this way is marked for an owner to look over, and if a
  duplicate still gets through, the owner merges it and the hours move with it.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from itertools import permutations

from django.db import transaction
from django.utils.text import slugify

from apps.core.models import Position, Role, User
from apps.core.pins import set_pin, validate_pin

# How alike two names must be, 0 to 1, before we ask "Is this you?".
# Measured on the slips that happen on a tablet: "Ravi Kumaar" 0.95, "Jon
# Smith" 0.95, "Meena Nair" for "Meera Nair" 0.90 -- all asked. "Priya Verma"
# against "Priya Sharma" is 0.78 and is not. Some genuinely different people
# get asked too ("Anil" and "Sunil Kumar"); that costs one tap, where a
# duplicate that slips through costs somebody half their hours on payday.
SIMILAR = 0.85


class PersonError(Exception):
    """Raised with a sentence that can be shown to the person as it is."""


class AlreadyListed(PersonError):
    def __init__(self, person: User):
        self.person = person
        super().__init__(f"{person} is already on the list. Please pick your name.")


class LooksLike(PersonError):
    def __init__(self, people: list[User]):
        self.people = people
        super().__init__("Somebody with a name like yours is already on the list.")


def _clean(part: str) -> str:
    """One name, tidied: no accents, no stray punctuation, single spaces."""
    part = unicodedata.normalize("NFKD", part or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z' -]", " ", part).split())


def name_key(first: str, last: str = "") -> str:
    """
    What makes two names the same name. Case, spacing, accents and order are
    ignored, so "Ravi Kumar", "kumar  ravi" and "RAVI KUMAR" share a key.
    """
    words = f"{_clean(first)} {_clean(last)}".casefold().replace("'", "").split()
    return " ".join(sorted(words))


def _key_of(person: User) -> str:
    if person.first_name or person.last_name:
        return name_key(person.first_name, person.last_name)
    return name_key(person.display_name or person.username)


def _is_close(a: str, b: str) -> bool:
    """
    Close enough to ask "Is this you?". Both are name keys. Words are
    compared in whichever order lines them up best -- sorting alone would
    put "kavi kumar" and "kumar ravi" in different orders and hide a
    one-letter slip at the start of a name.
    """
    if not a or not b:
        return False
    aw, bw = a.split(), b.split()
    orders = permutations(bw) if len(bw) <= 4 else [bw]
    if max(SequenceMatcher(None, a, " ".join(o)).ratio() for o in orders) >= SIMILAR:
        return True

    # "Ravi K" or plain "Ravi" against "Ravi Kumar": every word of one name
    # starts a word of the other.
    def covered(xs, ys):
        return all(any(y.startswith(x) for y in ys) for x in xs)

    return covered(aw, bw) or covered(bw, aw)


def _listed():
    return User.objects.filter(is_active=True, is_active_staff=True).exclude(role=Role.OWNER)


def find_matches(first: str, last: str = "") -> tuple[User | None, list[User]]:
    """The person with exactly this name, if any, and the people with a close one."""
    key = name_key(first, last)
    exact, close = None, []
    for person in _listed():
        other = _key_of(person)
        if other == key:
            exact = person
        elif _is_close(key, other):
            close.append(person)
    return exact, close


def _username_for(first: str, last: str) -> str:
    base = slugify(f"{first} {last}".strip())[:140] or "staff"
    candidate, n = base, 2
    while User.objects.filter(username=candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


@transaction.atomic
def add_person(
    first: str,
    last: str,
    *,
    pin: str = "",
    pin_hash: str = "",
    position: Position | None = None,
    different_from: list[User] | None = None,
) -> User:
    """
    A new member of staff, added on the tablet.

    `different_from` is the list of close matches the person has already been
    shown and said they are not. If the matches have changed since -- somebody
    else was added in between -- they are shown again.

    `pin_hash` is for that second step: the PIN was checked against the rules
    and hashed on the first, so it never has to travel through the page.
    """
    first, last = _clean(first).title(), _clean(last).title()
    if not first:
        raise PersonError("Please enter your first name.")
    if not pin_hash:
        validate_pin(pin, role=Role.KITCHEN)

    exact, close = find_matches(first, last)
    if exact:
        raise AlreadyListed(exact)
    already_seen = {p.pk for p in (different_from or [])}
    if close and not {p.pk for p in close} <= already_seen:
        raise LooksLike(close)

    person = User(
        username=_username_for(first, last),
        first_name=first,
        last_name=last,
        display_name=f"{first} {last}".strip(),
        role=Role.KITCHEN,
        position=position,
        needs_review=True,
        possible_duplicate_of=close[0] if close else None,
    )
    person.set_unusable_password()
    if pin_hash:
        person.pin = pin_hash
        person.save()
    else:
        person.save()
        set_pin(person, pin)
    return person
