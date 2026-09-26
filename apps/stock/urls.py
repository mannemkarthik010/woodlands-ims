from django.urls import path

from apps.core import views as core_views
from apps.stock import views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("transfer/", views.transfer_new, name="transfer_new"),
    path("transfer/<int:pk>/", views.transfer_edit, name="transfer_edit"),
    path("transfer/<int:pk>/add/", views.transfer_add_line, name="transfer_add_line"),
    path("transfer/<int:pk>/remove/<int:line_pk>/", views.transfer_remove_line, name="transfer_remove_line"),
    path("transfer/<int:pk>/post/", views.transfer_post, name="transfer_post"),
    path("transfer/<int:pk>/done/", views.transfer_done, name="transfer_done"),
    path("count/", views.count_home, name="count_home"),
    path("count/new/", views.count_new, name="count_new"),
    path("count/lists/", views.count_lists, name="count_lists"),
    path("count/lists/<int:pk>/move/", views.count_list_move, name="count_list_move"),
    path("count/lists/<int:pk>/stop/", views.item_stop, name="item_stop"),
    path("count/lists/<int:pk>/back/", views.item_bring_back, name="item_bring_back"),
    path("count/lists/add/", views.item_add, name="item_add"),
    path("count/<int:pk>/", views.count_sheet, name="count_sheet"),
    path("count/<int:pk>/line/<int:line_pk>/", views.count_save_line, name="count_save_line"),
    path("count/<int:pk>/review/", views.count_review, name="count_review"),
    path("count/<int:pk>/post/", views.count_post, name="count_post"),
    path("items/search/", views.item_search, name="item_search"),
    path("delivery/", views.receipt_new, name="receipt_new"),
    path("delivery/<int:pk>/", views.receipt_edit, name="receipt_edit"),
    path("delivery/<int:pk>/add/", views.receipt_add_line, name="receipt_add_line"),
    path("delivery/<int:pk>/remove/<int:line_pk>/", views.receipt_remove_line, name="receipt_remove_line"),
    path("delivery/<int:pk>/post/", views.receipt_post, name="receipt_post"),
    path("delivery/<int:pk>/done/", views.receipt_done, name="receipt_done"),
]
