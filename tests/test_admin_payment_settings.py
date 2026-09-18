import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

import database as db  # noqa: E402
import handlers.admin as admin  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402


class FakeAggregate:
    def __init__(self, docs, *, match_start=None, match_end=None):
        self.docs = docs
        self.match_start = match_start
        self.match_end = match_end

    def __aiter__(self):
        async def _gen():
            for doc in self.docs:
                if self.match_start is not None and self.match_end is not None:
                    ts = doc.get("subscription_start")
                    if ts is None:
                        continue
                    try:
                        dt = db.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if not (self.match_start <= dt <= self.match_end):
                        continue
                yield doc
        return _gen()


class AdminPaymentSettingsTests(unittest.TestCase):
    def test_panel_opens_and_displays_current_provider(self):
        target = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        async def setting(key, default=""):
            return {
                "active_payment_provider": "manual",
                "manual_payment_qr": "qr-file",
                "manual_upi_text": "merchant@example",
            }.get(key, default)

        async def render():
            with patch.object(admin, "get_setting", new=setting):
                await admin._payment_settings_panel(target)

        asyncio.run(render())
        text, kwargs = target.answer.await_args.args[0], target.answer.await_args.kwargs
        self.assertIn("Active Payment Provider: <b>Manual Payment</b>", text)
        self.assertIn("Manual QR: ✅ set", text)
        self.assertIn("Manual UPI text: ✅ set", text)
        self.assertIsNotNone(kwargs["reply_markup"])

    def test_missing_or_invalid_provider_falls_back_to_famapp(self):
        for stored_value in (None, "invalid"):
            target = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

            async def setting(key, default=""):
                if key == "active_payment_provider":
                    return stored_value
                return default

            async def render():
                with patch.object(admin, "get_setting", new=setting):
                    await admin._payment_settings_panel(target)

            asyncio.run(render())
            text = target.answer.await_args.args[0]
            self.assertIn("Active Payment Provider: <b>FamApp</b>", text)

    def test_selecting_provider_saves_exactly_one_active_value(self):
        for callback_data, expected in (
            ("admin_pp_famapp", "famapp"),
            ("admin_pp_manual", "manual"),
            ("admin_pp_vc_gateway", "vc_gateway"),
        ):
            call = SimpleNamespace(
                data=callback_data,
                from_user=SimpleNamespace(id=1),
                answer=AsyncMock(),
            )
            with (
                patch.object(admin, "set_setting", new=AsyncMock()) as save,
                patch.object(admin, "_payment_provider_settings_panel", new=AsyncMock()),
            ):
                asyncio.run(admin.cb_toggle_payment_provider(call))
            save.assert_awaited_once_with("active_payment_provider", expected)

    def test_unauthorized_provider_selection_is_rejected(self):
        call = SimpleNamespace(
            data="admin_pp_vc_gateway",
            from_user=SimpleNamespace(id=999),
            answer=AsyncMock(),
        )
        with (
            patch.object(admin, "set_setting", new=AsyncMock()) as save,
            patch.object(admin, "_is_admin", return_value=False),
        ):
            asyncio.run(admin.cb_toggle_payment_provider(call))
        save.assert_not_awaited()
        call.answer.assert_awaited_once_with("⛔ Unauthorised.", show_alert=True)

    def test_payment_stats_zero_when_no_approved_orders(self):
        with patch.object(db, "_orders") as orders:
            orders.aggregate.return_value = FakeAggregate([])
            result = asyncio.run(db.get_payment_stats_last_24h())
        self.assertEqual(result["providers"]["famapp"], {"payments": 0, "amount": 0.0})
        self.assertEqual(result["providers"]["manual"], {"payments": 0, "amount": 0.0})
        self.assertEqual(result["providers"]["vc_gateway"], {"payments": 0, "amount": 0.0})
        self.assertEqual(result["total_payments"], 0)
        self.assertEqual(result["total_amount"], 0.0)

    def test_payment_stats_provider_wise_amounts_and_total(self):
        docs = [
            {"_id": "famapp", "payments": 1, "amount": 199.0},
            {"_id": "manual", "payments": 1, "amount": 149.0},
            {"_id": "vc_gateway", "payments": 2, "amount": 518.0},
        ]
        with patch.object(db, "_orders") as orders:
            orders.aggregate.return_value = FakeAggregate(docs)
            result = asyncio.run(db.get_payment_stats_last_24h())
        self.assertEqual(result["providers"]["famapp"]["payments"], 1)
        self.assertEqual(result["providers"]["famapp"]["amount"], 199.0)
        self.assertEqual(result["providers"]["manual"]["payments"], 1)
        self.assertEqual(result["providers"]["manual"]["amount"], 149.0)
        self.assertEqual(result["providers"]["vc_gateway"]["payments"], 2)
        self.assertEqual(result["providers"]["vc_gateway"]["amount"], 518.0)
        self.assertEqual(result["total_payments"], 4)
        self.assertEqual(result["total_amount"], 866.0)

    def test_payment_stats_ignores_pending_failed_expired_and_old_orders(self):
        docs = [
            {"_id": "famapp", "payments": 1, "amount": 99.0},
        ]
        with patch.object(db, "_orders") as orders:
            orders.aggregate.return_value = FakeAggregate(docs)
            result = asyncio.run(db.get_payment_stats_last_24h())
        self.assertEqual(result["total_payments"], 1)
        self.assertEqual(result["providers"]["famapp"]["payments"], 1)
        self.assertEqual(result["providers"]["manual"]["payments"], 0)
        self.assertEqual(result["providers"]["vc_gateway"]["payments"], 0)
        self.assertEqual(result["total_amount"], 99.0)

    def test_payment_stats_callback_requires_admin(self):
        call = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with (
            patch.object(admin, "_is_admin", return_value=False),
            patch.object(admin, "get_payment_stats_last_24h", new=AsyncMock()) as stats,
        ):
            asyncio.run(admin.cb_payment_stats_24h(call))
        stats.assert_not_awaited()
        call.answer.assert_awaited_once_with("⛔ Unauthorised.", show_alert=True)

    def test_payment_stats_repeated_render_skips_unchanged_message(self):
        stats = {
            "providers": {
                "famapp": {"payments": 1, "amount": 99.0},
                "manual": {"payments": 0, "amount": 0.0},
                "vc_gateway": {"payments": 0, "amount": 0.0},
            },
            "total_payments": 1,
            "total_amount": 99.0,
        }
        keyboard = admin.admin_panel_keyboard()
        message = SimpleNamespace(text=None, reply_markup=None, edit_text=AsyncMock())
        with patch.object(admin, "get_payment_stats_last_24h", new=AsyncMock(return_value=stats)):
            asyncio.run(admin._render_payment_stats_message(message, 1))
        message.text = message.edit_text.await_args.args[0]
        message.reply_markup = keyboard
        message.edit_text.reset_mock()

        with (
            patch.object(admin, "get_payment_stats_last_24h", new=AsyncMock(return_value=stats)),
            patch.object(admin, "admin_panel_keyboard", return_value=keyboard),
        ):
            asyncio.run(admin._render_payment_stats_message(message, 1))
        message.edit_text.assert_not_awaited()

    def test_payment_stats_callback_edits_panel_message_and_answers(self):
        stats = {
            "providers": {
                "famapp": {"payments": 2, "amount": 198.0},
                "manual": {"payments": 0, "amount": 0.0},
                "vc_gateway": {"payments": 0, "amount": 0.0},
            },
            "total_payments": 2,
            "total_amount": 198.0,
        }
        call = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            message=SimpleNamespace(text="🛠 <b>ADMIN PANEL</b>", reply_markup=None, edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        def discard_refresh_coroutine(coroutine):
            coroutine.close()
            return SimpleNamespace(done=lambda: True)

        with (
            patch.object(admin, "_is_admin", return_value=True),
            patch.object(admin, "get_payment_stats_last_24h", new=AsyncMock(return_value=stats)),
            patch.object(admin.asyncio, "create_task", side_effect=discard_refresh_coroutine),
        ):
            asyncio.run(admin.cb_payment_stats_24h(call))
        call.answer.assert_awaited_once_with()
        call.message.edit_text.assert_awaited_once()
        self.assertIn("PAYMENT STATS — LAST 24 HOURS", call.message.edit_text.await_args.args[0])

    def test_payment_stats_ignores_message_not_modified_error(self):
        message = SimpleNamespace(
            text=None,
            reply_markup=None,
            edit_text=AsyncMock(side_effect=TelegramBadRequest(method="editMessageText", message="Bad Request: message is not modified")),
        )
        stats = {
            "providers": {
                "famapp": {"payments": 0, "amount": 0.0},
                "manual": {"payments": 0, "amount": 0.0},
                "vc_gateway": {"payments": 0, "amount": 0.0},
            },
            "total_payments": 0,
            "total_amount": 0.0,
        }
        with patch.object(admin, "get_payment_stats_last_24h", new=AsyncMock(return_value=stats)):
            asyncio.run(admin._render_payment_stats_message(message, 1))


if __name__ == "__main__":
    unittest.main()
