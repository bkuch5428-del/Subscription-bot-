"""Central maintenance-mode access gate for normal users."""

import asyncio
import time

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from database import get_setting

MAINTENANCE_MESSAGE = (
    "🔧 <b>Bot Under Maintenance</b>\n\n"
    "We are currently performing maintenance.\n"
    "Please try again later.\n\n"
    "Thank you for your patience. 🙏"
)


def is_admin_user(user_id: int) -> bool:
    """Use the existing configured admin list for all maintenance decisions."""
    return user_id in ADMIN_IDS


async def is_maintenance_mode() -> bool:
    """Read the live setting; fail closed if the settings database is unavailable."""
    try:
        value = await get_setting("maintenance_mode", False)
        return value is True or str(value).lower() in {"1", "true", "on"}
    except Exception:
        return True


async def should_block_for_maintenance(user_id: int) -> bool:
    """Admins always bypass maintenance; uncertain state blocks normal users."""
    if is_admin_user(user_id):
        return False
    return await is_maintenance_mode()


class MaintenanceMiddleware(BaseMiddleware):
    """Stop normal-user updates before they reach any user handler."""

    def __init__(self, dedupe_seconds: float = 2.0) -> None:
        self._dedupe_seconds = dedupe_seconds
        self._last_notice: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def _should_send_notice(self, user_id: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            previous = self._last_notice.get(user_id, 0.0)
            self._last_notice[user_id] = now
            return now - previous >= self._dedupe_seconds

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or not await should_block_for_maintenance(user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer()
            if await self._should_send_notice(user.id) and event.message:
                await event.message.answer(MAINTENANCE_MESSAGE)
        elif isinstance(event, Message) and await self._should_send_notice(user.id):
            await event.answer(MAINTENANCE_MESSAGE)
        return None