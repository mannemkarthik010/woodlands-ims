"""
New owners choose their own password.

Nobody types a password for somebody else, and none travels through Slack or
email. An owner's account is made without a usable password, and they are
sent a one-time link -- privately -- that asks them to choose one. The link is
Django's own password-reset token: it stops working the moment the password
is set, and after PASSWORD_RESET_TIMEOUT regardless.

Owners get the "Owners" group: enough of the admin to run the restaurant's
side of the system -- staff and their PINs, jobs and shift times, items,
measures, suppliers -- and nothing that edits the stock ledger, which is
read-only to everybody.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.models import Role, User

OWNER_GROUP = "Owners"

# (app, model) the owners manage in the admin; view, add and change -- never delete.
OWNER_MODELS = [
    ("core", "user"),
    ("core", "position"),
    ("core", "supplier"),
    ("labour", "shifttemplate"),
    ("catalog", "item"),
    ("catalog", "itemmeasure"),
    ("catalog", "itemalias"),
]


def owners_group() -> Group:
    group, _ = Group.objects.get_or_create(name=OWNER_GROUP)
    wanted = Permission.objects.none()
    for app, model in OWNER_MODELS:
        wanted |= Permission.objects.filter(
            content_type__app_label=app,
            content_type__model=model,
            codename__in=[f"view_{model}", f"add_{model}", f"change_{model}"],
        )
    group.permissions.set(wanted)
    return group


def set_password_link(user: User) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return settings.SITE_URL + reverse("welcome", args=[uid, token])


@transaction.atomic
def invite_owner(username: str, *, display_name: str = "") -> tuple[User, str, bool]:
    """
    The owner's account, ready for them to choose a password, and the link
    that lets them. Asking again for somebody who already has a password
    gives a fresh link without touching their password -- if they have lost
    it, the link lets them choose a new one. Returns (user, link, created).
    """
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("A username is needed, e.g. pj or jaspinder.")
    user, created = User.objects.get_or_create(username=username)
    if not created and user.role != Role.OWNER:
        raise ValueError(f"{username} is a {user.get_role_display().lower()} account, not an owner.")
    user.role = Role.OWNER
    user.is_staff = True
    user.is_active = True
    if display_name:
        user.display_name = display_name
    if created:
        user.set_unusable_password()
    user.save()
    user.groups.add(owners_group())
    return user, set_password_link(user), created
