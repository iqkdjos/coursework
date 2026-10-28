"""Inbound webhook from the MAX messenger platform.

MAX posts an ``Update`` object to this endpoint (see
https://dev.max.ru/docs-api/methods/POST/subscriptions). The endpoint:

* rejects requests whose ``X-Max-Bot-Api-Secret`` header does not match;
* answers ``200 OK`` to everything else, because any other code or a timeout
  counts as a failed delivery and MAX unsubscribes the bot after 8 hours;
* binds a MAX chat to an applicant account when the user sends the one-time
  link code shown in their personal cabinet.
"""
from __future__ import annotations

import json
import logging
import secrets

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import User
from .services import max_bot

logger = logging.getLogger("accounts")

WELCOME_TEXT = (
    "Система абитуриента БГУ, Институт математики, физики и компьютерных наук.\n\n"
    "Чтобы привязать чат к личному кабинету:\n"
    "1. Откройте раздел «MAX-бот» в личном кабинете и нажмите «Получить код».\n"
    "2. Отправьте сюда сообщение вида: LINK XXXXXXXXXXXX\n\n"
    "После привязки бот будет присылать уведомления о баллах и мероприятиях."
)


def _reply(user_id: int | None, text: str) -> None:
    if user_id:
        max_bot.send_text(user_id, text)


def _handle_update(update: max_bot.IncomingUpdate) -> None:
    if update.is_from_bot or not update.user_id:
        return

    if update.update_type == "bot_started":
        _reply(update.user_id, WELCOME_TEXT)
        return

    if update.update_type != "message_created":
        return

    text = update.text.strip()
    lowered = text.lower()

    if lowered in {"/start", "/help", "помощь", "старт"}:
        _reply(update.user_id, WELCOME_TEXT)
        return

    token = text.split()[-1].upper() if text.split() else ""
    if lowered.startswith(("link", "/link", "привязка")) or len(token) == 12:
        candidate = User.objects.filter(max_link_code=token).first()
        if candidate and candidate.max_link_expires_at and candidate.max_link_expires_at > timezone.now():
            already = (
                User.objects.filter(max_user_id=update.user_id)
                .exclude(pk=candidate.pk)
                .exists()
            )
            if already:
                _reply(update.user_id, "Этот чат уже привязан к другой учётной записи.")
                return
            candidate.max_user_id = update.user_id
            candidate.max_linked_at = timezone.now()
            candidate.max_link_code = None
            candidate.max_link_expires_at = None
            candidate.save(update_fields=["max_user_id", "max_linked_at", "max_link_code",
                                          "max_link_expires_at"])
            logger.info("MAX chat %s linked to user %s", update.user_id, candidate.email)
            _reply(
                update.user_id,
                f"Готово! Чат привязан к аккаунту {candidate.email}. "
                "Теперь сюда будут приходить уведомления о баллах и мероприятиях.",
            )
            return
        _reply(update.user_id, "Код привязки не найден или истёк. Обновите его в личном кабинете.")
        return

    _reply(update.user_id, "Я не умею отвечать на свободные сообщения. "
                           "Напишите /help, чтобы увидеть инструкцию.")


@csrf_exempt
@require_POST
def max_webhook(request):
    if settings.MAX_WEBHOOK_SECRET:
        provided = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not secrets.compare_digest(provided, settings.MAX_WEBHOOK_SECRET):
            logger.warning("MAX webhook rejected: bad secret from %s", request.META.get("REMOTE_ADDR"))
            return JsonResponse({"error": "bad secret"}, status=403)

    if not request.body:
        return JsonResponse({"ok": True})

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("MAX webhook: body is not valid JSON (%s bytes)", len(request.body))
        return JsonResponse({"ok": True})

    # MAX delivers a single Update object; a list is accepted just in case.
    updates = payload if isinstance(payload, list) else payload.get("updates", [payload])
    for item in updates:
        if not isinstance(item, dict):
            continue
        try:
            _handle_update(max_bot.parse_update(item))
        except Exception:  # noqa: BLE001 - never let a handler error break delivery
            logger.exception("MAX webhook: failed to handle update")

    return JsonResponse({"ok": True})
