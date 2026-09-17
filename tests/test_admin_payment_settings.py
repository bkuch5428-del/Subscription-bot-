import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

import handlers.admin as admin  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
