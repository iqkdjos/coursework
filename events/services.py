"""Points, bonus recalculation and MAX notifications."""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from accounts.services import max_bot

from .models import Enrollment

logger = logging.getLogger("events")


def recalc_bonus_points(applicant) -> int:
    """Keep ``profile.bonus_points`` in sync with the awarded enrollment points."""
    total = sum(
        value or 0
        for value in Enrollment.objects.filter(applicant=applicant)
        .exclude(status=Enrollment.STATUS_CANCELLED)
        .values_list("points", flat=True)
    )
    profile = getattr(applicant, "profile", None)
    if profile and profile.bonus_points != total:
        profile.bonus_points = total
        profile.save(update_fields=["bonus_points", "updated_at"])
    return total


@transaction.atomic
def award_points(*, enrollment: Enrollment, points: int | None, note: str,
                 admin_user) -> tuple[bool, str]:
    """Set or clear the points of one enrolment and notify the applicant."""
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    enrollment.points = points
    enrollment.points_note = (note or "").strip()[:250]
    enrollment.points_set_by = admin_user
    enrollment.points_set_at = timezone.now()
    if points is not None and enrollment.status == Enrollment.STATUS_ENROLLED:
        # Awarding points implies the applicant actually took part.
        enrollment.status = Enrollment.STATUS_ATTENDED
    enrollment.save(update_fields=["points", "points_note", "points_set_by", "points_set_at",
                                   "status"])

    total = recalc_bonus_points(enrollment.applicant)

    if enrollment.applicant.max_linked and enrollment.applicant.max_notify:
        if points is None:
            text = (
                f"Баллы за мероприятие «{enrollment.event.title}» сняты.\n"
                f"Суммарные дополнительные баллы: {total}."
            )
        else:
            text = (
                f"Вам начислено {points} балл(ов) за мероприятие "
                f"«{enrollment.event.title}».\n"
                f"Основание: {enrollment.points_note or 'участие в мероприятии'}.\n"
                f"Суммарные дополнительные баллы: {total}."
            )
        max_bot.send_text(enrollment.applicant.max_user_id, text)

    logger.info(
        "points=%s for enrollment=%s by %s", points, enrollment.pk,
        getattr(admin_user, "email", "system"),
    )
    return True, "Баллы сохранены."


def notify_event_change(event, *, created: bool = True) -> int:
    """Tell every enrolled applicant about a new/changed/cancelled event."""
    if not max_bot.is_enabled():
        return 0
    recipients = (
        Enrollment.objects.filter(event=event)
        .exclude(status=Enrollment.STATUS_CANCELLED)
        .select_related("applicant")
    )
    sent = 0
    for enrollment in recipients:
        applicant = enrollment.applicant
        if not (applicant.max_linked and applicant.max_notify):
            continue
        when = timezone.localtime(event.start_at).strftime("%d.%m.%Y %H:%M")
        if created:
            text = (
                f"Новое мероприятие ИМиКН: «{event.title}».\n"
                f"Когда: {when}.\nМесто: {event.location or 'уточняется'}.\n"
                f"Доп. баллы за участие: до {event.bonus_points}."
            )
        else:
            text = (
                f"Изменения в мероприятии «{event.title}».\n"
                f"Новые данные — когда: {when}, место: {event.location or 'уточняется'}."
            )
        if max_bot.send_text(applicant.max_user_id, text):
            sent += 1
    return sent
