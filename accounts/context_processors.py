"""Template globals: role flags and integration availability."""
from django.conf import settings

from .services import totp as totp_service


def nav_flags(request):
    user = getattr(request, "user", None)
    authenticated = bool(user and user.is_authenticated)
    return {
        "is_admin_user": authenticated and user.is_admin,
        "is_applicant_user": authenticated and user.is_applicant,
        "two_factor_enabled": authenticated and totp_service.is_enabled(user),
        "max_enabled": settings.MAX_ENABLED,
        "max_linked": authenticated and user.max_linked,
        "two_factor_method": request.session.get("2fa_method", ""),
        "totp_period": settings.TOTP_PERIOD,
        "totp_issuer": settings.TOTP_ISSUER,
        "max_bot_username": settings.MAX_BOT_USERNAME,
    }
