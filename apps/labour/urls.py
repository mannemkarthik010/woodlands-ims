from django.urls import path

from apps.labour import views

urlpatterns = [
    path("hours/", views.start, name="hours_start"),
    path("hours/new/", views.new_person, name="hours_new"),
    path("hours/me/", views.me, name="hours_me"),
    path("hours/me/add/", views.add, name="hours_add"),
    path("hours/done/", views.done, name="hours_done"),
    path("hours/report/", views.report, name="hours_report"),
    path("hours/report/<int:pk>/", views.person_report, name="hours_person"),
    path("hours/people/<int:pk>/confirm/", views.confirm, name="hours_confirm"),
    path("hours/people/<int:pk>/merge/", views.merge, name="hours_merge"),
]
