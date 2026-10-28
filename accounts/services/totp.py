"""TOTP second factor (RFC 6238).

Compatible with Yandex.Key, Google Authenticator, FreeOTP and any other
authenticator that follows the standard. The secret is generated on the server,
shown once as a QR code / base32 string and then only used for verification.
"""
from __future__ import annotations

import base64
import io
import secrets
import time
from dataclasses import dataclass

import pyotp
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from ..models import RecoveryCode, TotpDevice


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    provisioning_uri: str
    qr_data_uri: str


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret, digits=settings.TOTP_DIGITS, interval=settings.TOTP_PERIOD)


def new_secret() -> str:
    # 20 random bytes -> 32 base32 characters, the usual strength for TOTP.
    return pyotp.random_base32(32)


def qr_data_uri(provisioning_uri: str) -> str:
    import qrcode

    img = qrcode.make(provisioning_uri, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def start_setup(user) -> TotpSetup:
    """Create (or replace) an unconfirmed device and return its enrolment data."""
    secret = new_secret()
    TotpDevice.objects.update_or_create(
        user=user,
        defaults={"secret": secret, "confirmed": False, "confirmed_at": None, "last_step": 0},
    )
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name=settings.TOTP_ISSUER
    )
    return TotpSetup(secret=secret, provisioning_uri=uri, qr_data_uri=qr_data_uri(uri))


def confirm_setup(user, code: str) -> tuple[bool, list[str]]:
    """Verify the first code, mark the device active and issue backup codes."""
    device = getattr(user, "totp", None)
    if not device:
        return False, []
    step = _verify(device, code)
    if step is None:
        return False, []

    device.confirmed = True
    device.confirmed_at = timezone.now()
    device.last_step = step
    device.save(update_fields=["confirmed", "confirmed_at", "last_step"])

    # Old backup codes are invalidated, new plaintext codes are shown once.
    RecoveryCode.objects.filter(user=user, used_at__isnull=True).delete()
    plaintext = issue_recovery_codes(user)
    return True, plaintext


def _verify(device: TotpDevice, code: str) -> int | None:
    """Return the accepted time step, or None. Guards against code reuse."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != settings.TOTP_DIGITS:
        return None
    try:
        totp = _totp(device.secret)
    except Exception:  # noqa: BLE001 - corrupted secret must not break the login flow
        return None

    now = time.time()
    period = settings.TOTP_PERIOD
    for drift in range(-settings.TOTP_VALID_WINDOW, settings.TOTP_VALID_WINDOW + 1):
        moment = now + drift * period
        if secrets.compare_digest(totp.at(moment), code):
            step = int(moment // period)
            if step <= device.last_step:
                return None  # replay of an already used step
            return step
    return None


def verify_code(user, code: str) -> bool:
    device = getattr(user, "totp", None)
    if not device or not device.confirmed:
        return False
    step = _verify(device, code)
    if step is None:
        return False
    TotpDevice.objects.filter(pk=device.pk).update(last_step=step)
    return True


def issue_recovery_codes(user, count: int | None = None) -> list[str]:
    count = count or settings.TOTP_RECOVERY_CODES
    codes = [
        "-".join((secrets.token_hex(2), secrets.token_hex(2), secrets.token_hex(2)))
        for _ in range(count)
    ]
    RecoveryCode.objects.bulk_create(
        [RecoveryCode(user=user, code_hash=make_password(c)) for c in codes]
    )
    return codes


def check_recovery_code(user, code: str) -> bool:
    """Consume a single-use backup code if it matches."""
    code = (code or "").strip().lower().replace(" ", "")
    if not code:
        return False
    for record in RecoveryCode.objects.filter(user=user, used_at__isnull=True):
        if check_password(code, record.code_hash):
            from django.utils import timezone

            record.used_at = timezone.now()
            record.save(update_fields=["used_at"])
            return True
    return False


def is_enabled(user) -> bool:
    device = getattr(user, "totp", None)
    return bool(device and device.confirmed)


def revoke(user) -> None:
    TotpDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()
