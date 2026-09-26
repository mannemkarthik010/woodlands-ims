from django.urls import path
from django.views.decorators.cache import never_cache

from apps.labour import views

# Every hours page tells the browser never to keep a copy. On a shared tablet
# a kept copy is wrong twice over: Back shows the name list from before
# somebody added themselves, and it can show the last worker's week to the
# next person after they tapped Done. Wrapped here, once, so a page added
# later cannot forget it.
_pages = [
    ("hours/", views.start, "hours_start"),
    ("hours/new/", views.new_person, "hours_new"),
    ("hours/me/", views.me, "hours_me"),
    ("hours/me/add/", views.add, "hours_add"),
    ("hours/done/", views.done, "hours_done"),
    ("hours/pay/", views.pay_screen, "hours_pay"),
    ("hours/payments/", views.payments, "hours_payments"),
    ("hours/payments/<int:pk>/", views.payment, "hours_payment"),
    ("hours/payments/<int:pk>/undo/", views.payment_undo, "hours_payment_undo"),
    ("hours/payments/<int:pk>/<int:person_pk>/", views.pay_statement, "hours_pay_statement"),
    ("hours/report/", views.report, "hours_report"),
    ("hours/report/<int:pk>/", views.person_report, "hours_person"),
    ("hours/shifts/<int:pk>/", views.shift_fix, "hours_shift"),
    ("hours/shifts/<int:pk>/cancel/", views.shift_cancel, "hours_shift_cancel"),
    ("hours/people/<int:pk>/add-shift/", views.shift_add, "hours_shift_add"),
    ("hours/people/<int:pk>/confirm/", views.confirm, "hours_confirm"),
    ("hours/people/<int:pk>/merge/", views.merge, "hours_merge"),
]

urlpatterns = [path(route, never_cache(view), name=name) for route, view, name in _pages]
