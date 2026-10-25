"""Phone notifications via ntfy (free app, no account) and/or Telegram."""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)


def notify(title: str, message: str, url: str | None = None, priority: str = "default") -> list[str]:
    """Send to every configured channel. Returns the channels that accepted it."""
    sent: list[str] = []
    if settings.ntfy_topic:
        try:
            headers = {"Title": title, "Priority": priority, "Tags": "chart_with_upwards_trend"}
            if url:
                headers["Click"] = url
                headers["Actions"] = f"view, Open, {url}"
            r = httpx.post(f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}",
                           content=message.encode(), headers=headers, timeout=10)
            r.raise_for_status()
            sent.append("ntfy")
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy failed: %s", e)
    if settings.telegram_bot_token and settings.telegram_chat_id:
        try:
            text = f"*{title}*\n{message}" + (f"\n{url}" if url else "")
            r = httpx.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "Markdown",
                      "disable_web_page_preview": True},
                timeout=10,
            )
            r.raise_for_status()
            sent.append("telegram")
        except Exception as e:  # noqa: BLE001
            log.warning("telegram failed: %s", e)
    if not sent:
        log.info("NOTIFY (no channel configured): %s - %s %s", title, message, url or "")
    return sent
