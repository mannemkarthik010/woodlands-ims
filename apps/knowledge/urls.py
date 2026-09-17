from django.urls import path

from apps.knowledge import views

urlpatterns = [
    path("ask/", views.ask_page, name="ask_page"),
    path("ask/answer/", views.ask, name="ask"),
]
