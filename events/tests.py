"""Tests for the event catalogue, enrolment rules and the committee tools."""
from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ApplicantProfile, User
from accounts.tests import VALID_PASSWORD, enable_totp, full_login
from events.models import Enrollment, Event
from events.services import award_points, recalc_bonus_points


def make_event(**kwargs) -> Event:
    defaults = {
        "title": "Мастер-класс",
        "event_type": "lecture",
        "start_at": timezone.now() + timedelta(days=5),
        "location": "ауд. 105",
        "capacity": 0,
        "bonus_points": 4,
        "status": Event.STATUS_PUBLISHED,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


class ApplicantEnrolmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.org", password=VALID_PASSWORD)
        self.secret = enable_totp(self.user)
        full_login(self.client, "a@example.org", VALID_PASSWORD, self.secret)

    def test_catalog_shows_only_published_events(self):
        published = make_event(title="Публичное")
        make_event(title="Черновик", status=Event.STATUS_DRAFT)
        make_event(title="Отменено", status=Event.STATUS_CANCELLED)
        response = self.client.get(reverse("events:catalog"))
        self.assertContains(response, "Публичное")
        self.assertNotContains(response, "Черновик")
        self.assertNotContains(response, "Отменено")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["event"].pk, published.pk)

    def test_enrolment_and_cancellation(self):
        event = make_event()
        response = self.client.post(reverse("events:event_enroll", args=[event.pk]),
                                    {"note": "приду с ноутом"})
        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.get(event=event, applicant=self.user)
        self.assertEqual(enrollment.status, Enrollment.STATUS_ENROLLED)
        self.assertEqual(enrollment.note, "приду с ноутом")

        self.client.post(reverse("events:event_cancel", args=[event.pk]))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, Enrollment.STATUS_CANCELLED)
        self.assertIsNotNone(enrollment.cancelled_at)

    def test_capacity_is_enforced(self):
        event = make_event(title="Мастер-класс", capacity=1)
        other = User.objects.create_user(email="b@example.org", password=VALID_PASSWORD)
        Enrollment.objects.create(event=event, applicant=other)
        response = self.client.post(reverse("events:event_enroll", args=[event.pk]), follow=True)
        self.assertContains(response, "Свободных мест нет")
        self.assertFalse(Enrollment.objects.filter(event=event, applicant=self.user,
                                                  status=Enrollment.STATUS_ENROLLED).exists())

    def test_cannot_enroll_to_past_or_draft(self):
        past = make_event(title="Прошло", start_at=timezone.now() - timedelta(days=1))
        draft = make_event(title="Черновик", status=Event.STATUS_DRAFT)
        for event in (past, draft):
            response = self.client.post(reverse("events:event_enroll", args=[event.pk]),
                                        follow=True)
            self.assertFalse(Enrollment.objects.filter(event=event, applicant=self.user,
                                                       status=Enrollment.STATUS_ENROLLED).exists())

    def test_double_enrolment_is_idempotent(self):
        event = make_event()
        self.client.post(reverse("events:event_enroll", args=[event.pk]))
        self.client.post(reverse("events:event_enroll", args=[event.pk]))
        self.assertEqual(Enrollment.objects.filter(event=event, applicant=self.user).count(), 1)


class CommitteeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email="office@example.org",
                                                  password=VALID_PASSWORD)
        self.secret = enable_totp(self.admin)
        self.applicant = User.objects.create_user(email="a@example.org", password=VALID_PASSWORD)
        ApplicantProfile.objects.create(user=self.applicant, last_name="Абитуриентов",
                                        first_name="Абитуриент", direction="02.03.01")
        full_login(self.client, "office@example.org", VALID_PASSWORD, self.secret)

    def test_admin_creates_and_edits_event(self):
        response = self.client.post(reverse("events:admin_event_create"), {
            "title": "Олимпиада", "event_type": "olympiad", "description": "описание",
            "start_at": (timezone.now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
            "end_at": "", "location": "ауд. 214", "capacity": 30, "bonus_points": 7,
            "status": Event.STATUS_PUBLISHED,
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title="Олимпиада")
        self.assertEqual(event.created_by, self.admin)
        self.assertEqual(event.status, Event.STATUS_PUBLISHED)

        response = self.client.post(reverse("events:admin_event_update", args=[event.pk]), {
            "title": "Олимпиада (перенесена)", "event_type": "olympiad",
            "description": "новое", "start_at": event.start_at.strftime("%Y-%m-%dT%H:%M"),
            "end_at": "", "location": "ауд. 215", "capacity": 20, "bonus_points": 8,
            "status": Event.STATUS_PUBLISHED,
        })
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.location, "ауд. 215")

    def test_end_before_start_is_rejected(self):
        response = self.client.post(reverse("events:admin_event_create"), {
            "title": "Сломанное", "event_type": "lecture", "description": "",
            "start_at": (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
            "end_at": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "location": "", "capacity": 0, "bonus_points": 0, "status": "draft",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.filter(title="Сломанное").exists())

    def test_status_change_and_delete(self):
        event = make_event()
        self.client.post(reverse("events:admin_event_status", args=[event.pk]),
                         {"status": "cancelled"})
        event.refresh_from_db()
        self.assertEqual(event.status, Event.STATUS_CANCELLED)
        self.client.post(reverse("events:admin_event_delete", args=[event.pk]))
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_awarding_points_updates_bonus_and_notifies_max(self):
        event = make_event()
        enrollment = Enrollment.objects.create(event=event, applicant=self.applicant)
        with mock.patch("accounts.services.max_bot.is_enabled", return_value=True), \
             mock.patch("accounts.services.max_bot.send_text", return_value=True) as send:
            self.applicant.max_user_id = 777
            self.applicant.save(update_fields=["max_user_id"])
            award_points(enrollment=enrollment, points=7, note="диплом",
                         admin_user=self.admin)
        enrollment.refresh_from_db()
        self.applicant.refresh_from_db()
        self.assertEqual(enrollment.points, 7)
        self.assertEqual(enrollment.status, Enrollment.STATUS_ATTENDED)
        self.assertEqual(enrollment.points_set_by, self.admin)
        self.assertEqual(self.applicant.profile.bonus_points, 7)
        send.assert_called_once()

        # Clearing the points must recalculate the profile total as well.
        award_points(enrollment=enrollment, points=None, note="", admin_user=self.admin)
        self.applicant.refresh_from_db()
        self.assertEqual(recalc_bonus_points(self.applicant), 0)

    def test_points_are_validated(self):
        event = make_event()
        enrollment = Enrollment.objects.create(event=event, applicant=self.applicant)
        response = self.client.post(reverse("events:admin_award_points", args=[enrollment.pk]),
                                    {"points": 42, "note": ""}, follow=True)
        self.assertContains(response, "Проверьте значение баллов")
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.points)

    def test_applicant_list_and_detail(self):
        response = self.client.get(reverse("events:admin_applicant_list"))
        self.assertContains(response, "a@example.org")
        response = self.client.get(
            reverse("events:admin_applicant_detail", args=[self.applicant.pk]))
        self.assertContains(response, self.applicant.email)

    def test_applicant_status_change_notifies_user(self):
        with mock.patch("accounts.services.max_bot.send_text", return_value=True) as send:
            self.applicant.max_user_id = 555
            self.applicant.save(update_fields=["max_user_id"])
            response = self.client.post(
                reverse("events:admin_applicant_status", args=[self.applicant.pk]),
                {"status": "invited", "comment": "Ждём документы"}, follow=True)
        self.assertContains(response, "Приглашён")
        send.assert_called_once()
