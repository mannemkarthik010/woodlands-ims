from django.urls import path

from apps.sales import views

urlpatterns = [
    path("mapping/", views.mapping_queue, name="mapping_queue"),
    path("mapping/search/", views.mapping_search, name="mapping_search"),
    path("mapping/review/", views.mapping_review, name="mapping_review"),
    path("mapping/merge/", views.item_merge, name="item_merge"),
    path("mapping/convert/", views.item_convert, name="item_convert"),
    path("mapping/retire/", views.item_retire, name="item_retire"),
    path("mapping/<str:key>/apply/", views.mapping_apply, name="mapping_apply"),
    path("mapping/<str:key>/ignore/", views.mapping_ignore, name="mapping_ignore"),
    path("mapping/<str:key>/reopen/", views.mapping_reopen, name="mapping_reopen"),
]
