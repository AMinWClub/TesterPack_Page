"""Historical receipts and per-link capacity; only synthetic in-memory documents."""
import asyncio
import copy
import unittest
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import AutoReconnect

from app import selection_config, selections as service
from app.workers import tester_sms as worker
from tests import test_selections as fixtures


class SelectionHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.SelectionTests.asyncSetUp
    call = fixtures.SelectionTests.call
    stored_order = fixtures.SelectionTests.stored_order
    selection = fixtures.SelectionTests.selection
    freeze_window = fixtures.SelectionTests.freeze_window

    async def historical_receipt(self, original_count, current_count):
        self.selection["required_count"] = original_count
        pids = [str(i) for i in range(1, original_count + 1)]
        self.assertEqual((await self.call("submit", pids=pids))[0], 200)
        self.selection.pop("required_count")  # Pre-feature receipt, without migration.
        before = copy.deepcopy((self.selection, self.stored_order))
        writes = self.db.writes
        with patch.object(service, "COUNT", current_count):
            for action, extra in (("context", {}), ("submit", {"pids": pids})):
                status, body, _ = await self.call(action, **extra)
                self.assertEqual(status, 200)
                self.assertEqual(body["order"]["tester_count"], original_count)
                self.assertEqual(body["selection"]["selected_pids"], pids)
                self.assertEqual(body["selection"]["status"], "submitted")
            self.assertEqual((await self.call("submit", pids=list(reversed(pids))))[0], 409)
        self.assertEqual((self.selection, self.stored_order), before)
        self.assertEqual(self.db.writes, writes)

    async def test_ten_receipt_with_current_six(self):
        await self.historical_receipt(10, 6)

    async def test_six_receipt_with_current_five(self):
        await self.historical_receipt(6, 5)

    async def test_receipts_have_no_historical_count_allowlist(self):
        await self.historical_receipt(13, 3)

    async def test_new_link_freezes_capacity_and_worker_retries_never_change_it(self):
        db = fixtures.FakeDatabase([])
        order_id = ObjectId()
        with patch.object(selection_config, "TESTER_SELECTION_COUNT", 10):
            link = await worker.ensure_selection(db[worker.SELECTIONS], order_id, self.config)
        original = copy.deepcopy(db[worker.SELECTIONS].documents[0])
        with patch.object(selection_config, "TESTER_SELECTION_COUNT", 5):
            self.assertEqual(await worker.ensure_selection(db[worker.SELECTIONS], order_id, self.config), link)
            await worker.ensure_selection(db[worker.SELECTIONS], ObjectId(), self.config)
        self.assertEqual(db[worker.SELECTIONS].documents[0], original)
        self.assertEqual(original["required_count"], 10)
        self.assertEqual(db[worker.SELECTIONS].documents[1]["required_count"], 5)

    async def test_pending_link_preserves_capacity_in_context_and_submission(self):
        self.selection["required_count"] = 10
        with patch.object(service, "COUNT", 6):
            self.assertEqual((await self.call())[1]["order"]["tester_count"], 10)
            self.assertEqual((await self.call("submit", pids=[str(i) for i in range(1, 7)]))[0], 422)
            self.assertEqual((await self.call("submit", pids=[str(i) for i in range(1, 11)]))[0], 200)

    async def test_legacy_pending_policy_is_readonly_until_accepting_current_count(self):
        self.selection.pop("required_count")
        before = copy.deepcopy((self.selection, self.stored_order))
        writes = self.db.writes
        with patch.object(service, "COUNT", 5):
            self.assertEqual((await self.call())[1]["order"]["tester_count"], 5)
            self.assertEqual((self.selection, self.stored_order), before)
            self.assertEqual(self.db.writes, writes)
            self.assertEqual((await self.call("submit", pids=[str(i) for i in range(1, 7)]))[0], 422)
            pids = [str(i) for i in range(1, 6)]
            self.assertEqual((await self.call("submit", pids=pids))[0], 200)
        self.assertEqual(self.selection["required_count"], 5)
        with patch.object(service, "COUNT", 3):
            self.assertEqual((await self.call("submit", pids=pids))[0], 200)

    async def test_invalid_stored_capacity_fails_closed_without_writes(self):
        for invalid in (None, True, 0, -1, "6", [], 499):
            self.selection["required_count"] = invalid
            before = copy.deepcopy((self.selection, self.stored_order))
            for action, extra in (("context", {}), ("submit", {"pids": self.pids})):
                status, body, _ = await self.call(action, **extra)
                self.assertEqual((status, body["detail"]["code"]), (409, "inconsistent_selection"))
            self.assertEqual((self.selection, self.stored_order), before)

    async def test_legacy_pending_capacity_is_never_inferred_from_stray_intent_fields(self):
        self.selection.pop("required_count")
        self.selection.update(submission_id="a" * 32, selected_pids=["old"], selected_names=["Old name"])
        with patch.object(service, "COUNT", 5):
            self.assertEqual((await self.call())[1]["order"]["tester_count"], 5)

    async def test_legacy_pending_concurrent_different_counts_only_one_intent_wins(self):
        self.selection.pop("required_count")
        results = await asyncio.gather(
            self.call("submit", pids=self.pids), self.call("submit", pids=list(reversed(self.pids))),
        )
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        self.assertEqual(self.selection["required_count"], len(self.pids))

    async def test_recovery_of_legacy_intent_after_count_change(self):
        self.selection["required_count"] = 10
        pids = [str(i) for i in range(1, 11)]
        orders = self.db["preprocess_orders"]
        original = orders.update_one
        with patch.object(orders, "update_one", side_effect=AutoReconnect("synthetic lost connection")):
            self.assertEqual((await self.call("submit", pids=pids))[0], 503)
        self.assertEqual(self.selection["status"], "submitting")
        self.selection.pop("required_count")
        with patch.object(service, "COUNT", 5), patch.object(orders, "update_one", wraps=original):
            status, body, _ = await self.call("submit", pids=pids)
        self.assertEqual((status, body["order"]["tester_count"], body["selection"]["status"]), (200, 10, "submitted"))

    async def test_historical_count_does_not_bypass_deadline(self):
        from datetime import timedelta
        self.selection["required_count"] = 10
        self.freeze_window()
        self.clock += timedelta(minutes=31)
        with patch.object(service, "COUNT", 5):
            self.assertEqual((await self.call("submit", pids=[str(i) for i in range(1, 11)]))[0], 410)
        self.assertNotIn("recommended_products", self.stored_order)


class ReceiptStructureTests(unittest.TestCase):
    def test_valid_receipt_ignores_current_and_stored_required_count(self):
        for count in (1, 6, 10, 17):
            selection = {"submission_id": "a" * 32, "required_count": None,
                         "selected_pids": [str(i) for i in range(count)], "selected_names": ["Exact name"] * count}
            with patch.object(service, "COUNT", 5):
                self.assertTrue(service.intent_valid(selection))

    def test_invalid_receipts_are_rejected_without_exceptions(self):
        valid = {"submission_id": "a" * 32, "selected_pids": ["1", "2"], "selected_names": ["One", "Two"]}
        corruptions = ({"selected_pids": []}, {"selected_names": []}, {"selected_names": ["One"]},
                       {"selected_pids": ["1", "1"]}, {"selected_pids": ["1", {}]},
                       {"selected_pids": ["1", " 2"]}, {"selected_pids": ["1", "x\x1f"]},
                       {"selected_pids": ["1", "x" * 129]}, {"selected_pids": "12"},
                       {"selected_names": ["One", None]}, {"selected_names": ["One", "\u1680\x1c "]},
                       {"submission_id": "invalid"})
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                self.assertFalse(service.intent_valid({**valid, **corruption}))
