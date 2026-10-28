"""Views for applicants (catalogue, sign up) and for the admissions committee."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.constants import ROLE_APPLICANT
from accounts.models import ApplicantProfile, User
from accounts.security import admin_required, applicant_required
from accounts.services import max_bot

from .forms import ApplicantFilterForm, AwardPointsForm, EventFilterForm, EventForm
from .models import Enrollment, Event
from .services import award_points, notify_event_change


def _paginate(request, queryset, per_page: int = 10):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


# ---------------------------------------------------------------- applicant ---
@applicant_required
def catalog(request):
    events = list(
        Event.objects.published().select_related("created_by").order_by("start_at")
    )
    my_status = {
        e.event_id: e.status
        for e in Enrollment.objects.filter(applicant=request.user).exclude(
            status=Enrollment.STATUS_CANCELLED
        )
    }
    rows = []
    for event in events:
        rows.append(
            {
                "event": event,
                "enrolled": event.pk in my_status,
                "seats_left": event.seats_left,
                "closed": not event.can_enroll()[0] and event.pk not in my_status,
            }
        )
    return render(
        request,
        "events/catalog.html",
        {
            "page_title": "Мероприятия ИМиКН",
            "rows": _paginate(request, rows, 12),
            "upcoming_count": sum(1 for r in rows if not r["event"].is_past),
        },
    )


@applicant_required
def event_detail(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    if event.status != Event.STATUS_PUBLISHED:
        messages.warning(request, "Мероприятие недоступно для просмотра.")
        return redirect("events:catalog")

    enrollment = Enrollment.objects.filter(event=event, applicant=request.user).first()
    enroll_ok, enroll_reason = event.can_enroll()
    return render(
        request,
        "events/event_detail.html",
        {
            "page_title": event.title,
            "event": event,
            "enrollment": enrollment if (enrollment and enrollment.is_active) else None,
            "seats_left": event.seats_left,
            "enroll_ok": enroll_ok,
            "enroll_reason": enroll_reason,
        },
    )


@applicant_required
@require_POST
def event_enroll(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    enrollment, message = Enrollment.enroll(
        event=event, applicant=request.user
    )
    if enrollment is None:
        messages.error(request, message)
    else:
        note = request.POST.get("note", "").strip()[:250]
        if note and enrollment.note != note:
            enrollment.note = note
            enrollment.save(update_fields=["note"])
        (messages.success if enrollment.is_active else messages.info)(request, message)
    return redirect("events:event_detail", pk=pk)


@applicant_required
@require_POST
def event_cancel(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    ok, message = Enrollment.cancel(event=event, applicant=request.user)
    (messages.success if ok else messages.error)(request, message)
    return redirect("events:event_detail", pk=pk)


@applicant_required
def my_events(request):
    enrollments = (
        Enrollment.objects.filter(applicant=request.user)
        .select_related("event")
        .order_by("-registered_at")
    )
    return render(
        request,
        "events/my_events.html",
        {"page_title": "Мои записи", "enrollments": _paginate(request, enrollments, 15)},
    )


# ------------------------------------------------------------------- admin ----
@admin_required
def admin_dashboard(request):
    stats = {
        "events_total": Event.objects.count(),
        "events_published": Event.objects.published().count(),
        "applicants": User.objects.filter(role=ROLE_APPLICANT).count(),
        "applications_submitted": ApplicantProfile.objects.filter(status="submitted").count(),
        "enrollments": Enrollment.objects.exclude(status=Enrollment.STATUS_CANCELLED).count(),
        "points_total": Enrollment.objects.aggregate(total=Sum("points"))["total"] or 0,
    }
    upcoming = Event.objects.upcoming()[:5]
    recent_logs = None
    from accounts.models import LoginLog

    recent_logs = LoginLog.objects.select_related("user")[:8]
    return render(
        request,
        "events/admin_dashboard.html",
        {
            "page_title": "Панель приёмной комиссии",
            "stats": stats,
            "upcoming": upcoming,
            "logs": recent_logs,
            "bot_enabled": max_bot.is_enabled(),
        },
    )


@admin_required
def admin_event_list(request):
    form = EventFilterForm(request.GET or None)
    events = Event.objects.annotate(
        enrolled=Count("enrollments", filter=Q(enrollments__status=Enrollment.STATUS_ENROLLED))
    ).select_related("created_by")
    if form.is_valid():
        if form.cleaned_data.get("status"):
            events = events.filter(status=form.cleaned_data["status"])
        if form.cleaned_data.get("event_type"):
            events = events.filter(event_type=form.cleaned_data["event_type"])
    return render(
        request,
        "events/admin_event_list.html",
        {"page_title": "Мероприятия — управление", "form": form,
         "events": _paginate(request, events.order_by("-start_at"), 15)},
    )


@admin_required
def admin_event_create(request):
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.save()
        messages.success(request, f"Мероприятие «{event.title}» создано.")
        if event.status == Event.STATUS_PUBLISHED:
            notify_event_change(event, created=True)
        return redirect("events:admin_event_detail", pk=event.pk)
    return render(request, "events/admin_event_form.html",
                  {"page_title": "Новое мероприятие", "form": form, "event": None})


@admin_required
def admin_event_detail(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    enrollments = (
        event.enrollments.select_related("applicant", "applicant__profile", "points_set_by")
        .order_by("-registered_at")
    )
    forms_by_id = {e.pk: AwardPointsForm(initial={"points": e.points, "note": e.points_note})
                   for e in enrollments}
    return render(
        request,
        "events/admin_event_detail.html",
        {
            "page_title": event.title,
            "event": event,
            "enrollments": _paginate(request, enrollments, 25),
            "point_forms": forms_by_id,
            "seats_left": event.seats_left,
        },
    )


@admin_required
def admin_event_update(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    was_published = event.status == Event.STATUS_PUBLISHED
    form = EventForm(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        if event.created_by_id is None:
            event.created_by = request.user
        event.save()
        messages.success(request, "Изменения сохранены.")
        now_published = event.status == Event.STATUS_PUBLISHED
        if now_published and not was_published:
            notify_event_change(event, created=True)
        elif now_published and was_published:
            notify_event_change(event, created=False)
        return redirect("events:admin_event_detail", pk=event.pk)
    return render(request, "events/admin_event_form.html",
                  {"page_title": f"Правка: {event.title}", "form": form, "event": event})


@admin_required
@require_POST
def admin_event_status(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    target = request.POST.get("status")
    if target not in dict(Event.STATUS_CHOICES):
        messages.error(request, "Неизвестный статус.")
    else:
        event.status = target
        event.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Статус изменён на «{event.get_status_display()}».")
        if target == Event.STATUS_CANCELLED:
            for enrollment in event.enrollments.exclude(status=Enrollment.STATUS_CANCELLED):
                applicant = enrollment.applicant
                if applicant.max_linked and applicant.max_notify:
                    max_bot.send_text(
                        applicant.max_user_id,
                        f"Мероприятие «{event.title}» отменено.",
                    )
    return redirect("events:admin_event_detail", pk=pk)


@admin_required
@require_POST
def admin_event_delete(request, pk: int):
    event = get_object_or_404(Event, pk=pk)
    title = event.title
    event.delete()
    messages.success(request, f"Мероприятие «{title}» удалено вместе с записями.")
    return redirect("events:admin_event_list")


@admin_required
@require_POST
def admin_award_points(request, pk: int):
    enrollment = get_object_or_404(Enrollment, pk=pk)
    form = AwardPointsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Проверьте значение баллов (0–10).")
    else:
        _, message = award_points(
            enrollment=enrollment,
            points=form.cleaned_data["points"],
            note=form.cleaned_data["note"],
            admin_user=request.user,
        )
        messages.success(request, message)
    return redirect("events:admin_event_detail", pk=enrollment.event_id)


@admin_required
@require_POST
def admin_mark_attended(request, pk: int):
    enrollment = get_object_or_404(Enrollment, pk=pk)
    enrollment.status = (
        Enrollment.STATUS_ENROLLED
        if enrollment.status == Enrollment.STATUS_ATTENDED
        else Enrollment.STATUS_ATTENDED
    )
    enrollment.save(update_fields=["status"])
    messages.success(request, f"Статус: {enrollment.get_status_display()}.")
    return redirect("events:admin_event_detail", pk=enrollment.event_id)


@admin_required
def admin_applicant_list(request):
    form = ApplicantFilterForm(request.GET or None)
    profiles = (
        ApplicantProfile.objects.select_related("user")
        .annotate(
            events_count=Count("user__enrollments", filter=Q(user__enrollments__status="enrolled")),
        )
        .order_by("last_name", "first_name", "user_id")
    )
    if form.is_valid():
        data = form.cleaned_data
        if data.get("q"):
            q = data["q"]
            profiles = profiles.filter(
                Q(last_name__icontains=q) | Q(first_name__icontains=q)
                | Q(user__email__icontains=q)
            )
        if data.get("direction"):
            profiles = profiles.filter(direction=data["direction"])
        if data.get("status"):
            profiles = profiles.filter(status=data["status"])
    return render(
        request,
        "events/admin_applicant_list.html",
        {"page_title": "Абитуриенты", "form": form,
         "profiles": _paginate(request, profiles, 20)},
    )


@admin_required
def admin_applicant_detail(request, pk: int):
    applicant = get_object_or_404(User, pk=pk, role=ROLE_APPLICANT)
    profile_obj = getattr(applicant, "profile", None)
    enrollments = (
        Enrollment.objects.filter(applicant=applicant)
        .select_related("event", "points_set_by")
        .order_by("-registered_at")
    )
    return render(
        request,
        "events/admin_applicant_detail.html",
        {
            "page_title": applicant.full_display_name,
            "applicant": applicant,
            "profile": profile_obj,
            "enrollments": enrollments,
            "now": timezone.now(),
        },
    )


@admin_required
@require_POST
def admin_applicant_status(request, pk: int):
    profile_obj = get_object_or_404(ApplicantProfile, user_id=pk)
    status = request.POST.get("status")
    valid = dict(ApplicantProfile._meta.get_field("status").choices)
    if status not in valid:
        messages.error(request, "Неизвестный статус анкеты.")
    else:
        profile_obj.status = status
        profile_obj.comment = request.POST.get("comment", profile_obj.comment)[:2000]
        profile_obj.save(update_fields=["status", "comment", "updated_at"])
        messages.success(request, f"Статус анкеты: {profile_obj.get_status_display()}.")
        if profile_obj.user.max_linked and profile_obj.user.max_notify:
            max_bot.send_text(
                profile_obj.user.max_user_id,
                f"Статус вашей анкеты изменён на «{profile_obj.get_status_display()}».",
            )
    return redirect("events:admin_applicant_detail", pk=pk)
