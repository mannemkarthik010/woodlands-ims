from django.urls import path

from apps.labour import views

urlpatterns = [
    path("hours/", views.start, name="hours_start"),
    path("hours/new/", views.new_person, name="hours_new"),
    path("hours/me/", views.me, name="hours_me"),
    path("hours/me/add/", views.add, name="hours_add"),
    path("hours/done/", views.done, name="hours_done"),
    path("hours/pay/", views.pay_screen, name="hours_pay"),
    path("hours/payments/", views.payments, name="hours_payments"),
    path("hours/payments/<int:pk>/", views.payment, name="hours_payment"),
    path("hours/payments/<int:pk>/undo/", views.payment_undo, name="hours_payment_undo"),
    path("hours/payments/<int:pk>/<int:person_pk>/", views.pay_statement, name="hours_pay_statement"),
    path("hours/report/", views.report, name="hours_report"),
    path("hours/report/<int:pk>/", views.person_report, name="hours_person"),
    path("hours/shifts/<int:pk>/", views.shift_fix, name="hours_shift"),
    path("hours/shifts/<int:pk>/cancel/", views.shift_cancel, name="hours_shift_cancel"),
    path("hours/people/<int:pk>/add-shift/", views.shift_add, name="hours_shift_add"),
    path("hours/people/<int:pk>/confirm/", views.confirm, name="hours_confirm"),
    path("hours/people/<int:pk>/merge/", views.merge, name="hours_merge"),
]
