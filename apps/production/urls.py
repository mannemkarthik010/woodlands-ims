from django.urls import path

from apps.production import views

urlpatterns = [
    path("made/", views.made_today, name="made_today"),
    path("made/start/", views.batch_start, name="batch_start"),
    path("made/<int:pk>/", views.batch_edit, name="batch_edit"),
    path("made/<int:pk>/add/", views.batch_add_input, name="batch_add_input"),
    path("made/<int:pk>/remove/<int:input_pk>/", views.batch_remove_input, name="batch_remove_input"),
    path("made/<int:pk>/finish/", views.batch_finish, name="batch_finish"),
    path("made/<int:pk>/discard/", views.batch_discard, name="batch_discard"),
]
