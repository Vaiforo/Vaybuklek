"""Безопасная отправка Telegram-уведомлений с fallback в общий чат."""

from __future__ import annotations

from aiogram import Bot

from ..logging_setup import get_logger

log = get_logger("dirizher.bot.notifications")


async def send_with_fallback(
    bot: Bot,
    target_chat_id: int | None,
    text: str,
    *,
    fallback_chat_id: int | None = None,
) -> bool:
    """Отправить сообщение в target; если личка недоступна — попробовать fallback.

    Возвращает True, если сообщение было отправлено хотя бы куда-то.
    """
    if target_chat_id is None and fallback_chat_id is None:
        return False

    if target_chat_id is not None:
        try:
            await bot.send_message(target_chat_id, text)
            return True
        except Exception as e:  # noqa: BLE001 — Telegram может вернуть Forbidden/BadRequest
            log.warning("Не удалось отправить в чат %s: %s", target_chat_id, e)

    if fallback_chat_id is not None and fallback_chat_id != target_chat_id:
        try:
            await bot.send_message(fallback_chat_id, text)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось отправить fallback в чат %s: %s", fallback_chat_id, e)

    return False
