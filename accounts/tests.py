"""Tests for authentication, the two-factor flow and role based access."""
from __future__ import annotations

from unittest import mock

import pyotp
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.constants import ROLE_ADMIN, ROLE_APPLICANT
from accounts.models import ApplicantProfile, LoginLog, RecoveryCode, TotpDevice, User
from accounts.services import totp as totp_service

VALID_PASSWORD = "Str0ng-Passw0rd-2026"


def enable_totp(user) -> str:
    """Attach a confirmed TOTP device with a known secret."""
    secret = pyotp.random_base32(32)
    TotpDevice.objects.create(user=user, secret=secret, confirmed=True)
    return secret


def totp_code(secret: str) -> str:
    return pyotp.TOTP(secret, digits=settings.TOTP_DIGITS,
                      interval=settings.TOTP_PERIOD).now()


def full_login(client, email: str, password: str, secret: str) -> None:
    response = client.post(reverse("accounts:login"),
                           {"email": email, "password": password})
    assert response.status_code == 302, response.redirect_chain
    response = client.post(reverse("accounts:two_factor"),
                           {"action": "totp", "code": totp_code(secret)}, follow=True)
    assert response.status_code == 200


class RegistrationTests(TestCase):
    def test_creates_applicant_with_hashed_password_and_profile(self):
        response = self.client.post(reverse("accounts:register"), {
            "email": "new.applicant@example.org",
            "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD,
            "last_name": "Иванов",
            "first_name": "Иван",
            "middle_name": "",
            "birth_date": "2008-05-01",
            "direction": "02.03.01",
            "company": "",
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="new.applicant@example.org")
        self.assertEqual(user.role, ROLE_APPLICANT)
        self.assertFalse(user.is_staff)
        self.assertTrue(ApplicantProfile.objects.filter(user=user).exists())
        # The password must never be stored in plain text.
        self.assertNotEqual(user.password, VALID_PASSWORD)
        self.assertTrue(user.password.startswith("argon2"),
                        f"unexpected hasher: {user.password[:20]}")

    def test_weak_password_is_rejected(self):
        response = self.client.post(reverse("accounts:register"), {
            "email": "weak@example.org", "password1": "1234567890",
            "password2": "1234567890", "last_name": "Петров", "first_name": "Пётр",
            "middle_name": "", "birth_date": "2008-01-01", "direction": "01.03.01",
            "company": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.org").exists())

    def test_honeypot_field_blocks_bots(self):
        response = self.client.post(reverse("accounts:register"), {
            "email": "bot@example.org", "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD, "last_name": "Bot", "first_name": "Bot",
            "middle_name": "", "birth_date": "2008-01-01", "direction": "01.03.01",
            "company": "spam-inc",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="bot@example.org").exists())

    def test_duplicate_email_rejected(self):
        User.objects.create_user(email="dup@example.org", password=VALID_PASSWORD)
        response = self.client.post(reverse("accounts:register"), {
            "email": "dup@example.org", "password1": VALID_PASSWORD,
            "password2": VALID_PASSWORD, "last_name": "Д", "first_name": "Д",
            "middle_name": "", "birth_date": "2008-01-01", "direction": "01.03.01",
            "company": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email="dup@example.org").count(), 1)


class TwoFactorLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="applicant@example.org",
                                             password=VALID_PASSWORD)
        self.secret = enable_totp(self.user)

    def test_password_alone_does_not_grant_access(self):
        response = self.client.post(reverse("accounts:login"),
                                    {"email": "applicant@example.org",
                                     "password": VALID_PASSWORD})
        self.assertRedirects(response, reverse("accounts:two_factor"),
                             fetch_redirect_response=False)
        # The session is not authenticated yet, so the catalogue is unreachable.
        response = self.client.get(reverse("events:catalog"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_valid_totp_completes_login(self):
        full_login(self.client, "applicant@example.org", VALID_PASSWORD, self.secret)
        self.assertTrue(self.client.session.get("2fa_verified"))
        response = self.client.get(reverse("events:catalog"))
        self.assertEqual(response.status_code, 200)

    def test_wrong_totp_is_rejected_and_logged(self):
        self.client.post(reverse("accounts:login"), {"email": "applicant@example.org",
                                                     "password": VALID_PASSWORD})
        response = self.client.post(reverse("accounts:two_factor"),
                                    {"action": "totp", "code": "000000"}, follow=True)
        self.assertNotIn("2fa_verified", self.client.session)
        self.assertTrue(LoginLog.objects.filter(
            user=self.user, outcome=LoginLog.OUTCOME_2FA_FAIL).exists())

    def test_totp_code_cannot_be_replayed(self):
        code = totp_code(self.secret)
        full_login(self.client, "applicant@example.org", VALID_PASSWORD, self.secret)
        self.client.get(reverse("accounts:logout"))
        # Same code again in a new session must not be accepted.
        self.client.post(reverse("accounts:login"), {"email": "applicant@example.org",
                                                     "password": VALID_PASSWORD})
        self.client.post(reverse("accounts:two_factor"), {"action": "totp", "code": code})
        self.assertNotIn("2fa_verified", self.client.session)

    def test_recovery_code_is_single_use(self):
        codes = totp_service.issue_recovery_codes(self.user)
        self.client.post(reverse("accounts:login"), {"email": "applicant@example.org",
                                                     "password": VALID_PASSWORD})
        response = self.client.post(reverse("accounts:two_factor"),
                                    {"action": "recovery", "code": codes[0]}, follow=True)
        self.assertTrue(self.client.session.get("2fa_verified"))
        self.assertEqual(RecoveryCode.objects.filter(user=self.user,
                                                     used_at__isnull=False).count(), 1)
        self.client.get(reverse("accounts:logout"))
        self.client.post(reverse("accounts:login"), {"email": "applicant@example.org",
                                                     "password": VALID_PASSWORD})
        self.client.post(reverse("accounts:two_factor"),
                         {"action": "recovery", "code": codes[0]})
        self.assertNotIn("2fa_verified", self.client.session)

    def test_wrong_password_locks_after_threshold(self):
        for _ in range(security_threshold := 11):
            self.client.post(reverse("accounts:login"),
                             {"email": "applicant@example.org", "password": "wrong-password"})
        response = self.client.post(reverse("accounts:login"),
                                    {"email": "applicant@example.org",
                                     "password": VALID_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("2fa_pending_user", self.client.session)
        self.assertTrue(LoginLog.objects.filter(
            outcome=LoginLog.OUTCOME_LOCKED).exists())

    def test_unknown_user_and_bad_password_share_one_message(self):
        for email in ["ghost@example.org", "applicant@example.org"]:
            response = self.client.post(reverse("accounts:login"),
                                        {"email": email, "password": "wrong-password"})
            self.assertContains(response, "Неверная почта или пароль")

    def test_password_alone_cannot_replace_the_totp_secret(self):
        """Regression: knowing only the password must not allow re-enrolling 2FA."""
        self.client.post(reverse("accounts:login"), {"email": "applicant@example.org",
                                                     "password": VALID_PASSWORD})
        response = self.client.get(reverse("accounts:totp_setup"))
        self.assertRedirects(response, reverse("accounts:two_factor"),
                             fetch_redirect_response=False)
        self.assertNotContains(response, "secret-box", status_code=302)
        # The genuine secret of the device stays untouched.
        self.assertEqual(TotpDevice.objects.filter(user=self.user, confirmed=True).count(), 1)

    def test_pending_session_without_two_factor_can_enrol(self):
        """A brand new account with no device yet must still get a QR code."""
        fresh = User.objects.create_user(email="fresh@example.org", password=VALID_PASSWORD)
        self.client.post(reverse("accounts:login"), {"email": "fresh@example.org",
                                                     "password": VALID_PASSWORD})
        response = self.client.get(reverse("accounts:totp_setup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data:image/png;base64,")

    def test_force_login_without_second_factor_is_blocked_by_middleware(self):
        """Even a session created outside the login view must pass the 2FA guard."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("events:catalog"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.assertTrue(LoginLog.objects.filter(
            user=self.user, outcome=LoginLog.OUTCOME_2FA_SKIP).exists())


class RoleAccessTests(TestCase):
    def setUp(self):
        self.applicant = User.objects.create_user(email="applicant@example.org",
                                                 password=VALID_PASSWORD)
        self.admin = User.objects.create_superuser(email="office@example.org",
                                                  password=VALID_PASSWORD)
        self.applicant_secret = enable_totp(self.applicant)
        self.admin_secret = enable_totp(self.admin)

    def test_applicant_cannot_open_committee_area(self):
        full_login(self.client, "applicant@example.org", VALID_PASSWORD, self.applicant_secret)
        response = self.client.get(reverse("events:admin_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_open_applicant_cabinet(self):
        full_login(self.client, "office@example.org", VALID_PASSWORD, self.admin_secret)
        response = self.client.get(reverse("events:catalog"))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse("events:admin_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("events:catalog"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response.url)

    def test_django_admin_login_cannot_bypass_two_factor(self):
        """``/admin/login/`` knows nothing about TOTP, so the guard must stop it."""
        self.client.post("/admin/login/", {"username": "office@example.org",
                                          "password": VALID_PASSWORD})
        response = self.client.get("/admin/", follow=True)
        self.assertIn(reverse("accounts:login"), str(response.redirect_chain))
        self.assertNotContains(response, "django-admin")
        self.assertTrue(LoginLog.objects.filter(
            user=self.admin, outcome=LoginLog.OUTCOME_2FA_SKIP).exists())


class AnonymousAccessTests(TestCase):
    """Anonymous visitors must be redirected, never hit a server error."""

    def test_get_pages_redirect_to_login(self):
        for path in ["/auth/max/", "/auth/recovery-codes/", "/auth/settings/log/",
                     "/auth/settings/", "/auth/profile/", "/events/", "/events/my/",
                     "/office/", "/office/events/", "/office/applicants/"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn(reverse("accounts:login"), response.url, path)

    def test_post_endpoints_redirect_to_login(self):
        for path in ["/auth/max/start/", "/auth/max/unlink/", "/auth/max/test/",
                     "/auth/max/notify/", "/auth/recovery-codes/refresh/",
                     "/auth/2fa/disable/", "/auth/profile/submit/"]:
            response = self.client.post(path, {})
            self.assertEqual(response.status_code, 302, path)
            self.assertIn(reverse("accounts:login"), response.url, path)

    def test_committee_pages_are_not_reachable_for_anonymous_post(self):
        response = self.client.post("/office/enrollments/1/points/", {"points": 5})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)


@override_settings(MAX_BOT_TOKEN="test-token", MAX_WEBHOOK_SECRET="test-secret")
class MaxWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="applicant@example.org",
                                             password=VALID_PASSWORD)
        self.link_code = self.user.issue_max_link_code()

    def _post(self, payload, secret="test-secret"):
        headers = {"HTTP_X_MAX_BOT_API_SECRET": secret} if secret else {}
        with mock.patch("accounts.services.max_bot.send_text", return_value=True) as send:
            response = self.client.post(reverse("max_webhook"), data=payload,
                                        content_type="application/json", **headers)
        return response, send

    def test_request_without_secret_is_rejected(self):
        response, _ = self._post({"update_type": "message_created"}, secret="")
        self.assertEqual(response.status_code, 403)

    def test_link_code_binds_the_chat(self):
        payload = {
            "update_type": "message_created",
            "sender": {"user_id": 9911, "is_bot": False},
            "message": {"text": f"LINK {self.link_code}"},
        }
        response, send = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.max_user_id, 9911)
        self.assertIsNone(self.user.max_link_code)
        send.assert_called_once()

    def test_expired_code_does_not_bind(self):
        from django.utils import timezone

        self.user.max_link_expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.user.save(update_fields=["max_link_expires_at"])
        payload = {
            "update_type": "message_created",
            "sender": {"user_id": 9912, "is_bot": False},
            "message": {"text": f"LINK {self.link_code}"},
        }
        response, _ = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.max_user_id)

    def test_bot_started_sends_instructions(self):
        payload = {"update_type": "bot_started", "user": {"user_id": 42, "is_bot": False}}
        response, send = self._post(payload)
        self.assertEqual(response.status_code, 200)
        send.assert_called_once()
