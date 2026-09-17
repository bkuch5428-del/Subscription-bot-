import asyncio
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("VC_GATEWAY_UPI_ID", "merchant@example")

import handlers.payment as payment  # noqa: E402


class VcGatewayTests(unittest.TestCase):
    def test_qr_contains_stored_amount_and_vc_order_id(self):
        with patch.object(payment, "VC_GATEWAY_UPI_ID", "merchant@example"):
            uri = payment._build_vc_upi_uri("129.50", "VC260917123456ABC")
        query = parse_qs(urlsplit(uri).query)
        self.assertEqual(query["pa"], ["merchant@example"])
        self.assertEqual(query["am"], ["129.5"])
        self.assertEqual(query["tn"], ["VC260917123456ABC"])
        self.assertTrue(payment._generate_vc_qr_bytes("129.50", "VC260917123456ABC"))

    def test_response_parser_handles_json_and_plain_statuses(self):
        parsed = payment._parse_vc_gateway_response(
            '{"status":"SUCCESS","data":{"order_id":"VC1","amount":"10.00"}}'
        )
        self.assertEqual(parsed["status"], "SUCCESS")
        self.assertEqual(parsed["order_id"], "VC1")
        self.assertEqual(parsed["amount"], "10.00")
        self.assertEqual(payment._parse_vc_gateway_response("PENDING")["status"], "PENDING")
        self.assertIsNone(payment._parse_vc_gateway_response("not a provider response"))

    def test_mocked_gateway_statuses_and_success_validation(self):
        class FakeResponse:
            status = 200

            def __init__(self, body):
                self.body = body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def text(self):
                return self.body

        class FakeSession:
            def __init__(self, body):
                self.body = body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def get(self, *_args, **_kwargs):
                return FakeResponse(self.body)

        order = {
            "order_id": "ORD1",
            "user_id": 7,
            "payment_provider": "vc_gateway",
            "vc_order_id": "VC1",
            "expected_amount": "10.00",
        }

        async def verify(body):
            with (
                patch.object(payment, "VC_GATEWAY_API_KEY", "secret"),
                patch.object(payment.aiohttp, "ClientSession", lambda **_kwargs: FakeSession(body)),
            ):
                return await payment.verify_vc_gateway_payment(order)

        for status in ("PENDING", "FAILED", "INVALID", "NOT_FOUND"):
            result, _summary = asyncio.run(verify('{"status":"%s"}' % status))
            self.assertEqual(result, status)

        success, _summary = asyncio.run(verify('{"status":"SUCCESS","order_id":"VC1","amount":"10.00"}'))
        self.assertEqual(success, "SUCCESS")
        mismatch, _summary = asyncio.run(verify('{"status":"SUCCESS","order_id":"VC1","amount":"11.00"}'))
        self.assertEqual(mismatch, "INVALID")

    def test_provider_settings_default_to_famapp_and_manual(self):
        async def setting(_key, default=""):
            return default

        with patch.object(payment, "get_setting", new=setting):
            providers = asyncio.run(payment._enabled_payment_providers())
        self.assertEqual(providers, ["famapp", "manual"])

    def test_active_provider_defaults_to_famapp(self):
        async def setting(_key, default=""):
            return default

        with patch.object(payment, "get_setting", new=setting):
            self.assertEqual(asyncio.run(payment._active_payment_provider()), "famapp")

    def test_invalid_active_provider_falls_back_to_famapp(self):
        with patch.object(payment, "get_setting", new=AsyncMock(return_value="unknown")):
            self.assertEqual(asyncio.run(payment._active_payment_provider()), "famapp")

    def test_legacy_provider_selection_callback_routes_directly(self):
        call = SimpleNamespace(
            data="choose_payment:3",
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        bot = AsyncMock()
        with patch.object(payment, "callback_buy", new=AsyncMock()) as callback_buy:
            asyncio.run(payment.callback_choose_payment(call, bot))
        callback_buy.assert_awaited_once_with(call, bot, plan_id=3)

    def test_all_disabled_has_no_enabled_provider(self):
        async def setting(_key, _default=""):
            return "0"

        with patch.object(payment, "get_setting", new=setting):
            providers = asyncio.run(payment._enabled_payment_providers())
        self.assertEqual(providers, [])

    def test_vc_buy_creates_fresh_provider_orders(self):
        bot = AsyncMock()
        bot.send_photo.side_effect = [SimpleNamespace(message_id=10), SimpleNamespace(message_id=12)]
        bot.send_message.side_effect = [SimpleNamespace(message_id=11), SimpleNamespace(message_id=13)]
        plan = {
            "name": "Gold",
            "price": "199",
            "validity": "30 days",
            "access_link": "https://example.com/access",
        }
        created = []

        async def record_order(**kwargs):
            created.append(kwargs)

        async def run():
            with (
                patch.object(payment, "VC_GATEWAY_UPI_ID", "merchant@example"),
                patch.object(payment, "create_order", new=record_order),
                patch.object(payment, "supersede_active_orders", new=AsyncMock()),
                patch.object(payment, "update_order_messages", new=AsyncMock()),
                patch.object(payment, "set_pending_reminder", new=AsyncMock()),
            ):
                await payment.create_vc_gateway_payment(bot, 7, 7, plan, 3, "199", "Price", 0)
                await payment.create_vc_gateway_payment(bot, 7, 7, plan, 3, "199", "Price", 0)

        asyncio.run(run())
        self.assertEqual(len(created), 2)
        self.assertNotEqual(created[0]["order_id"], created[1]["order_id"])
        self.assertNotEqual(created[0]["vc_order_id"], created[1]["vc_order_id"])
        self.assertEqual(created[0]["payment_provider"], "vc_gateway")
        first_qr_uri = payment._build_vc_upi_uri("199", created[0]["vc_order_id"])
        self.assertIn(created[0]["vc_order_id"], first_qr_uri)

    def test_order_provider_rejects_cross_provider_callbacks(self):
        self.assertEqual(payment._order_provider({"payment_provider": "vc_gateway"}), "vc_gateway")
        self.assertEqual(payment._order_provider({"payment_purpose": "FAP123"}), "famapp")
        self.assertEqual(payment._order_provider({}), "manual")


if __name__ == "__main__":
    unittest.main()
