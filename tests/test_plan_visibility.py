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
import handlers.payment as payment  # noqa: E402
from keyboards.menu import admin_edit_fields_keyboard  # noqa: E402


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, ordering):
        return self

    def __aiter__(self):
        async def iterate():
            for doc in self.docs:
                yield doc
        return iterate()


class FakePlanCollection:
    def __init__(self, docs):
        self.docs = {doc["_id"]: doc for doc in docs}

    def find(self, query):
        return FakeCursor([
            doc.copy() for doc in self.docs.values()
            if doc.get("is_hidden") is not True
        ])

    async def find_one_and_update(self, query, update, return_document):
        doc = self.docs[query["_id"]]
        doc["is_hidden"] = not bool(doc.get("is_hidden", False))
        return doc.copy()


def button_labels(keyboard):
    return [button.text for row in keyboard.inline_keyboard for button in row]


class PlanVisibilityTests(unittest.TestCase):
    def test_visible_plan_query_keeps_legacy_and_visible_plans_only(self):
        docs = [
            {"_id": 1, "name": "Legacy", "price": "49"},
            {"_id": 2, "name": "Visible", "price": "99", "is_hidden": False},
            {"_id": 3, "name": "Hidden", "price": "199", "is_hidden": True},
        ]
        plans = SimpleNamespace()

        def find(query):
            self.assertEqual(query, {"is_hidden": {"$ne": True}})
            return FakeCursor([doc for doc in docs if doc.get("is_hidden") is not True])

        plans.find = find

        async def get_plans():
            with patch.object(db, "_plans", plans):
                return await db.get_visible_plans()

        visible = asyncio.run(get_plans())
        self.assertEqual([plan["id"] for plan in visible], [1, 2])
        self.assertEqual([plan["price"] for plan in visible], ["49", "99"])
        self.assertEqual([plan["is_hidden"] for plan in visible], [False, False])
        self.assertFalse(db._plan_doc_to_dict(docs[0])["is_hidden"])
        self.assertEqual(db._plan_doc_to_dict(docs[2])["id"], 3)

    def test_edit_plan_selection_shows_individual_hide_action(self):
        call = SimpleNamespace(
            data="admin_ep:41",
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        plan = {"id": 41, "name": "Plan A", "is_hidden": False}

        async def select():
            with (
                patch.object(admin, "_is_admin", return_value=True),
                patch.object(admin, "get_plan", new=AsyncMock(return_value=plan)),
            ):
                await admin.cb_edit_plan_selected(call)

        asyncio.run(select())
        self.assertIn("👁️ Hide Plan", button_labels(call.message.edit_text.await_args.kwargs["reply_markup"]))
        self.assertEqual(call.data, "admin_ep:41")

    def test_toggle_refreshes_selected_plan_to_show_then_hide(self):
        call = SimpleNamespace(
            data="admin_toggle_plan_visibility:41",
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        async def toggle():
            with (
                patch.object(admin, "_is_admin", return_value=True),
                patch.object(admin, "toggle_plan_visibility", new=AsyncMock(side_effect=[
                    {"id": 41, "name": "Plan A", "is_hidden": True},
                    {"id": 41, "name": "Plan A", "is_hidden": False},
                ])) as update,
            ):
                await admin.cb_toggle_plan_visibility(call)
                hidden_labels = button_labels(call.message.edit_text.await_args.kwargs["reply_markup"])
                call.message.edit_text.reset_mock()
                await admin.cb_toggle_plan_visibility(call)
                visible_labels = button_labels(call.message.edit_text.await_args.kwargs["reply_markup"])
            self.assertEqual(update.await_args_list[0].args, (41,))
            self.assertEqual(update.await_args_list[1].args, (41,))
            return hidden_labels, visible_labels

        hidden_labels, visible_labels = asyncio.run(toggle())
        self.assertIn("👁️ Show Plan", hidden_labels)
        self.assertIn("👁️ Hide Plan", visible_labels)
        self.assertEqual(call.data, "admin_toggle_plan_visibility:41")

    def test_non_admin_cannot_toggle_visibility(self):
        call = SimpleNamespace(
            data="admin_toggle_plan_visibility:41",
            from_user=SimpleNamespace(id=999),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        async def reject():
            with (
                patch.object(admin, "_is_admin", return_value=False),
                patch.object(admin, "toggle_plan_visibility", new=AsyncMock()) as toggle,
            ):
                await admin.cb_toggle_plan_visibility(call)
            toggle.assert_not_awaited()

        asyncio.run(reject())
        call.answer.assert_awaited_once_with("⛔ Unauthorised.", show_alert=True)

    def test_hidden_plan_old_buy_callback_does_not_start_payment(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            answer=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock())),
        )
        call = SimpleNamespace(
            data="buy:41",
            from_user=SimpleNamespace(id=7, first_name="User"),
            message=message,
            answer=AsyncMock(),
        )

        async def attempt_purchase():
            with (
                patch.object(payment, "get_plan", new=AsyncMock(return_value={
                    "id": 41,
                    "name": "Plan A",
                    "price": "99",
                    "validity": "30 days",
                    "is_hidden": True,
                })),
                patch.object(payment, "cancel_start_reminders", new=AsyncMock()) as reminders,
                patch.object(payment, "_send_payment_screen", new=AsyncMock()) as create_payment,
                patch.object(payment, "create_vc_gateway_payment", new=AsyncMock()) as create_vc,
                patch.object(payment, "_send_manual_payment_screen", new=AsyncMock()) as create_manual,
            ):
                await payment.callback_buy(call, bot=AsyncMock())
            reminders.assert_not_awaited()
            create_payment.assert_not_awaited()
            create_vc.assert_not_awaited()
            create_manual.assert_not_awaited()

        asyncio.run(attempt_purchase())
        self.assertIn(
            "⚠️ This plan is currently unavailable.",
            [entry.args[0] for entry in message.answer.await_args_list],
        )

    def test_admin_visibility_toggle_is_atomic_and_scoped_to_plan(self):
        updated = {"_id": 41, "name": "Plan A", "price": "99", "is_hidden": True}
        plans = SimpleNamespace(find_one_and_update=AsyncMock(return_value=updated))

        async def toggle():
            with patch.object(db, "_plans", plans):
                return await db.toggle_plan_visibility(41)

        result = asyncio.run(toggle())
        query, update = plans.find_one_and_update.await_args.args
        self.assertEqual(query, {"_id": 41})
        self.assertEqual(update, [{"$set": {"is_hidden": {"$not": [{"$ifNull": ["$is_hidden", False]}]}}}])
        self.assertEqual(result["id"], 41)
        self.assertEqual(result["price"], "99")
        self.assertTrue(result["is_hidden"])

    def test_hide_show_is_immediate_and_leaves_other_plans_and_subscriptions_unchanged(self):
        original_docs = [
            {"_id": 41, "name": "Plan A", "price": "99", "validity": "30 days"},
            {"_id": 42, "name": "Plan B", "price": "149", "is_hidden": False},
            {"_id": 43, "name": "Plan C", "price": "199", "is_hidden": False},
        ]
        plans = FakePlanCollection([doc.copy() for doc in original_docs])
        existing_subscription = {"user_id": 7, "plan_id": 41, "subscription_end": "2030-01-01"}

        async def toggle_and_read():
            with patch.object(db, "_plans", plans):
                hidden = await db.toggle_plan_visibility(41)
                after_hide = await db.get_visible_plans()
                shown = await db.toggle_plan_visibility(41)
                after_show = await db.get_visible_plans()
                return hidden, after_hide, shown, after_show

        hidden, after_hide, shown, after_show = asyncio.run(toggle_and_read())
        self.assertTrue(hidden["is_hidden"])
        self.assertEqual([plan["id"] for plan in after_hide], [42, 43])
        self.assertFalse(shown["is_hidden"])
        self.assertEqual([plan["id"] for plan in after_show], [41, 42, 43])
        self.assertEqual([plan["price"] for plan in after_show], ["99", "149", "199"])
        self.assertEqual(plans.docs[42], original_docs[1])
        self.assertEqual(plans.docs[43], original_docs[2])
        self.assertEqual(
            {key: value for key, value in plans.docs[41].items() if key != "is_hidden"},
            original_docs[0],
        )
        self.assertEqual(existing_subscription, {"user_id": 7, "plan_id": 41, "subscription_end": "2030-01-01"})

    def test_edit_keyboard_defaults_to_visible_plan(self):
        labels = button_labels(admin_edit_fields_keyboard(41))
        self.assertIn("👁️ Hide Plan", labels)
        self.assertNotIn("👁️ Show Plan", labels)


if __name__ == "__main__":
    unittest.main()