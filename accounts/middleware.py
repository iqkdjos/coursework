"""Session hardening middleware.

Guarantees that no view of the application is reachable with a password-only
session: the second factor must have been verified during this very session.
It also blocks the Django admin sign-in bypass (``/admin/login/`` does not know
anything about our TOTP) and marks private pages as non-cacheable.
"""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .security import (
    PUBLIC_PREFIXES,
    log_login,
    require_two_factor,
    two_factor_verified,
)
from .models import LoginLog


class SessionSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if not path.startswith(PUBLIC_PREFIXES):
            if request.user.is_authenticated and not two_factor_verified(request):
                # Password-only session: terminate it and start the 2FA flow again.
                require_two_factor(request)
                messages.error(
                    request,
                    "Требуется подтверждение второго фактора. Войдите заново.",
                )
                return redirect(f"{reverse('accounts:login')}?next={path}")

            if path.startswith("/admin/") and request.user.is_authenticated:
                if not (request.user.is_staff and request.user.is_admin):
                    log_login(request=request, user=request.user,
                              identifier=request.user.email, outcome=LoginLog.OUTCOME_2FA_SKIP)
                    return redirect("events:catalog")

        response = self.get_response(request)

        if request.user.is_authenticated and not path.startswith("/static/"):
            # Applicant data must not be kept in shared/proxy caches.
            response.setdefault("Cache-Control", "no-store")
            response.setdefault("Vary", "Cookie")
        return response
