"""URL configuration.

``/office/``  - admissions committee area (admin role only)
``/auth/``    - registration, login, two factor, MAX linkage
``/max/webhook/`` - inbound events from the MAX messenger platform
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from accounts.max_webhook import max_webhook
from accounts.views import home

admin.site.site_header = "ИМиКН БГУ — приёмная комиссия"
admin.site.site_title = "ИМиКН БГУ"
admin.site.index_title = "Служебное пространство (для разработчиков)"


def healthz(request):
    """Liveness probe used by deployment checks."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database = "ok"
    except Exception:  # noqa: BLE001
        database = "unavailable"
    return JsonResponse({"status": "ok" if database == "ok" else "degraded",
                         "database": database})


urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    path("auth/", include("accounts.urls")),
    path("max/webhook/", max_webhook, name="max_webhook"),
    path("admin/", admin.site.urls),
    path("", include("events.urls")),
]

handler400 = "accounts.error_pages.bad_request"
handler403 = "accounts.error_pages.permission_denied"
handler404 = "accounts.error_pages.page_not_found"
handler500 = "accounts.error_pages.server_error"
