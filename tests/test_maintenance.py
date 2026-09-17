import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, Chat, Message, User

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

import database  # noqa: E402
from handlers import maintenance  # noqa: E402


class MaintenanceTests(unittest.TestCase):
    def test_default_setting_is_off(self):
        settings = AsyncMock()
        settings.find_one = AsyncMock(return_value=None)

        async def read_setting():
            with patch.object(database, "_settings", settings):
                return await database.get_setting("maintenance_mode", "0")

        self.assertEqual(asyncio.run(read_setting()), "0")

    def test_normal_users_are_allowed_when_off(self):
        handler = AsyncMock(return_value="handled")
        event = SimpleNamespace(from_user=SimpleNamespace(id=2))

        async def run():
            with patch.object(maintenance, "get_setting", new=AsyncMock(return_value="0")):
                return await maintenance.MaintenanceMiddleware()(handler, event, {"key": "value"})

        self.assertEqual(asyncio.run(run()), "handled")
        handler.assert_awaited_once()

    def test_normal_message_is_blocked_when_on(self):
        handler = AsyncMock()
        event = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=2, type="private"),
            from_user=User(id=2, is_bot=False, first_name="User"),
            text="/start",
        )

        async def run():
            with (
                patch.object(maintenance, "get_setting", new=AsyncMock(return_value="1")),
                patch.object(Message, "answer", new=AsyncMock()) as answer,
            ):
                result = await maintenance.MaintenanceMiddleware()(handler, event, {})
                return result, answer

        result, answer = asyncio.run(run())
        self.assertIsNone(result)
        answer.assert_awaited_once_with(maintenance.MAINTENANCE_MESSAGE)
        handler.assert_not_awaited()

    def test_admin_bypasses_maintenance_even_if_setting_read_fails(self):
        handler = AsyncMock(return_value="admin handled")
        event = SimpleNamespace(from_user=SimpleNamespace(id=1))

        async def run():
            with patch.object(maintenance, "get_setting", side_effect=RuntimeError("db down")):
                return await maintenance.MaintenanceMiddleware()(handler, event, {})

        self.assertEqual(asyncio.run(run()), "admin handled")
        handler.assert_awaited_once()

    def test_repeated_callbacks_are_acknowledged_but_notice_is_deduplicated(self):
        handler = AsyncMock()
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=2, type="private"),
            from_user=User(id=2, is_bot=False, first_name="User"),
            text="button",
        )
        first = CallbackQuery(
            id="first",
            from_user=User(id=2, is_bot=False, first_name="User"),
            chat_instance="test",
            message=message,
            data="buy:1",
        )
        second = CallbackQuery(
            id="second",
            from_user=User(id=2, is_bot=False, first_name="User"),
            chat_instance="test",
            message=message,
            data="buy:1",
        )
        middleware = maintenance.MaintenanceMiddleware(dedupe_seconds=60)

        async def run():
            with (
                patch.object(maintenance, "get_setting", new=AsyncMock(return_value="1")),
                patch.object(CallbackQuery, "answer", new=AsyncMock()) as callback_answer,
                patch.object(Message, "answer", new=AsyncMock()) as message_answer,
            ):
                await middleware(handler, first, {})
                await middleware(handler, second, {})
                return callback_answer, message_answer

        callback_answer, message_answer = asyncio.run(run())
        self.assertEqual(callback_answer.await_count, 2)
        message_answer.assert_awaited_once_with(maintenance.MAINTENANCE_MESSAGE)
        handler.assert_not_awaited()

    def test_maintenance_update_is_atomic_and_immediate(self):
        settings = AsyncMock()
        settings.update_one = AsyncMock()

        async def update():
            with patch.object(database, "_settings", settings):
                await database.set_maintenance_mode(True)
                return await database.set_maintenance_mode(False)

        self.assertFalse(asyncio.run(update()))
        self.assertEqual(settings.update_one.await_count, 2)
        settings.update_one.assert_any_await(
            {"_id": "maintenance_mode"},
            {"$set": {"value": True}},
            upsert=True,
        )
        settings.update_one.assert_any_await(
            {"_id": "maintenance_mode"},
            {"$set": {"value": False}},
            upsert=True,
        )


if __name__ == "__main__":
    unittest.main()
