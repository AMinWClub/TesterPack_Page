"""Mock MongoDB and HTTP: test worker fencing, date filters and crash recovery."""

import asyncio
import copy
import io
import json
import os
import re
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

from bson import ObjectId
from pymongo.errors import AutoReconnect, DuplicateKeyError

from app.services.sms import FarazSmsClient, FarazSmsConfig
from app.workers import tester_sms as worker
from tests.test_sms_faraz import FakeHttpResponse


def path_value(document, path):
    if not path:
        return document
    key, _, tail = path.partition(".")
    if isinstance(document, list):
        return [path_value(item, path) for item in document]
    value = document.get(key) if isinstance(document, dict) else None
    return path_value(value, tail) if tail else value


def expression(value, document, variables=None):
    """Evaluate only the Mongo operators used by the real date expression."""
    variables = variables or {}
    if isinstance(value, str) and value.startswith("$$"):
        return path_value(variables, value[2:])
    if isinstance(value, str) and value.startswith("$"):
        return path_value(document, value[1:])
    if not isinstance(value, dict):
        return value
    operator, operands = next(iter(value.items()))
    evaluate = lambda item: expression(item, document, variables)
    if operator == "$let":
        new_variables = {**variables, **{key: evaluate(item) for key, item in operands["vars"].items()}}
        return expression(operands["in"], document, new_variables)
    if operator == "$cond":
        return evaluate(operands[1] if evaluate(operands[0]) else operands[2])
    if operator == "$type":
        item = evaluate(operands)
        return "string" if isinstance(item, str) else "other"
    if operator == "$regexFind":
        match = re.search(operands["regex"], evaluate(operands["input"]))
        return {"captures": list(match.groups())} if match else None
    if operator == "$arrayElemAt":
        return evaluate(operands[0])[operands[1]]
    if operator == "$ifNull":
        first = evaluate(operands[0])
        return evaluate(operands[1]) if first is None else first
    if operator == "$concat":
        items = [evaluate(item) for item in operands]
        return None if None in items else "".join(items)
    if operator == "$substrBytes":
        return evaluate(operands[0])[operands[1]:operands[1] + operands[2]]
    if operator == "$dateFromString":
        try:
            return datetime.strptime(evaluate(operands["dateString"]), operands["format"]).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    if operator == "$and":
        return all(evaluate(item) for item in operands)
    left, right = (evaluate(item) for item in operands)
    if operator == "$eq":
        return left == right
    if operator == "$ne":
        return left != right
    if operator == "$gte":
        return left is not None and right is not None and left >= right
    raise AssertionError(f"Unsupported expression: {operator}")


def equal(left, right):
    # BSON distinguishes a boolean from an integer.
    return type(left) is type(right) and left == right


def condition(value, expected):
    if not isinstance(expected, dict):
        return any(equal(item, expected) for item in value) if isinstance(value, list) else equal(value, expected)
    for operator, operand in expected.items():
        if operator == "$ne" and equal(value, operand):
            return False
        if operator == "$in" and not any(equal(value, item) for item in operand):
            return False
        if operator == "$gte" and (value is None or type(value) is not type(operand) or value < operand):
            return False
        if operator == "$lte" and (value is None or value > operand):
            return False
        if operator == "$exists" and (value is not None) != operand:
            return False
        if operator == "$type" and not (operand == "string" and isinstance(value, str)):
            return False
        if operator == "$size" and (not isinstance(value, list) or len(value) != operand):
            return False
        if operator == "$elemMatch" and (not isinstance(value, list) or not any(condition(item, operand) for item in value)):
            return False
    return True


def matches(document, query):
    for key, expected in query.items():
        if key == "$expr":
            if not expression(expected, document):
                return False
        elif key == "$or":
            if not any(matches(document, item) for item in expected):
                return False
        elif not condition(path_value(document, key), expected):
            return False
    return True


