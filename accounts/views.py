"""Views: registration, password login, the 2FA challenge, MAX linkage."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import security
from .forms import (
    ApplicantProfileForm,
    ChangePasswordForm,
    LoginForm,
    MaxCodeForm,
    RecoveryCodeForm,
    RegistrationForm,
    TotpCodeForm,
)
from .models import ApplicantProfile, LoginLog, OneTimeCode, User
from .services import max_bot
from .services import totp as totp_service

logger = logging.getLogger("accounts")

PENDING_TTL_SECONDS = 600


# --- pending (not yet fully authenticated) helper ----------------------------
def _remember_pending(request, user) -> None:
    request.session[security.SESSION_PENDING_USER] = user.pk
    request.session[security.SESSION_PENDING_AT] = timezone.now().timestamp()


def _clear_pending(request) -> None:
    request.session.pop(security.SESSION_PENDING_USER, None)
    request.session.pop(security.SESSION_PENDING_AT, None)


def _pending_user(request) -> User | None:
    pk = request.session.get(security.SESSION_PENDING_USER)
    stamp = request.session.get(security.SESSION_PENDING_AT)
    if not pk or not stamp:
        return None
    if timezone.now().timestamp() - float(stamp) > PENDING_TTL_SECONDS:
        _clear_pending(request)
        return None
    return User.objects.filter(pk=pk, is_active=True).first()


def _finish_login(request, user, method: str) -> redirect:
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    _clear_pending(request)
    security.mark_two_factor_passed(request, user, method)
    security.log_login(request=request, user=user, identifier=user.email,
                       outcome=LoginLog.OUTCOME_SUCCESS)
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
        next_url = ""
    messages.success(request, f"Вход выполнен (второй фактор: {method}).")
    return redirect(next_url or security.staff_dashboard_url(user))


# --- registration ------------------------------------------------------------
def register(request):
    if request.user.is_authenticated:
        return redirect("events:catalog")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _remember_pending(request, user)
        messages.success(
            request,
            "Аккаунт создан. Чтобы включить двухфакторную защиту, отсканируйте QR-код "
            "в приложении «Яндекс Ключ» и подтвердите настройку.",
        )
        return redirect("accounts:totp_setup")

    return render(request, "accounts/register.html", {"form": form, "page_title": "Регистрация"})


# --- step 1: password --------------------------------------------------------
def user_login(request):
    if request.user.is_authenticated:
        return redirect("events:catalog")

    form = LoginForm(request.POST or None)
    lock_seconds = 0

    if request.method == "POST":
        email = form.data.get("email", "").strip().lower() if form.data else ""
        lock_seconds = security.password_lock_seconds_left(email)
        if lock_seconds:
            form.add_error(
                None,
                f"Слишком много неудачных попыток. Повторите через {lock_seconds // 60 + 1} мин.",
            )
            security.log_login(request=request, identifier=email,
                               outcome=LoginLog.OUTCOME_LOCKED)
        elif form.is_valid():
            email = form.cleaned_data["email"]
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                security.log_login(request=request, identifier=email,
                                   outcome=LoginLog.OUTCOME_UNKNOWN_USER)
                form.add_error(None, "Неверная почта или пароль.")
            elif not user.is_active:
                security.log_login(request=request, user=user, identifier=email,
                                   outcome=LoginLog.OUTCOME_INACTIVE)
                form.add_error(None, "Учётная запись отключена. Обратитесь в приёмную комиссию.")
            else:
                authenticated = authenticate(request, username=email,
                                             password=form.cleaned_data["password"])
                if authenticated is None:
                    security.log_login(request=request, user=user, identifier=email,
                                       outcome=LoginLog.OUTCOME_BAD_PASSWORD)
                    remaining = security.password_lock_seconds_left(email)
                    note = f" Осталось попыток до блокировки: " \
                           f"{max(security.PASSWORD_MAX_FAILURES - security.password_failures_recent(email), 0)}."
                    form.add_error(None, "Неверная почта или пароль." + note)
                else:
                    _remember_pending(request, authenticated)
                    if totp_service.is_enabled(authenticated):
                        return redirect("accounts:two_factor")
                    messages.info(request, "Двухфакторная защита ещё не настроена — "
                                           "это обязательно.")
                    return redirect("accounts:totp_setup")

    return render(
        request,
        "accounts/login.html",
        {"form": form, "page_title": "Вход", "lock_seconds": lock_seconds,
         "next": request.GET.get("next", "")},
    )


# --- step 2: second factor ---------------------------------------------------
def two_factor(request):
    user = _pending_user(request)
    if user is None:
        return redirect("accounts:login")

    if not totp_service.is_enabled(user):
        return redirect("accounts:totp_setup")

    failures_left = security.twofa_failures_left(user)
    max_code_allowed = (
        settings.MAX_AS_SECOND_FACTOR and settings.MAX_ENABLED and user.max_linked
    )
    context = {
        "user_email": user.email,
        "totp_form": TotpCodeForm(user=user),
        "max_form": MaxCodeForm(),
        "recovery_form": RecoveryCodeForm(user=user),
        "max_allowed": max_code_allowed,
        "failures_left": failures_left,
        "page_title": "Подтверждение входа",
        "next": request.GET.get("next", request.POST.get("next", "")),
    }

    if request.method != "POST":
        return render(request, "accounts/two_factor.html", context)

    action = request.POST.get("action", "totp")

    if action == "max_send":
        if not max_code_allowed:
            messages.error(request, "Код в MAX недоступен: чат не привязан.")
        else:
            record, code = OneTimeCode.issue(
                user, OneTimeCode.PURPOSE_SECOND_FACTOR, settings.MAX_CODE_TTL_SECONDS
            )
            sent = max_bot.send_text(
                user.max_user_id,
                f"Код подтверждения входа в систему абитуриента БГУ ИМиКН: {code}\n"
                f"Годен {settings.MAX_CODE_TTL_SECONDS // 60} мин. Никому не сообщайте его.",
            )
            if sent:
                messages.success(request, "Код отправлен в чат MAX.")
            else:
                record.consumed_at = timezone.now()
                record.save(update_fields=["consumed_at"])
                messages.error(request, "MAX не ответил. Используйте код из «Яндекс Ключ».")
        return redirect("accounts:two_factor")

    if failures_left <= 0:
        security.log_login(request=request, user=user, identifier=user.email,
                           outcome=LoginLog.OUTCOME_LOCKED)
        messages.error(request, "Превышено число попыток. Подождите несколько минут.")
        return redirect("accounts:two_factor")

    if action == "max":
        form = MaxCodeForm(request.POST)
        if form.is_valid():
            record = (
                OneTimeCode.objects.filter(
                    user=user, purpose=OneTimeCode.PURPOSE_SECOND_FACTOR, consumed_at__isnull=True
                )
                .order_by("-created_at")
                .first()
            )
            if record and record.verify(form.cleaned_data["code"]):
                return _finish_login(request, user, "код в MAX")
        security.log_login(request=request, user=user, identifier=user.email,
                           outcome=LoginLog.OUTCOME_2FA_FAIL)
        messages.error(request, "Код из MAX не подошёл или истёк.")
        return redirect("accounts:two_factor")

    if action == "recovery":
        form = RecoveryCodeForm(request.POST, user=user)
        if form.is_valid():
            return _finish_login(request, user, "резервный код")
        security.log_login(request=request, user=user, identifier=user.email,
                           outcome=LoginLog.OUTCOME_2FA_FAIL)
        messages.error(request, "Резервный код не подошёл.")
        return redirect("accounts:two_factor")

    # default: TOTP from Yandex.Key
    form = TotpCodeForm(request.POST, user=user)
    if form.is_valid() and totp_service.verify_code(user, form.cleaned_data["code"]):
        return _finish_login(request, user, "Яндекс Ключ (TOTP)")
    security.log_login(request=request, user=user, identifier=user.email,
                       outcome=LoginLog.OUTCOME_2FA_FAIL)
    messages.error(request, "Код не подошёл. Проверьте время на телефоне (оно должно "
                            "синхронизироваться автоматически).")
    return redirect("accounts:two_factor")


# --- 2FA enrolment -----------------------------------------------------------
def totp_setup(request):
    """Show the QR code for Yandex.Key, then accept the first code.

    Security note: the page must never hand out a fresh secret to a session that
    only knows the password. Otherwise a stolen password alone would be enough to
    re-enrol 2FA on the attacker's phone and pass the second factor. Re-enrolment
    is allowed exclusively for a session that has already passed 2FA.
    """
    user = request.user if request.user.is_authenticated else _pending_user(request)
    if user is None:
        return redirect("accounts:login")

    already_enabled = totp_service.is_enabled(user)
    if already_enabled and not security.two_factor_verified(request):
        messages.error(request, "Двухфакторная защита уже включена — новый секрет не выдаётся. "
                                "Для перенастройки сначала подтвердите вход.")
        return redirect("accounts:two_factor")

    setup = request.session.get("totp_setup")
    if not setup or setup.get("for") != user.pk:
        data = totp_service.start_setup(user)
        setup = {"for": user.pk, "uri": data.provisioning_uri, "qr": data.qr_data_uri,
                 "secret": data.secret}
        request.session["totp_setup"] = setup

    form = TotpCodeForm(request.POST or None, user=user)
    error = None
    if request.method == "POST" and form.is_valid():
        ok, codes = totp_service.confirm_setup(user, form.cleaned_data["code"])
        if ok:
            request.session.pop("totp_setup", None)
            request.session["recovery_codes"] = codes
            if request.user.is_authenticated:
                messages.success(request, "Двухфакторная защита включена.")
                return redirect("accounts:recovery_show")
            return redirect("accounts:two_factor_done_setup")
        error = "Код не подошёл. Подождите 30 секунд и введите новый."

    return render(
        request,
        "accounts/totp_setup.html",
        {"form": form, "setup": setup, "error": error, "user_email": user.email,
         "page_title": "Настройка 2FA"},
    )


def two_factor_done_setup(request):
    """Right after enrolment during registration: show backup codes once."""
    user = _pending_user(request)
    if user is None:
        return redirect("accounts:login")
    codes = request.session.get("recovery_codes") or []
    return render(request, "accounts/recovery_codes.html",
                  {"codes": codes, "next_step": "login", "page_title": "Резервные коды"})


def recovery_show(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    codes = request.session.pop("recovery_codes", None) or []
    return render(request, "accounts/recovery_codes.html",
                  {"codes": codes, "next_step": "profile", "page_title": "Резервные коды"})


@login_required
@require_POST
def recovery_regenerate(request):
    codes = totp_service.issue_recovery_codes(request.user)
    request.session["recovery_codes"] = codes
    messages.success(request, "Сгенерированы новые резервные коды; старые отменены.")
    return redirect("accounts:recovery_show")


@login_required
@require_POST
def totp_disable(request):
    password = request.POST.get("password", "")
    if not request.user.check_password(password):
        messages.error(request, "Текущий пароль не подошёл — 2FA не отключён.")
        return redirect("accounts:profile")
    totp_service.revoke(request.user)
    security.log_login(request=request, user=request.user, identifier=request.user.email,
                       outcome=LoginLog.OUTCOME_2FA_FAIL)
    messages.warning(request, "Двухфакторная защита отключена. При следующем входе её "
                              "нужно будет настроить заново.")
    return redirect("accounts:profile")


# --- MAX messenger -----------------------------------------------------------
@login_required
def max_account(request):
    user = request.user
    link_code = request.session.get("max_link_code")
    if link_code and request.session.get("max_link_code_for") != user.pk:
        link_code = None
    return render(
        request,
        "accounts/max_account.html",
        {
            "page_title": "MAX-бот",
            "bot_enabled": settings.MAX_ENABLED,
            "linked": user.max_linked,
            "max_user_id": user.max_user_id,
            "max_notify": user.max_notify,
            "link_code": link_code,
            "bot_username": settings.MAX_BOT_USERNAME,
            "as_second_factor": settings.MAX_AS_SECOND_FACTOR,
        },
    )


@login_required
@require_POST
def max_link_start(request):
    if not settings.MAX_ENABLED:
        messages.error(request, "Бот MAX не настроен (MAX_BOT_TOKEN не задан).")
        return redirect("accounts:max_account")
    code = request.user.issue_max_link_code()
    request.session["max_link_code"] = code
    request.session["max_link_code_for"] = request.user.pk
    messages.success(request, "Код привязки создан, он действует 15 минут.")
    return redirect("accounts:max_account")


@login_required
@require_POST
def max_unlink(request):
    request.user.clear_max_link()
    request.session.pop("max_link_code", None)
    messages.success(request, "Чат MAX отвязан.")
    return redirect("accounts:max_account")


@login_required
@require_POST
def max_test_message(request):
    user = request.user
    if not user.max_linked:
        messages.error(request, "Сначала привяжите чат в MAX.")
        return redirect("accounts:max_account")
    ok = max_bot.send_text(user.max_user_id,
                           "Тестовое уведомление системы абитуриента БГУ ИМиКН — "
                           "связь работает.")
    messages.success(request, "Сообщение отправлено." if ok
                     else "MAX не ответил на запрос. Попробуйте позже.")
    return redirect("accounts:max_account")


@login_required
@require_POST
def max_notify_toggle(request):
    user = request.user
    user.max_notify = not user.max_notify
    user.save(update_fields=["max_notify"])
    messages.success(request, f"Уведомления в MAX: {'включены' if user.max_notify else 'выключены'}.")
    return redirect("accounts:max_account")


# --- profile / misc ----------------------------------------------------------
def home(request):
    """Entry point: send everybody to the screen belonging to their role."""
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if not security.two_factor_verified(request):
        return redirect("accounts:login")
    return redirect(security.staff_dashboard_url(request.user))


@security.applicant_required
def profile(request):
    """Applicant self service: questionnaire, points, security settings."""
    profile_obj, _ = ApplicantProfile.objects.get_or_create(user=request.user)
    form = ApplicantProfileForm(request.POST or None, instance=profile_obj)

    if request.method == "POST" and "profile" in request.POST and form.is_valid():
        form.save()
        messages.success(request, "Анкета сохранена.")
        return redirect("accounts:profile")

    enrollments = (
        request.user.enrollments.select_related("event")
        .order_by("-event__start_at")[:50]
    )
    return render(
        request,
        "accounts/profile.html",
        {
            "page_title": "Личный кабинет",
            "form": form,
            "profile": profile_obj,
            "enrollments": enrollments,
        },
    )


@security.applicant_required
@require_POST
def submit_application(request):
    profile_obj = get_object_or_404(ApplicantProfile, user=request.user)
    if not profile_obj.is_complete:
        messages.error(request, "Анкета не заполнена: укажите ФИО, дату рождения "
                                "и направление подготовки.")
    else:
        profile_obj.status = "submitted"
        profile_obj.save(update_fields=["status", "updated_at"])
        messages.success(request, "Анкета подана в приёмную комиссию.")
    return redirect("accounts:profile")


@login_required
def account_settings(request):
    form = ChangePasswordForm(request.user, request.POST or None)
    if request.method == "POST" and "password" in request.POST and form.is_valid():
        form.save()
        messages.success(request, "Пароль изменён, сессия сохранена.")
        return redirect("accounts:account_settings")

    logs = LoginLog.objects.filter(user=request.user)[:50]
    return render(
        request,
        "accounts/account_settings.html",
        {
            "page_title": "Безопасность аккаунта",
            "form": form,
            "logs": logs,
            "totp_enabled": totp_service.is_enabled(request.user),
        },
    )


@login_required
def login_log(request):
    logs = LoginLog.objects.filter(user=request.user)[:200]
    return render(request, "accounts/login_log.html",
                  {"logs": logs, "page_title": "Журнал входов"})


def logout_view(request):
    if request.user.is_authenticated:
        security.log_login(request=request, user=request.user, identifier=request.user.email,
                           outcome=LoginLog.OUTCOME_LOGOUT)
    auth_logout(request)
    messages.info(request, "Вы вышли из системы.")
    return redirect("accounts:login")
