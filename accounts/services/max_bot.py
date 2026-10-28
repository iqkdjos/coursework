"""MAX messenger bot client (https://dev.max.ru/docs-api).

Endpoints used:

* ``GET  /me``                - token health check
* ``POST /messages?user_id=`` - send a text message to a private chat
* ``POST /subscriptions``     - register the webhook that receives updates

Authentication is a plain ``Authorization: <token>`` header (MAX does not put
the token into the URL). All calls fail softly: without a configured token the
bot is simply disabled and the rest of the system keeps working.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger("accounts")

REQUEST_TIMEOUT = 10
WEBHOOK_UPDATE_TYPES = ["message_created", "bot_started"]


class MaxBotError(RuntimeError):
    pass


def is_enabled() -> bool:
    return bool(settings.MAX_BOT_TOKEN)


def _request(method: str, path: str, *, params: dict | None = None, payload: dict | None = None):
    if not is_enabled():
        raise MaxBotError("MAX_BOT_TOKEN не задан — бот отключён")
    url = f"{settings.MAX_API_BASE.rstrip('/')}{path}"
    try:
        response = requests.request(
            method,
            url,
            params=params,
            json=payload,
            headers={"Authorization": settings.MAX_BOT_TOKEN, "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:  # network / DNS / TLS problems
        logger.warning("MAX API %(method)s %(path)s network error: %(exc)s",
                       {"method": method, "path": path, "exc": exc})
        raise MaxBotError(f"Сеть недоступна: {exc}") from exc

    if response.status_code >= 400:
        logger.warning("MAX API %s %s -> HTTP %s %s", method, path, response.status_code,
                       response.text[:300])
        raise MaxBotError(f"MAX API вернул HTTP {response.status_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise MaxBotError("MAX API вернул не JSON") from exc


def get_me() -> dict:
    """Bot profile; also serves as a connectivity self-test."""
    return _request("GET", "/me")


def send_text(user_id: int, text: str) -> bool:
    """Send a message into the private chat with ``user_id``."""
    if not user_id:
        return False
    try:
        _request("POST", "/messages", params={"user_id": user_id}, payload={"text": text})
    except MaxBotError:
        return False
    return True


def subscribe_webhook(public_url: str) -> dict:
    """Point the MAX platform to our webhook endpoint."""
    payload = {
        "url": public_url,
        "update_types": WEBHOOK_UPDATE_TYPES,
    }
    if settings.MAX_WEBHOOK_SECRET:
        payload["secret"] = settings.MAX_WEBHOOK_SECRET
    return _request("POST", "/subscriptions", payload=payload)


@dataclass(frozen=True)
class IncomingUpdate:
    update_type: str
    user_id: int | None
    text: str
    raw: dict

    @property
    def is_from_bot(self) -> bool:
        return bool(self.raw.get("is_bot"))


def parse_update(payload: dict) -> IncomingUpdate:
    """Normalise the MAX ``Update`` object.

    Depending on the event type the author lives either in ``user`` (chat
    events such as ``bot_started``) or in ``sender`` (``message_created``).
    """
    payload = payload if isinstance(payload, dict) else {}
    actor = payload.get("user") or payload.get("sender") or {}
    user_id = actor.get("user_id") if isinstance(actor, dict) else None
    message = payload.get("message") or {}
    text = ""
    if isinstance(message, dict):
        text = (message.get("text") or "").strip()
    elif isinstance(message, str):
        text = message.strip()
    return IncomingUpdate(
        update_type=payload.get("update_type", ""),
        user_id=int(user_id) if user_id else None,
        text=text,
        raw=payload,
    )
