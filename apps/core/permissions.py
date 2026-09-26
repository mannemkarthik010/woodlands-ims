"""Who may see what. One definition, used by every app."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from apps.core.models import Role


def is_owner(user) -> bool:
    return user.is_authenticated and (user.role == Role.OWNER or user.is_superuser)


# Implements: FR-1202.
def owner_required(view):
    """Signed in, and an owner. Everybody else gets a plain refusal, not the page."""

    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_owner(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped
