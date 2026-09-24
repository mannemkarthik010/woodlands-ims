"""
The shared-tablet PIN: setting it, checking it, and limiting guesses.

A PIN is a weak secret by nature -- four digits is ten thousand possibilities
-- and it sits on a tablet everybody walks past. What makes it acceptable is
three things together, all enforced here rather than in a screen:

- it is hashed with the same hasher as passwords, never stored in clear;
- the obvious ones (1111, 1234, 4321) are refused, because they are the first
  anybody would try;
- five wrong tries lock that person's PIN for five minutes, which turns
  "guess the PIN" from a minute's work into days of standing at the tablet.

An owner never has a PIN. Owners sign in with a real password on their own
device, so nothing reachable from the tablet can see pay or change a record.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from apps.core.models import User

MIN_LENGTH = 4
MAX_LENGTH = 6
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=5)


class PinError(Exception):
    """Raised with a sentence that can be shown to the person as it is."""


def _is_obvious(pin: str) -> bool:
    if len(set(pin)) == 1:
        return True
    digits = [int(d) for d in pin]
    steps = {b - a for a, b in zip(digits, digits[1:], strict=False)}
    return steps in ({1}, {-1})


# Implements: FR-102, NFR-09.
def set_pin(user: User, pin: str) -> None:
    if not user.can_use_pin:
        raise PinError("Owners sign in with a password, not a PIN.")
    pin = (pin or "").strip()
    if not pin.isdigit() or not MIN_LENGTH <= len(pin) <= MAX_LENGTH:
        raise PinError(f"A PIN is {MIN_LENGTH} to {MAX_LENGTH} digits.")
    if _is_obvious(pin):
        raise PinError("That PIN is too easy to guess. Avoid repeated or consecutive digits.")
    user.pin = make_password(pin)
    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    user.save(update_fields=["pin", "pin_failed_attempts", "pin_locked_until"])


def is_locked(user: User, now=None) -> bool:
    now = now or timezone.now()
    return user.pin_locked_until is not None and user.pin_locked_until > now


# Implements: FR-102, D-04.
def check_pin(user: User, pin: str, now=None) -> bool:
    """
    True only for the right PIN, from somebody allowed to use one, while not
    locked out. A locked PIN is refused even when it is correct -- otherwise
    the lock would only slow down the wrong guesses.
    """
    now = now or timezone.now()
    if not user.can_use_pin or not user.pin or not user.is_active or not user.is_active_staff:
        return False
    if is_locked(user, now):
        return False

    if check_password(pin or "", user.pin):
        if user.pin_failed_attempts or user.pin_locked_until:
            user.pin_failed_attempts = 0
            user.pin_locked_until = None
            user.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
        return True

    user.pin_failed_attempts += 1
    if user.pin_failed_attempts >= MAX_FAILED_ATTEMPTS:
        user.pin_failed_attempts = 0
        user.pin_locked_until = now + LOCKOUT
    user.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
    return False
