from django.urls import path

from apps.stock import views

urlpatterns = [
    path("", views.home, name="home"),
    path("transfer/", views.transfer_new, name="transfer_new"),
    path("transfer/<int:pk>/", views.transfer_edit, name="transfer_edit"),
    path("transfer/<int:pk>/add/", views.transfer_add_line, name="transfer_add_line"),
    path("transfer/<int:pk>/remove/<int:line_pk>/", views.transfer_remove_line, name="transfer_remove_line"),
    path("transfer/<int:pk>/post/", views.transfer_post, name="transfer_post"),
    path("transfer/<int:pk>/done/", views.transfer_done, name="transfer_done"),
    path("items/search/", views.item_search, name="item_search"),
]
