"""Events of the institute and the applicant enrolment table.

``events_event`` is the "events" table from the specification, ``events_enrollment``
keeps who signed up for what and how many bonus points were awarded.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from accounts.constants import ENROLLMENT_STATUS, EVENT_TYPES


class EventQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Event.STATUS_PUBLISHED)

    def upcoming(self):
        return self.published().filter(start_at__gte=timezone.now()).order_by("start_at")


class Event(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Черновик (не видно абитуриентам)"),
        (STATUS_PUBLISHED, "Опубликовано"),
        (STATUS_CANCELLED, "Отменено"),
    ]

    title = models.CharField("Название", max_length=200)
    event_type = models.CharField("Тип", max_length=20, choices=EVENT_TYPES, default="open_day")
    description = models.TextField("Описание", blank=True)
    start_at = models.DateTimeField("Начало")
    end_at = models.DateTimeField("Окончание", null=True, blank=True)
    location = models.CharField("Место проведения", max_length=200, blank=True)
    capacity = models.PositiveSmallIntegerField(
        "Мест",
        default=0,
        help_text="0 — без ограничения. Учитываются активные записи.",
        validators=[MaxValueValidator(10000)],
    )
    bonus_points = models.PositiveSmallIntegerField(
        "Баллы за участие",
        default=0,
        validators=[MaxValueValidator(10)],
        help_text="Сколько дополнительных баллов начисляет комиссия за посещение.",
    )
    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_events",
        verbose_name="Создал",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Изменено", auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        db_table = "events_event"
        verbose_name = "Мероприятие"
        verbose_name_plural = "Мероприятия"
        ordering = ["-start_at"]
        indexes = [models.Index(fields=["status", "start_at"], name="event_status_start_idx")]

    def __str__(self) -> str:
        return f"{self.title} ({self.start_at:%d.%m.%Y %H:%M})"

    def clean(self):
        if self.end_at and self.start_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "Окончание должно быть позже начала."})

    @property
    def is_past(self) -> bool:
        return self.start_at < timezone.now()

    @property
    def enrollments_count(self) -> int:
        return self.enrollments.filter(status=Enrollment.STATUS_ENROLLED).count()

    @property
    def seats_left(self) -> int | None:
        if not self.capacity:
            return None
        return max(self.capacity - self.enrollments_count, 0)

    @property
    def is_full(self) -> bool:
        left = self.seats_left
        return left is not None and left == 0

    def can_enroll(self) -> tuple[bool, str]:
        if self.status != self.STATUS_PUBLISHED:
            return False, "Запись закрыта: мероприятие не опубликовано."
        if self.is_past:
            return False, "Мероприятие уже началось."
        if self.is_full:
            return False, "Свободных мест нет."
        return True, ""


class Enrollment(models.Model):
    STATUS_ENROLLED = "enrolled"
    STATUS_ATTENDED = "attended"
    STATUS_CANCELLED = "cancelled"

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="enrollments", verbose_name="Мероприятие"
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Абитуриент",
    )
    status = models.CharField(
        "Статус", max_length=16, choices=ENROLLMENT_STATUS, default=STATUS_ENROLLED
    )
    registered_at = models.DateTimeField("Записан", auto_now_add=True)
    cancelled_at = models.DateTimeField("Отмена", null=True, blank=True)
    note = models.CharField("Комментарий абитуриента", max_length=250, blank=True)

    # Points awarded by the admissions committee.
    points = models.PositiveSmallIntegerField(
        "Баллы",
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    points_note = models.CharField("Основание", max_length=250, blank=True)
    points_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="points_awarded",
        verbose_name="Баллы выставил",
    )
    points_set_at = models.DateTimeField("Баллы выставлены", null=True, blank=True)

    class Meta:
        db_table = "events_enrollment"
        verbose_name = "Запись на мероприятие"
        verbose_name_plural = "Записи на мероприятия"
        ordering = ["-registered_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "applicant"], name="uniq_event_applicant"),
        ]

    def __str__(self) -> str:
        return f"{self.applicant} → {self.event}"

    @property
    def is_active(self) -> bool:
        return self.status != self.STATUS_CANCELLED

    @classmethod
    @transaction.atomic
    def enroll(cls, *, event: Event, applicant) -> tuple["Enrollment | None", str]:
        """Idempotent sign up honouring the capacity limit."""
        existing = cls.objects.select_for_update().filter(event=event, applicant=applicant).first()
        if existing and existing.is_active:
            return existing, "Вы уже записаны на это мероприятие."

        ok, reason = event.can_enroll()
        if not ok:
            return None, reason

        if event.capacity:
            taken = cls.objects.filter(event=event, status=cls.STATUS_ENROLLED).count()
            if taken >= event.capacity:
                return None, "Свободных мест нет."

        if existing:
            existing.status = cls.STATUS_ENROLLED
            existing.cancelled_at = None
            existing.save(update_fields=["status", "cancelled_at"])
            return existing, "Запись восстановлена."

        enrollment = cls.objects.create(event=event, applicant=applicant)
        return enrollment, "Вы записаны на мероприятие."

    @classmethod
    @transaction.atomic
    def cancel(cls, *, event: Event, applicant) -> tuple[bool, str]:
        enrollment = cls.objects.filter(event=event, applicant=applicant).first()
        if not enrollment or not enrollment.is_active:
            return False, "Активной записи не найдено."
        if enrollment.status == cls.STATUS_ATTENDED:
            return False, "Посещённое мероприятие отменить нельзя."
        if event.is_past:
            return False, "Отменить запись после начала мероприятия нельзя."
        enrollment.status = cls.STATUS_CANCELLED
        enrollment.cancelled_at = timezone.now()
        enrollment.save(update_fields=["status", "cancelled_at"])
        return True, "Запись отменена."
