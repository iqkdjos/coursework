"""Access control helpers: role guards, 2FA session state and login throttling."""
from __future__ import annotations

import functools

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .constants import ROLE_ADMIN, ROLE_APPLICANT
from .models import LoginLog

# Session keys used by the two-factor flow. ``SESSION_PENDING_USER`` marks a
# password-authenticated but not yet verified identity; ``SESSION_2FA_USER`` is
# written only after the second factor succeeded.
SESSION_2FA_DONE = "2fa_verified"
SESSION_2FA_USER = "2fa_verified_for"
SESSION_2FA_AT = "2fa_verified_at"
SESSION_2FA_METHOD = "2fa_method"
SESSION_PENDING_USER = "2fa_pending_user"
SESSION_PENDING_AT = "2fa_pending_at"

# Requests that stay reachable without a fully verified session.
PUBLIC_PREFIXES = (
    "/auth/login/",
    "/auth/logout/",
    "/auth/2fa/",
    "/auth/two-factor/",
    "/static/",
    "/max/webhook/",
    "/favicon.ico",
    "/robots.txt",
    "/healthz",
)

# Failed password attempts per e-mail inside the window before a cooldown starts.
PASSWORD_MAX_FAILURES = 10
PASSWORD_WINDOW_SECONDS = 600


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.META.get("REMOTE_ADDR")


def log_login(*, request=None, user=None, identifier: str = "", outcome: str) -> LoginLog:
    return LoginLog.objects.create(
        user=user,
        identifier=identifier or (user.email if user else ""),
        outcome=outcome,
        ip_address=client_ip(request) if request else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:256] if request else ""),
    )


def password_failures_recent(identifier: str) -> int:
    since = timezone.now() - timezone.timedelta(seconds=PASSWORD_WINDOW_SECONDS)
    return LoginLog.objects.filter(
        identifier__iexact=identifier,
        outcome__in=[LoginLog.OUTCOME_BAD_PASSWORD, LoginLog.OUTCOME_UNKNOWN_USER],
        created_at__gte=since,
    ).count()


def password_lock_seconds_left(identifier: str) -> int:
    """Seconds to wait before password auth is allowed again for this e-mail."""
    last = (
        LoginLog.objects.filter(
            identifier__iexact=identifier,
            outcome__in=[LoginLog.OUTCOME_BAD_PASSWORD, LoginLog.OUTCOME_UNKNOWN_USER],
        )
        .order_by("-created_at")
        .first()
    )
    if password_failures_recent(identifier) < PASSWORD_MAX_FAILURES or not last:
        return 0
    opens_at = last.created_at + timezone.timedelta(seconds=PASSWORD_WINDOW_SECONDS)
    return max(int((opens_at - timezone.now()).total_seconds()), 0)


def twofa_failures_left(user) -> int:
    """Second-factor attempts left for the current lockout window."""
    since = timezone.now() - timezone.timedelta(seconds=settings.TWOFA_LOCKOUT_SECONDS)
    used = LoginLog.objects.filter(
        user=user, outcome=LoginLog.OUTCOME_2FA_FAIL, created_at__gte=since
    ).count()
    return max(settings.TWOFA_MAX_ATTEMPTS - used, 0)


# --- session state -----------------------------------------------------------
def mark_two_factor_passed(request, user, method: str) -> None:
    request.session[SESSION_2FA_DONE] = True
    request.session[SESSION_2FA_METHOD] = method
    request.session[SESSION_2FA_AT] = timezone.now().isoformat()
    request.session[SESSION_2FA_USER] = user.pk


def two_factor_verified(request) -> bool:
    if not request.user.is_authenticated:
        return False
    return bool(request.session.get(SESSION_2FA_DONE)) and request.session.get(
        SESSION_2FA_USER
    ) == request.user.pk


def require_two_factor(request) -> bool:
    """Middleware guard: an authenticated session must have passed the 2FA step."""
    if not request.user.is_authenticated:
        return True
    if two_factor_verified(request):
        return True
    # Attempt to reach the site with a password-only session -> kill it.
    log_login(request=request, user=request.user, identifier=request.user.email,
              outcome=LoginLog.OUTCOME_2FA_SKIP)
    logout(request)
    return False


# --- decorators --------------------------------------------------------------
def role_required(*roles):
    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"{reverse('accounts:login')}?next={request.path}")
            if request.user.role not in roles:
                raise PermissionDenied("Раздел доступен только роли "
                                       f"{'/'.join(roles)}.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


admin_required = role_required(ROLE_ADMIN)
applicant_required = role_required(ROLE_APPLICANT)


def staff_dashboard_url(user) -> str:
    return reverse("events:admin_dashboard") if user.role == ROLE_ADMIN else reverse("events:catalog")
