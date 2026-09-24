from django.urls import path

from apps.labour import views

urlpatterns = [
    path("hours/", views.names, name="hours_names"),
    path("hours/<int:pk>/pin/", views.pin, name="hours_pin"),
    path("hours/me/", views.me, name="hours_me"),
    path("hours/me/add/", views.add, name="hours_add"),
    path("hours/done/", views.done, name="hours_done"),
]