class FakeCursor:
    def __init__(self, documents):
        self.documents = copy.deepcopy(documents)

    def sort(self, field, direction):
        self.documents.sort(key=lambda document: (path_value(document, field) is None, path_value(document, field)))
        return self

    def limit(self, limit):
        self.documents = self.documents[:limit]
        return self

    def __aiter__(self):
        self.iterator = iter(self.documents)
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, db, documents=()):
        self.db = db
        self.documents = copy.deepcopy(list(documents))
        self.indexes = {}
        self.fail_update = None

    def with_options(self, **kwargs):
        self.write_concern = kwargs["write_concern"]
        return self

    async def create_index(self, keys, **kwargs):
        self.db.writes += 1
        self.indexes[kwargs["name"]] = {"key": [(keys, 1)] if isinstance(keys, str) else keys, **kwargs}
        if "collation" in kwargs:
            self.indexes[kwargs["name"]]["collation"] = kwargs["collation"].document

    async def index_information(self):
        return self.indexes

    async def find_one(self, query, **kwargs):
        await asyncio.sleep(0)
        return next((copy.deepcopy(document) for document in self.documents if matches(document, query)), None)

    def find(self, query):
        return FakeCursor([document for document in self.documents if matches(document, query)])

    async def update_one(self, query, update, upsert=False, **kwargs):
        await asyncio.sleep(0)
        if self.fail_update and self.fail_update(update):
            raise AutoReconnect("simulated database failure")
        # No awaits after the match: mimic atomic single-document updates.
        self.db.writes += 1
        for document in self.documents:
            if matches(document, query):
                before = copy.deepcopy(document)
                document.update(copy.deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=int(before != document))
        if upsert:
            self.documents.append(copy.deepcopy(update.get("$setOnInsert", {})))
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def find_one_and_update(self, query, update, **kwargs):
        await asyncio.sleep(0)
        for document in self.documents:
            if matches(document, query):
                self.db.writes += 1
                document.update(copy.deepcopy(update["$set"]))
                return copy.deepcopy(document)
        return None

    async def aggregate(self, pipeline, **kwargs):
        self.last_pipeline = pipeline
        self.last_options = kwargs
        documents = copy.deepcopy(self.documents)
        for stage in pipeline:
            if "$match" in stage:
                documents = [document for document in documents if matches(document, stage["$match"])]
            elif "$sort" in stage:
                fields = list(stage["$sort"])
                documents.sort(key=lambda document: tuple(document[field] for field in fields))
            elif "$lookup" in stage:
                lookup = stage["$lookup"]
                for document in documents:
                    document[lookup["as"]] = [copy.deepcopy(other) for other in self.db[lookup["from"]].documents
                                               if equal(other[lookup["foreignField"]], document[lookup["localField"]])]
            elif "$limit" in stage:
                documents = documents[:stage["$limit"]]
            elif "$project" in stage:
                documents = [{key: document[key] for key in stage["$project"] if key in document} for document in documents]
        return FakeCursor(documents)


class FakeDatabase:
    def __init__(self, orders):
        self.writes = 0
        self.collections = {"preprocess_orders": FakeCollection(self, orders)}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection(self)
        return self.collections[name]


class SimulatedCrash(BaseException):
    pass


def order(order_id="order-1", **changes):
    return {"_id": order_id, "product_id": ["999", "123"], "date_created": "2026-10-13T20:30:00Z",
            "phone": "+989120000000", **changes}


class TesterWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = self.start_patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse())
        self.start_patch("socket.create_connection", side_effect=AssertionError("Network disabled"))
        self.start_patch("app.services.sms.faraz.load_dotenv")
        self.settings = worker.WorkerSettings(
            orders_collection="preprocess_orders", product_id="123",
            start_at=datetime(2026, 10, 13, 20, 30, tzinfo=timezone.utc), sms_enabled=True,
            selection_base_url="https://tester.example.com/s", token_secret="random-test-secret-" + "x" * 32,
        )
        self.sms = FarazSmsClient(FarazSmsConfig(api_key="test-key", line_number="50001", pattern_code="pattern"))
        self.sms.send_pattern = Mock(wraps=self.sms.send_pattern)
        self.db = FakeDatabase([order()])
        await worker.create_indexes(worker.worker_collections(self.db, self.settings))
        self.db.writes = 0

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    async def run_worker(self, **kwargs):
        return await worker.run_once(self.db, self.settings, sms=self.sms, **kwargs)

    def notification(self):
        return self.db[worker.NOTIFICATIONS].documents[0]

    async def test_date_product_array_and_sms_flag_filter(self):
        self.db["preprocess_orders"].documents = [
            order("old", date_created="2026-10-13T20:29:59.999999Z"),
            order("boundary"),
            order("new", date_created="2026-10-13T20:30:00.000001Z", product_id=[123]),
            order("other", product_id=["1234"]),
            order("sent", sms_sent=True),
            order("scalar", product_id="123"),
        ]
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 2)
        self.assertEqual(self.sms.send_pattern.call_count, 2)
        changed = {item["_id"] for item in self.db["preprocess_orders"].documents if item.get("sms_sent") is True}
        self.assertEqual(changed, {"boundary", "new", "sent"})
        self.assertNotIn("sms_sent", self.db["preprocess_orders"].documents[0])

    async def test_rerun_does_not_duplicate_sms_notification_or_selection(self):
        await self.run_worker()
        await self.run_worker()
        self.sms.send_pattern.assert_called_once()
        self.assertEqual(len(self.db[worker.NOTIFICATIONS].documents), 1)
        self.assertEqual(len(self.db[worker.SELECTIONS].documents), 1)

    async def test_two_concurrent_workers_claim_only_once(self):
        await asyncio.gather(self.run_worker(), self.run_worker())
        self.sms.send_pattern.assert_called_once()
        self.assertEqual(len(self.db[worker.NOTIFICATIONS].documents), 1)
        self.assertEqual(len(self.db[worker.SELECTIONS].documents), 1)

    async def test_selection_creation_is_idempotent_and_does_not_store_raw_token(self):
        selections = self.db[worker.SELECTIONS]
        links = await asyncio.gather(*(worker.ensure_selection(selections, "order-1", self.settings) for _ in range(2)))
        self.assertEqual(links[0], links[1])
        self.assertEqual(len(selections.documents), 1)
        token = links[0].rsplit("/", 1)[1]
        self.assertEqual(len(token), 24)
        self.assertNotIn(token, repr(selections.documents))
        self.assertEqual(set(selections.documents[0]), {"order_id", "token_hash", "token_version", "status", "created_at"})

    async def test_duplicate_key_race_reuses_existing_notification_and_selection(self):
        collections = worker.worker_collections(self.db, self.settings)
        notification = await worker.prepare_notification(collections["notifications"], "order-1")
        link = await worker.ensure_selection(collections["selections"], "order-1", self.settings)
        with patch.object(collections["notifications"], "update_one", new=AsyncMock(side_effect=DuplicateKeyError("race"))):
            again = await worker.prepare_notification(collections["notifications"], "order-1")
        with patch.object(collections["selections"], "update_one", new=AsyncMock(side_effect=DuplicateKeyError("race"))):
            same_link = await worker.ensure_selection(collections["selections"], "order-1", self.settings)
        self.assertEqual(notification, again)
        self.assertEqual(link, same_link)

    async def test_changed_token_secret_does_not_send_an_inconsistent_selection(self):
        await worker.ensure_selection(self.db[worker.SELECTIONS], "order-1", self.settings)
        self.settings = replace(self.settings, token_secret="new-random-secret-" + "y" * 32)
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "failed")
        self.sms.send_pattern.assert_not_called()

    async def test_late_response_can_finalize_same_expired_attempt_without_resubmission(self):
        notifications = self.db[worker.NOTIFICATIONS]
        await worker.prepare_notification(notifications, "order-1")
        claim = await worker.claim_notification(notifications, "order-1", 15)
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await worker.recover_notifications(worker.worker_collections(self.db, self.settings), worker.order_filter(self.settings), 50)
        self.assertEqual(self.notification()["status"], "submission_unknown")
        self.assertTrue(await worker.finish_notification(notifications, claim, "accepted", provider_data=42))
        self.assertEqual(self.notification()["status"], "accepted")
        self.sms.send_pattern.assert_not_called()

    async def test_worker_cli_closes_mongodb_client_after_failure(self):
        mongo = Mock()
        mongo.admin.command = AsyncMock(side_effect=AutoReconnect("private connection details"))
        mongo.close = AsyncMock()
        with patch.object(worker.WorkerSettings, "from_environment", return_value=self.settings), \
             patch.object(worker, "AsyncMongoClient", return_value=mongo):
            with self.assertRaises(AutoReconnect):
                await worker.run_command(SimpleNamespace(once=True, dry_run=True, create_indexes=False, limit=1))
        mongo.close.assert_awaited_once()

    async def test_dry_run_is_read_only_and_does_not_generate_token(self):
        before = copy.deepcopy(self.db["preprocess_orders"].documents)
        with patch.object(worker, "token_for_order", side_effect=AssertionError("No token in dry run")):
            result = await self.run_worker(dry_run=True)
        self.assertEqual(result["eligible"], 1)
        self.assertTrue(result["read_only"])
        self.assertEqual(self.db.writes, 0)
        self.assertEqual(before, self.db["preprocess_orders"].documents)
        self.sms.send_pattern.assert_not_called()
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents, [])

    async def test_sms_disabled_is_read_only_without_secret_or_faraz_client(self):
        self.settings = replace(self.settings, sms_enabled=False, token_secret="", selection_base_url="")
        with patch.object(worker, "FarazSmsClient", side_effect=AssertionError("No SMS client needed")), \
             patch.object(worker, "token_for_order", side_effect=AssertionError("No token")):
            result = await worker.run_once(self.db, self.settings)
        self.assertTrue(result["read_only"])
        self.assertEqual(self.db.writes, 0)

    async def test_definite_provider_rejection_is_failed_without_retry(self):
        self.http.side_effect = HTTPError("https://provider.example", 422, "invalid", {}, io.BytesIO(b'{"status":"error"}'))
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "failed")
        self.assertEqual(self.notification()["error_code"], "provider_rejected")
        await self.run_worker()
        self.sms.send_pattern.assert_called_once()

    async def test_timeout_is_unknown_without_retry(self):
        self.http.side_effect = TimeoutError("private transport details")
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "submission_unknown")
        await self.run_worker()
        self.sms.send_pattern.assert_called_once()

    async def test_http_500_is_unknown_without_retry(self):
        self.http.side_effect = HTTPError("https://provider.example", 500, "server error", {}, io.BytesIO(b'failure'))
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "submission_unknown")
        await self.run_worker()
        self.sms.send_pattern.assert_called_once()

    async def test_malformed_success_response_is_unknown(self):
        self.http.return_value = FakeHttpResponse(201, b'{"status":"success","data":{"id":42}}')
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "submission_unknown")
        await self.run_worker()
        self.sms.send_pattern.assert_called_once()

    async def test_crash_after_dispatch_is_not_retried_and_expired_claim_becomes_unknown(self):
        self.sms.send_pattern.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            await self.run_worker()
        self.assertEqual(self.notification()["status"], "sending")
        await self.run_worker()
        self.sms.send_pattern.assert_called_once()
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "submission_unknown")
        self.sms.send_pattern.assert_called_once()

    async def test_database_failure_after_response_never_resubmits(self):
        notifications = self.db[worker.NOTIFICATIONS]
        notifications.fail_update = lambda update: update.get("$set", {}).get("status") == "accepted"
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "sending")
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        notifications.fail_update = None
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "submission_unknown")
        self.sms.send_pattern.assert_called_once()

    async def test_accepted_notification_repairs_order_flag_without_resending(self):
        orders = self.db["preprocess_orders"]
        orders.fail_update = lambda update: "sms_sent" in update.get("$set", {})
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertNotIn("sms_sent", orders.documents[0])
        orders.fail_update = None
        result = await self.run_worker()
        self.assertEqual(result["repaired"], 1)
        self.assertTrue(orders.documents[0]["sms_sent"])
        self.sms.send_pattern.assert_called_once()

    async def test_repair_never_changes_an_order_before_start(self):
        self.db["preprocess_orders"].documents = [order(date_created="2026-08-26T12:24:27.83444Z")]
        self.db[worker.NOTIFICATIONS].documents = [{"order_id": "order-1", "status": "accepted"}]
        await self.run_worker()
        self.assertNotIn("sms_sent", self.db["preprocess_orders"].documents[0])
        self.sms.send_pattern.assert_not_called()

    async def test_invalid_mobile_records_failure_and_continues(self):
        self.db["preprocess_orders"].documents = [order("bad", phone="invalid"), order("good")]
        result = await self.run_worker()
        self.assertEqual((result["failed"], result["accepted"]), (1, 1))
        self.sms.send_pattern.assert_called_once()

    async def test_link_attribute_is_configurable_and_metadata_contains_no_secrets(self):
        self.settings = replace(self.settings, link_attribute="selection_url")
        self.http.return_value = FakeHttpResponse(201, b'{"status":"success","data":42,"messages":"private-token"}')
        with self.assertNoLogs(worker.logger.name, level="DEBUG"):
            await self.run_worker()
        arguments = self.sms.send_pattern.call_args.kwargs
        self.assertEqual(set(arguments["attributes"]), {"selection_url"})
        url = arguments["attributes"]["selection_url"]
        persisted = repr(self.db[worker.NOTIFICATIONS].documents + self.db[worker.SELECTIONS].documents)
        for secret in (url, url.rsplit("/", 1)[1], "test-key", "private-token", "09120000000"):
            self.assertNotIn(secret, persisted)
        self.assertEqual(self.notification()["provider_data"], 42)

    async def test_limit_and_batch_size_are_bounded_in_server_pipeline(self):
        self.db["preprocess_orders"].documents = [order(str(index)) for index in range(5)]
        self.settings = replace(self.settings, batch_size=2)
        result = await self.run_worker(dry_run=True)
        self.assertEqual(result["eligible"], 2)
        self.assertIn({"$limit": 2}, self.db["preprocess_orders"].last_pipeline)
        self.assertEqual(self.db["preprocess_orders"].last_options["batchSize"], 2)

    async def test_missing_unique_index_prevents_all_writes_and_sends(self):
        self.db[worker.NOTIFICATIONS].indexes = {}
        with self.assertRaises(worker.WorkerConfigurationError):
            await self.run_worker()
        self.assertEqual(self.db.writes, 0)
        self.sms.send_pattern.assert_not_called()


class WorkerSettingsAndTokenTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(worker, "load_dotenv")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.environment = {"TESTER_PACK_PRODUCT_ID": "123", "TESTER_SMS_START_AT": "2026-10-14T00:00:00+03:30"}

    def test_start_date_is_timezone_aware_and_converted_to_utc(self):
        with patch.dict(os.environ, self.environment, clear=True):
            settings = worker.WorkerSettings.from_environment()
        self.assertEqual(settings.start_at, datetime(2026, 10, 13, 20, 30, tzinfo=timezone.utc))
        self.assertFalse(settings.sms_enabled)

    def test_missing_or_invalid_settings_fail_with_setting_name(self):
        for key, invalid in (("TESTER_PACK_PRODUCT_ID", ""), ("TESTER_SMS_START_AT", "invalid"),
                             ("TESTER_SMS_START_AT", "2026-10-14T00:00:00"), ("TESTER_WORKER_BATCH_SIZE", "0"),
                             ("TESTER_SMS_ENABLED", "yes"), ("TESTER_PATTERN_LINK_ATTRIBUTE", "bad-name")):
            with self.subTest(key=key, invalid=invalid), patch.dict(os.environ, {**self.environment, key: invalid}, clear=True):
                with self.assertRaisesRegex(worker.WorkerConfigurationError, key):
                    worker.WorkerSettings.from_environment()

    def test_enabled_delivery_requires_url_and_long_secret(self):
        env = {**self.environment, "TESTER_SMS_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_SELECTION_BASE_URL"):
            worker.WorkerSettings.from_environment(require_delivery=True)
        with patch.dict(os.environ, {**env, "TESTER_SELECTION_BASE_URL": "https://tester.example.com/s"}, clear=True), \
             self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_TOKEN_SECRET"):
            worker.WorkerSettings.from_environment(require_delivery=True)

    def test_typed_identity_and_secret_affect_deterministic_24_character_token(self):
        secret = "a" * 48
        identifier = ObjectId("000000000000000000000001")
        token = worker.token_for_order(identifier, secret)
        self.assertRegex(token, r"^[a-zA-Z0-9_-]{24}$")
        self.assertEqual(token, worker.token_for_order(identifier, secret))
        self.assertNotEqual(token, worker.token_for_order(str(identifier), secret))
        self.assertNotEqual(token, worker.token_for_order(identifier, "b" * 48))

    def test_product_id_variants_do_not_use_substrings_or_array_strings(self):
        self.assertEqual(worker.product_id_variants("123"), ["123", 123])
        self.assertEqual(worker.product_id_variants("00123"), ["00123"])
        self.assertEqual(worker.product_id_variants("sku-123"), ["sku-123"])

    def test_variable_fraction_precision_and_invalid_dates(self):
        settings = worker.WorkerSettings("preprocess_orders", "123", datetime(2026, 10, 13, 20, 30, 0, 500000, timezone.utc))
        match = worker.order_filter(settings)
        for timestamp, expected in (("2026-10-13T20:30:00Z", False), ("2026-10-13T20:30:00.499999Z", False),
                                    ("2026-10-13T20:30:00.5Z", True), ("2026-10-13T20:30:00.500000Z", True),
                                    ("2026-10-13T20:30:01Z", True), ("2026-13-13T20:30:00Z", False),
                                    ("2026-10-13T20:30:00+03:30", False), (None, False)):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(matches(order(date_created=timestamp), match), expected)


if __name__ == "__main__":
    unittest.main()
