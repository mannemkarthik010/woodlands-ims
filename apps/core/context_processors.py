"""The navigation every page shows, with the current section marked."""

from django.urls import reverse

from apps.core.permissions import is_owner

# (label, url name, the paths that count as being in this section)
OWNER_NAV = [
    ("Dashboard", "home", ("/",)),
    ("Team", "team_today", ("/team/",)),
    (
        "Hours & pay",
        "hours_pay",
        ("/hours/pay", "/hours/payments", "/hours/report", "/hours/shifts", "/hours/people"),
    ),
    ("Sales", "sales_home", ("/sales/", "/mapping/")),
    ("Thali", "thali", ("/thali/",)),
    ("Stock", "count_home", ("/count/", "/transfer/")),
    ("Made today", "made_today", ("/made/",)),
    ("Deliveries", "receipt_new", ("/delivery/",)),
    ("Recipes", "ask_page", ("/ask/",)),
]
STAFF_NAV = [
    ("Home", "home", ("/",)),
    ("My hours", "hours_start", ("/hours/",)),
    ("Team", "team_today", ("/team/",)),
    ("Stock count", "count_home", ("/count/",)),
    ("Made today", "made_today", ("/made/",)),
    ("Deliveries", "receipt_new", ("/delivery/",)),
    ("Recipes", "ask_page", ("/ask/",)),
]


def _active(path: str, prefixes) -> bool:
    return any(path == p if p == "/" else path.startswith(p) for p in prefixes)


def nav(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav": []}
    owner = is_owner(user)
    entries = OWNER_NAV if owner else STAFF_NAV
    path = request.path
    items = [
        {"label": label, "url": reverse(name), "on": _active(path, prefixes)}
        for label, name, prefixes in entries
    ]
    return {"nav": items, "nav_is_owner": owner}
