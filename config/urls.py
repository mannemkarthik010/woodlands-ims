from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from apps.core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", core_views.healthz, name="healthz"),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # A new owner's one-time link: choose a password, and be signed in (apps.core.invites).
    path(
        "welcome/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="welcome.html", post_reset_login=True, success_url=reverse_lazy("home")
        ),
        name="welcome",
    ),
    path("", include("apps.knowledge.urls")),
    path("", include("apps.labour.urls")),
    path("", include("apps.production.urls")),
    path("", include("apps.sales.urls")),
    path("", include("apps.stock.urls")),
]
