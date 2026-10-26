"""Mock MongoDB and HTTP: test worker fencing, pending eligibility and crash recovery."""

import asyncio
import copy
import hashlib
import io
import json
import os
import re
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError, URLError

from bson import ObjectId
from pymongo.errors import AutoReconnect, DuplicateKeyError

from app.services.sms import FarazSmsClient, FarazSmsConfig, SmsValidationError
from app.workers import tester_sms as worker
from tests.test_sms_faraz import FakeHttpResponse, simple_success_body

SMS_TEMPLATE = (
    "راستی اینو یادم رفتتتت\n\n"
    "{full_name} عزیز، اون عطرهایی که می‌گفتی «باید امتحان کنم» کدوما بودن؟\n\n"
    "تسترهای دلخواهت رو تا نیم ساعت دیگه اینجا انتخاب کن که همراه سفارشت برات بفرستیم:\n\n"
    "{link}\n\nعطر سراد"
)

def path_value(document, path):
    if not path:
        return document
    key, _, tail = path.partition(".")
    if isinstance(document, list):
        if key.isdigit():
            value = document[int(key)] if int(key) < len(document) else None
            return path_value(value, tail) if tail else value
        return [path_value(item, path) for item in document]
    value = document.get(key) if isinstance(document, dict) else None
    return path_value(value, tail) if tail else value


def mongo_now():
    return datetime.now(timezone.utc)


def expression(value, document, variables=None):
    """Evaluate the Mongo operators used by worker and selection predicates."""
    variables = variables or {}
    if isinstance(value, str) and value == "$$NOW":
        return variables["NOW"] if "NOW" in variables else mongo_now()
    if isinstance(value, str) and value == "$$ROOT":
        return document
    if isinstance(value, str) and value.startswith("$$"):
        return path_value(variables, value[2:])
    if isinstance(value, str) and value.startswith("$"):
        return path_value(document, value[1:])
    if isinstance(value, list):
        return [expression(item, document, variables) for item in value]
    if not isinstance(value, dict):
        return value
    if not any(key.startswith("$") for key in value):
        return {key: expression(item, document, variables) for key, item in value.items()}
    operator, operands = next(iter(value.items()))
    evaluate = lambda item: expression(item, document, variables)
    if operator == "$literal":
        return operands
    if operator == "$add":
        items = [evaluate(item) for item in operands]
        if isinstance(items[0], datetime):
            return items[0] + timedelta(milliseconds=sum(items[1:]))
        return sum(items)
    if operator == "$min":
        return min(evaluate(item) for item in operands)
    if operator == "$concatArrays":
        return [item for array in operands for item in evaluate(array)]
    if operator == "$mergeObjects":
        result = {}
        for item in operands:
            result.update(evaluate(item))
        return result
    if operator == "$map":
        return [expression(operands["in"], document, {**variables, operands["as"]: item})
                for item in evaluate(operands["input"])]
    if operator == "$let":
        new_variables = {**variables, **{key: evaluate(item) for key, item in operands["vars"].items()}}
        return expression(operands["in"], document, new_variables)
    if operator == "$cond":
        return evaluate(operands[1] if evaluate(operands[0]) else operands[2])
    if operator == "$type":
        item = evaluate(operands)
        return ("string" if isinstance(item, str) else "date" if isinstance(item, datetime)
                else "array" if isinstance(item, list) else "other")
    if operator == "$regexFind":
        match = re.search(operands["regex"], evaluate(operands["input"]))
        return {"captures": list(match.groups())} if match else None
    if operator == "$arrayElemAt":
        array = evaluate(operands[0])
        return array[operands[1]] if array and len(array) > operands[1] else None
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
    if operator == "$or":
        return any(evaluate(item) for item in operands)
    left, right = (evaluate(item) for item in operands)
    if operator == "$eq":
        return left == right
    if operator == "$ne":
        return left != right
    if operator == "$gte":
        return left is not None and right is not None and left >= right
    if operator == "$lt":
        return left is not None and right is not None and left < right
    if operator == "$lte":
        return left is not None and right is not None and left <= right
    raise AssertionError(f"Unsupported expression: {operator}")


def equal(left, right):
    # BSON distinguishes a boolean from an integer.
    return type(left) is type(right) and left == right


def path_exists(document, path):
    key, _, tail = path.partition(".")
    if isinstance(document, list):
        if key.isdigit():
            if int(key) >= len(document):
                return False
            return path_exists(document[int(key)], tail) if tail else True
        return any(path_exists(item, path) for item in document)
    if not isinstance(document, dict) or key not in document:
        return False
    return path_exists(document[key], tail) if tail else True


def condition(value, expected, *, exists=None):
    if not isinstance(expected, dict):
        return any(equal(item, expected) for item in value) if isinstance(value, list) else equal(value, expected)
    for operator, operand in expected.items():
        if operator == "$ne" and (equal(value, operand) or (
            isinstance(value, list) and not isinstance(operand, list) and any(equal(item, operand) for item in value)
        )):
            return False
        if operator == "$in" and not any(equal(value, item) for item in operand):
            return False
        if operator == "$nin" and any(equal(item, excluded)
                                       for item in (value if isinstance(value, list) else [value]) for excluded in operand):
            return False
        if operator == "$gte" and (value is None or type(value) is not type(operand) or value < operand):
            return False
        if operator == "$gt" and (value is None or type(value) is not type(operand) or value <= operand):
            return False
        if operator == "$lte" and (value is None or value > operand):
            return False
        if operator == "$lt" and (value is None or value >= operand):
            return False
        if operator == "$exists" and (value is not None if exists is None else exists) != operand:
            return False
        if operator == "$type" and not (operand == "string" and isinstance(value, str)):
            return False
        if operator == "$size" and (not isinstance(value, list) or len(value) != operand):
            return False
        if operator == "$elemMatch" and (not isinstance(value, list) or not any(
            matches(item, operand) if isinstance(item, dict) else condition(item, operand) for item in value
        )):
            return False
    return True


def matches(document, query):
    for key, expected in query.items():
        if key == "$jsonSchema":
            if not schema_matches(document, expected):
                return False
        elif key == "$expr":
            if not expression(expected, document):
                return False
        elif key == "$or":
            if not any(matches(document, item) for item in expected):
                return False
        elif key == "$and":
            if not all(matches(document, item) for item in expected):
                return False
        elif not condition(path_value(document, key), expected, exists=path_exists(document, key)):
            return False
    return True


def schema_matches(value, schema):
    """Small fake for query-only schema predicates; real MongoDB verifies semantics."""
    types = {"string": str, "array": list, "date": datetime}
    if "bsonType" in schema and not isinstance(value, types[schema["bsonType"]]):
        return False
    if "not" in schema and schema_matches(value, schema["not"]):
        return False
    if "pattern" in schema and not re.search(schema["pattern"].replace("(*UCP)", ""), value):
        return False
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
        return False
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            return False
        return all(schema_matches(value[key], child) for key, child in schema.get("properties", {}).items() if key in value)
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            return False
        if schema.get("uniqueItems") and len(set(map(repr, value))) != len(value):
            return False
        return all(schema_matches(item, schema.get("items", {})) for item in value)
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

    def apply_update(self, document, update):
        if isinstance(update, list):
            variables = {"NOW": mongo_now()}
            for stage in update:
                original = copy.deepcopy(document)
                if "$replaceWith" in stage:
                    replacement = expression(stage["$replaceWith"], original, variables)
                    document.clear()
                    document.update(replacement)
                else:
                    document.update({key: expression(value, original, variables) for key, value in stage["$set"].items()})
        else:
            document.update(copy.deepcopy(update.get("$set", {})))
            document.update({key: mongo_now() for key in update.get("$currentDate", {})})

    def with_options(self, **kwargs):
        if "write_concern" in kwargs:
            self.write_concern = kwargs["write_concern"]
        if "read_preference" in kwargs:
            self.read_preference = kwargs["read_preference"]
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
        result = next((copy.deepcopy(document) for document in self.documents if matches(document, query)), None)
        if result is not None:
            for key, value in kwargs.get("projection", {}).items():
                if isinstance(value, (str, dict)):
                    result[key] = expression(value, result)
        return result

    def find(self, query):
        return FakeCursor([document for document in self.documents if matches(document, query)])

    async def update_one(self, query, update, upsert=False, **kwargs):
        await asyncio.sleep(0)
        update_view = {"$set": {key: value for stage in update for key, value in stage.get("$set", {}).items()}} if isinstance(update, list) else update
        if self.fail_update and self.fail_update(update_view):
            raise AutoReconnect("simulated database failure")
        # No awaits after the match: mimic atomic single-document updates.
        self.db.writes += 1
        for document in self.documents:
            if matches(document, query):
                before = copy.deepcopy(document)
                self.apply_update(document, update)
                return SimpleNamespace(matched_count=1, modified_count=int(before != document))
        if upsert:
            self.documents.append(copy.deepcopy(update.get("$setOnInsert", {})))
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def find_one_and_update(self, query, update, **kwargs):
        await asyncio.sleep(0)
        for document in self.documents:
            if matches(document, query):
                self.db.writes += 1
                self.apply_update(document, update)
                return copy.deepcopy(document)
        return None

    async def aggregate(self, pipeline, **kwargs):
        self.last_pipeline = pipeline
        self.last_options = kwargs
        documents = copy.deepcopy(self.documents)
        for stage in pipeline:
            if "$match" in stage:
                documents = [document for document in documents if matches(document, stage["$match"])]
            elif "$set" in stage or "$replaceWith" in stage:
                for document in documents:
                    self.apply_update(document, [stage])
            elif "$sort" in stage:
                fields = list(stage["$sort"])
                documents.sort(key=lambda document: tuple(document[field] for field in fields))
            elif "$lookup" in stage:
                lookup = stage["$lookup"]
                for document in documents:
                    document[lookup["as"]] = [copy.deepcopy(other) for other in self.db[lookup["from"]].documents
                                               if equal(other[lookup["foreignField"]], document[lookup["localField"]])
                                               and all(matches(other, stage["$match"]) for stage in lookup.get("pipeline", []) if "$match" in stage)]
            elif "$limit" in stage:
                documents = documents[:stage["$limit"]]
            elif "$project" in stage:
                included = {key for key, value in stage["$project"].items() if value}
                documents = ([{key: document[key] for key in included if key in document} for document in documents] if included else
                             [{key: value for key, value in document.items() if key not in stage["$project"]} for document in documents])
            elif "$count" in stage:
                documents = [{stage["$count"]: len(documents)}] if documents else []
        return FakeCursor(documents)


class FakeDatabase:
    def __init__(self, orders):
        self.writes = 0
        self.collections = {"preprocess_orders": FakeCollection(self, orders)}
        self.collections["preprocess_orders"].name = "preprocess_orders"

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection(self)
            self.collections[name].name = name
        return self.collections[name]


class SimulatedCrash(BaseException):
    pass


def order(order_id="order-1", **changes):
    return {"_id": order_id, "state": "pending", "tester_gender": "female", "date_created": "2026-10-13T20:30:00Z",
            "phone": "+989120000000", **changes}


# Legacy metadata values: every case is eligible when state is pending.
GENDER_CASES = [
    ("missing", {}, True), ("empty", {"tester_gender": ""}, True),
    ("text", {"tester_gender": "female"}, True), ("space", {"tester_gender": " "}, True),
    ("null", {"tester_gender": None}, True), ("false", {"tester_gender": False}, True),
    ("true", {"tester_gender": True}, True), ("zero", {"tester_gender": 0}, True),
    ("number", {"tester_gender": 42}, True), ("object", {"tester_gender": {}}, True),
    ("empty_array", {"tester_gender": []}, True),
    ("array", {"tester_gender": [None, False, 0]}, True),
    ("text_array", {"tester_gender": ["female", "male"]}, True),
    ("array_with_empty", {"tester_gender": ["female", ""]}, True),
]

SHIPPING_CASES = [
    ("pickup", {"shipping_method": "تحویل حضوری"}, True),
    ("express", {"shipping_method": "ارسال فوری"}, True),
    ("post", {"shipping_method": "پست"}, True),
    ("courier", {"shipping_method": "پیک"}, True),
    ("missing", {}, True), ("null", {"shipping_method": None}, True),
    ("empty", {"shipping_method": ""}, True),
    ("trailing_space", {"shipping_method": "ارسال فوری "}, True),
    ("arabic_yeh", {"shipping_method": "تحویل حضوري"}, True),
    ("blocked_array", {"shipping_method": ["پست", "ارسال فوری"]}, True),
    ("other_array", {"shipping_method": ["پست"]}, True),
]


class TesterWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = self.start_patch("app.services.sms.faraz.urlopen", return_value=FakeHttpResponse())
        self.start_patch("socket.create_connection", side_effect=AssertionError("Network disabled"))
        self.start_patch("app.services.sms.faraz.load_dotenv")
        self.settings = worker.WorkerSettings(
            orders_collection="preprocess_orders",
            sms_enabled=True,
            selection_base_url="https://tester.example.com", token_secret="random-test-secret-" + "x" * 32,
            sms_template=SMS_TEMPLATE,
        )
        self.sms = FarazSmsClient(FarazSmsConfig(api_key="test-key", line_number="50001"))
        self.sms.send_simple = Mock(wraps=self.sms.send_simple)
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

    async def test_only_pending_business_scope_with_sms_sent_guard(self):
        self.db["preprocess_orders"].documents = [
            order("old", date_created="2001-01-01", tester_gender=""),
            order("pickup", shipping_method="تحویل حضوری"), order("express", shipping_method="ارسال فوری"),
            order("sent", sms_sent=True), order("production", state="production"),
            {"_id": "minimal", "state": "pending", "phone": "09120000000"},
        ]
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 4)
        changed = {item["_id"] for item in self.db["preprocess_orders"].documents if item.get("sms_sent") is True}
        self.assertEqual(changed, {"old", "pickup", "express", "minimal", "sent"})
        self.assertEqual(self.sms.send_simple.call_count, 4)
        await self.run_worker()
        self.assertEqual(self.sms.send_simple.call_count, 4)

    async def test_all_gender_types_and_purchased_product_are_ignored(self):
        documents = []
        for name, fields, _ in GENDER_CASES:
            document = order(name, product_id=["unrelated"])
            document.pop("tester_gender")
            documents.append({**document, **fields})
        self.db["preprocess_orders"].documents = documents
        await self.run_worker()
        expected = {name for name, _, eligible in GENDER_CASES if eligible}
        actual = {row["_id"] for row in documents if row.get("sms_sent") is True}
        self.assertEqual(actual, expected)
        await self.run_worker()
        self.assertEqual(self.sms.send_simple.call_count, len(expected))
        self.assertEqual({row["order_id"] for row in self.db[worker.SELECTIONS].documents}, expected)

    async def test_all_shipping_methods_remain_eligible(self):
        documents = [order(name, **fields) for name, fields, _ in SHIPPING_CASES]
        self.db["preprocess_orders"].documents = documents
        expected = {name for name, _, _ in SHIPPING_CASES}
        self.assertNotIn("shipping_method", worker.sms_order_filter(self.settings))
        result = await self.run_worker()
        self.assertEqual(result["accepted"], len(expected))
        self.assertEqual({row["_id"] for row in documents if row.get("sms_sent") is True}, expected)
        await self.run_worker()
        self.assertEqual(self.http.call_count, len(expected))

    async def test_due_retry_waits_for_pending_and_keeps_same_link(self):
        self.http.side_effect = TimeoutError("synthetic timeout")
        await self.run_worker()
        self.make_retry_due()
        before = copy.deepcopy(self.notification())
        self.http.side_effect = None
        source = self.db["preprocess_orders"].documents[0]
        for state in ("not_seen", "production", None, ["pending"]):
            source["state"] = state
            self.assertEqual((await self.run_worker())["eligible"], 0)
            self.assertEqual(self.notification(), before)
        self.http.assert_called_once()
        source.update(state="pending", shipping_method="ارسال فوری", date_created="2001", tester_gender="")
        await self.run_worker()
        self.assertEqual((self.notification()["status"], self.notification()["attempt_count"]), ("accepted", 2))
        self.assertEqual(self.http.call_args_list[0].args[0].data, self.http.call_args_list[1].args[0].data)

    async def test_legacy_retry_recovery_does_not_process_nonpending_orders(self):
        now = datetime.now(timezone.utc)
        statuses = ("failed", "submission_unknown", "sending", "exhausted")
        self.db["preprocess_orders"].documents = [order(status, state="production") for status in statuses]
        self.db[worker.NOTIFICATIONS].documents = [
            {"order_id": status, "status": status, "updated_at": now - timedelta(minutes=10),
             "lease_expires_at": now - timedelta(seconds=1), "attempt_count": 3 if status == "exhausted" else 1}
            for status in statuses
        ]
        before = copy.deepcopy(self.db[worker.NOTIFICATIONS].documents)
        summary = await self.run_worker()
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents, before)
        self.assertEqual(summary["exhausted_total"], 0)
        self.http.assert_not_called()

    async def test_state_changed_after_candidate_query_is_rechecked(self):
        original = worker.process_order
        async def change_shipping(*args, **kwargs):
            self.db["preprocess_orders"].documents[0]["state"] = "production"
            return await original(*args, **kwargs)
        with patch.object(worker, "process_order", side_effect=change_shipping):
            summary = await self.run_worker()
        self.assertEqual((summary["eligible"], summary["skipped"]), (1, 1))
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents, [])
        self.http.assert_not_called()

    async def test_state_changed_during_selection_preparation_consumes_no_attempt(self):
        original = worker.ensure_selection
        async def change_shipping(*args, **kwargs):
            link = await original(*args, **kwargs)
            self.db["preprocess_orders"].documents[0]["state"] = "not_seen"
            return link
        with patch.object(worker, "ensure_selection", side_effect=change_shipping):
            self.assertEqual((await self.run_worker())["skipped"], 1)
        self.assertEqual((self.notification()["status"], self.notification()["attempt_count"]), ("prepared", 0))
        self.http.assert_not_called()
        saved = copy.deepcopy(self.db[worker.SELECTIONS].documents)
        self.db["preprocess_orders"].documents[0]["state"] = "pending"
        await self.run_worker()
        self.assertEqual(self.db[worker.SELECTIONS].documents, saved)
        self.assertEqual(self.notification()["attempt_count"], 1)

    async def test_accepted_order_repair_survives_release(self):
        orders = self.db["preprocess_orders"]
        orders.fail_update = lambda update: "sms_sent" in update.get("$set", {})
        await self.run_worker()
        accepted_at = self.notification()["accepted_at"]
        orders.fail_update = None
        for state in ("not_seen", "production"):
            orders.documents[0]["state"] = state
            await self.run_worker()
        self.assertTrue(orders.documents[0]["sms_sent"])
        self.assertTrue(self.notification()["order_synced"])
        self.assertEqual(self.notification()["accepted_at"], accepted_at)
        self.http.assert_called_once()

    async def test_successful_send_still_syncs_order_if_state_changes_in_provider_call(self):
        def accepted_response(*args, **kwargs):
            self.db["preprocess_orders"].documents[0]["state"] = "production"
            return FakeHttpResponse()
        self.http.side_effect = accepted_response
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertTrue(self.notification()["order_synced"])
        self.assertTrue(self.db["preprocess_orders"].documents[0]["sms_sent"])

    async def test_retry_requires_pending_but_accepted_repair_survives_any_state(self):
        documents, notifications = [], []
        states = ("pending", "production", None, ["pending"])
        for status in ("submission_unknown", "accepted"):
            for index, state in enumerate(states):
                identity = f"{status}-{index}"
                documents.append(order(identity, state=state, tester_gender="", date_created="2001"))
                notifications.append({"order_id": identity, "status": status, "order_synced": False,
                                      "updated_at": datetime.now(timezone.utc) - timedelta(minutes=10)})
        self.db["preprocess_orders"].documents = documents
        self.db[worker.NOTIFICATIONS].documents = notifications
        result = await worker.recover_notifications(worker.worker_collections(self.db, self.settings),
                                                    worker.sms_order_filter(self.settings), 50, self.settings)
        self.assertEqual((result["recovered_retry"], result["repaired"]), (1, len(states)))
        for record, source in zip(notifications, documents):
            if record["order_id"].startswith("accepted-"):
                self.assertTrue(record["order_synced"])
                self.assertTrue(source["sms_sent"])
            elif source["state"] == "pending":
                self.assertEqual(record["status"], "retry_pending")
            else:
                self.assertEqual(record["status"], "submission_unknown")
        self.http.assert_not_called()

    async def test_rerun_does_not_duplicate_sms_notification_or_selection(self):
        await self.run_worker()
        await self.run_worker()
        self.sms.send_simple.assert_called_once()
        self.assertEqual(len(self.db[worker.NOTIFICATIONS].documents), 1)
        self.assertEqual(len(self.db[worker.SELECTIONS].documents), 1)

    async def test_selection_and_claim_exist_before_simple_sms_dispatch(self):
        def check_before_dispatch(request, **kwargs):
            selection = self.db[worker.SELECTIONS].documents[0]
            token = worker.token_for_order("order-1", self.settings.token_secret)
            self.assertEqual(selection["token_hash"], hashlib.sha256(token.encode("ascii")).hexdigest())
            self.assertEqual(self.notification()["status"], "sending")
            self.assertEqual(self.notification()["attempt_count"], 1)
            self.assertEqual(json.loads(request.data)["text"], self.settings.sms_template.format(full_name="مشتری",
                link=self.settings.selection_base_url + "/" + token,
            ))
            return FakeHttpResponse()

        self.http.side_effect = check_before_dispatch
        result = await self.run_worker()
        self.assertEqual(result["accepted"], 1)
        self.http.assert_called_once()

    async def test_simple_payload_preflight_failure_does_not_claim_or_write(self):
        with patch.object(self.sms, "build_simple_payload", side_effect=SmsValidationError("invalid payload")):
            with self.assertRaises(SmsValidationError):
                await self.run_worker()
        self.assertEqual(self.db.writes, 0)
        self.sms.send_simple.assert_not_called()

    async def test_two_concurrent_workers_claim_only_once(self):
        await asyncio.gather(self.run_worker(), self.run_worker())
        self.sms.send_simple.assert_called_once()
        self.assertEqual(len(self.db[worker.NOTIFICATIONS].documents), 1)
        self.assertEqual(len(self.db[worker.SELECTIONS].documents), 1)

    async def test_selection_creation_is_idempotent_and_does_not_store_raw_token(self):
        selections = self.db[worker.SELECTIONS]
        links = await asyncio.gather(*(worker.ensure_selection(selections, "order-1", self.settings) for _ in range(2)))
        self.assertEqual(links[0], links[1])
        self.assertEqual(len(selections.documents), 1)
        token = links[0].rsplit("/", 1)[1]
        self.assertRegex(token, r"^[A-Za-z0-9_-]{18}$")
        self.assertEqual(selections.documents[0]["token_hash"], hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertEqual(selections.documents[0]["token_version"], worker.TOKEN_VERSION)
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
        self.db["preprocess_orders"].documents.append(order("order-2"))
        with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_TOKEN_SECRET") as caught:
            await self.run_worker()
        self.assertNotIn(self.settings.token_secret, str(caught.exception))
        self.assertEqual(self.notification()["status"], "prepared")
        self.assertEqual(self.notification()["attempt_count"], 0)
        self.assertEqual(self.notification()["attempts"], [])
        self.assertEqual(len(self.db[worker.NOTIFICATIONS].documents), 1)
        self.assertNotIn("sms_sent", self.db["preprocess_orders"].documents[0])
        self.sms.send_simple.assert_not_called()

    async def test_token_secret_mismatch_preserves_pending_retry_and_can_resume_after_fix(self):
        original_settings = self.settings
        self.http.side_effect = TimeoutError("timeout")
        await self.run_worker()
        self.make_retry_due()
        before = copy.deepcopy(self.notification())
        self.settings = replace(self.settings, token_secret="changed-secret-" + "y" * 32)
        self.http.side_effect = None
        with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_TOKEN_SECRET"):
            await self.run_worker()
        self.assertEqual(self.notification(), before)
        self.assertEqual(self.sms.send_simple.call_count, 1)
        self.settings = original_settings
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertEqual(self.notification()["attempt_count"], 2)

    async def test_late_response_can_finalize_same_expired_attempt_without_resubmission(self):
        notifications = self.db[worker.NOTIFICATIONS]
        await worker.prepare_notification(notifications, "order-1")
        claim = await worker.claim_notification(notifications, "order-1", 15)
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await worker.recover_notifications(worker.worker_collections(self.db, self.settings), worker.order_filter(self.settings), 50, self.settings)
        self.assertEqual(self.notification()["status"], "retry_pending")
        self.assertTrue(await worker.finish_notification(notifications, claim, "accepted", provider_data=42))
        self.assertEqual(self.notification()["status"], "accepted")
        self.sms.send_simple.assert_not_called()

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
        self.sms.send_simple.assert_not_called()
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents, [])

    async def test_sms_disabled_is_read_only_without_secret_or_faraz_client(self):
        self.settings = replace(self.settings, sms_enabled=False, token_secret="", selection_base_url="")
        with patch.object(worker, "FarazSmsClient", side_effect=AssertionError("No SMS client needed")), \
             patch.object(worker, "token_for_order", side_effect=AssertionError("No token")):
            result = await worker.run_once(self.db, self.settings)
        self.assertTrue(result["read_only"])
        self.assertEqual(self.db.writes, 0)

    async def test_definite_provider_rejection_waits_for_retry(self):
        self.http.side_effect = HTTPError("https://provider.example", 422, "invalid", {}, io.BytesIO(b'{"status":"error"}'))
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        self.assertEqual(self.notification()["last_error_code"], "provider_rejected")
        await self.run_worker()
        self.sms.send_simple.assert_called_once()

    async def test_timeout_waits_for_retry(self):
        self.http.side_effect = TimeoutError("private transport details")
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        await self.run_worker()
        self.sms.send_simple.assert_called_once()

    async def test_http_500_waits_for_retry(self):
        self.http.side_effect = HTTPError("https://provider.example", 500, "server error", {}, io.BytesIO(b'failure'))
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        await self.run_worker()
        self.sms.send_simple.assert_called_once()

    async def test_malformed_success_response_waits_for_retry(self):
        self.http.return_value = FakeHttpResponse(201, b'{"status":"success","data":{"id":42}}')
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        await self.run_worker()
        self.sms.send_simple.assert_called_once()

    async def test_object_pending_approval_is_accepted_without_metadata_or_resend(self):
        self.http.return_value = FakeHttpResponse(200, json.dumps(simple_success_body()).encode())
        before = datetime.now(timezone.utc)
        with self.assertNoLogs(worker.logger.name, level="DEBUG"):
            result = await self.run_worker()
        after = datetime.now(timezone.utc)
        notification = copy.deepcopy(self.notification())
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(notification["status"], "accepted")
        self.assertEqual(notification["response_status_code"], 200)
        self.assertEqual(notification["provider_data"], 15870343)
        self.assertIs(type(notification["provider_data"]), int)
        self.assertTrue(notification["order_synced"])
        self.assertTrue(self.db["preprocess_orders"].documents[0]["sms_sent"])
        self.assertLessEqual(before, notification["accepted_at"])
        self.assertLessEqual(notification["accepted_at"], after)
        self.assertEqual(notification["accepted_at"].utcoffset(), timedelta(0))
        for key in ("error_code", "last_error_code", "next_retry_at", "lease_expires_at"):
            self.assertIsNone(notification[key])
        self.assertEqual(notification["attempt_count"], 1)
        attempt, = notification["attempts"]
        self.assertEqual((attempt["attempt_number"], attempt["result"], attempt["http_status"], attempt["error_code"]),
                         (1, "accepted", 200, None))
        self.assertLessEqual(attempt["started_at"], attempt["finished_at"])
        persisted = repr(self.db[worker.NOTIFICATIONS].documents + self.db[worker.SELECTIONS].documents)
        link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
        for private in ("metadata", "private-sms-text", "09120000000", "aB7xQ2mN9pR4tY6kL8",
                        "2026-09-21", "test-key", link, link.rsplit("/", 1)[1]):
            self.assertNotIn(private, persisted)
        await self.run_worker()
        self.assertEqual(self.notification(), notification)
        self.http.assert_called_once()

    async def test_unknown_object_state_then_known_acceptance_retries_same_message(self):
        unknown = simple_success_body()
        unknown["data"]["status"] = "sent"
        self.http.return_value = FakeHttpResponse(200, json.dumps(unknown).encode())
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        self.assertEqual(self.notification()["last_error_code"], "provider_outcome_unknown")
        self.assertIsNone(self.notification()["accepted_at"])
        self.assertNotIn("sms_sent", self.db["preprocess_orders"].documents[0])
        await self.run_worker()
        self.http.assert_called_once()
        self.make_retry_due()
        self.http.return_value = FakeHttpResponse(200, json.dumps(simple_success_body()).encode())
        await self.run_worker()
        notification = self.notification()
        self.assertEqual((notification["status"], notification["attempt_count"], notification["provider_data"]),
                         ("accepted", 2, 15870343))
        self.assertIsNone(notification["error_code"])
        self.assertIsNone(notification["last_error_code"])
        self.assertEqual([attempt["result"] for attempt in notification["attempts"]], ["retry_pending", "accepted"])
        self.assertEqual(self.http.call_args_list[0].args[0].data, self.http.call_args_list[1].args[0].data)
        self.assertEqual(len(self.db[worker.SELECTIONS].documents), 1)
        await self.run_worker()
        self.assertEqual(self.http.call_count, 2)

    async def test_object_acceptance_repairs_order_without_resending_or_resetting_acceptance(self):
        self.http.return_value = FakeHttpResponse(200, json.dumps(simple_success_body()).encode())
        orders = self.db["preprocess_orders"]
        orders.fail_update = lambda update: "sms_sent" in update.get("$set", {})
        await self.run_worker()
        accepted_at = self.notification()["accepted_at"]
        selection = copy.deepcopy(self.db[worker.SELECTIONS].documents)
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertFalse(self.notification()["order_synced"])
        self.assertNotIn("sms_sent", orders.documents[0])
        orders.fail_update = None
        self.assertEqual((await self.run_worker())["repaired"], 1)
        self.assertTrue(orders.documents[0]["sms_sent"])
        self.assertTrue(self.notification()["order_synced"])
        self.assertEqual(self.notification()["accepted_at"], accepted_at)
        self.assertEqual(self.db[worker.SELECTIONS].documents, selection)
        self.http.assert_called_once()

    async def test_crash_after_dispatch_waits_for_lease_then_retry_delay(self):
        self.sms.send_simple.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            await self.run_worker()
        self.assertEqual(self.notification()["status"], "sending")
        await self.run_worker()
        self.sms.send_simple.assert_called_once()
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        self.sms.send_simple.assert_called_once()

    async def test_database_failure_after_response_waits_for_lease_then_retry_delay(self):
        notifications = self.db[worker.NOTIFICATIONS]
        notifications.fail_update = lambda update: update.get("$set", {}).get("status") == "accepted"
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "sending")
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        notifications.fail_update = None
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        self.sms.send_simple.assert_called_once()

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
        self.sms.send_simple.assert_called_once()

    async def test_accepted_repair_is_independent_of_order_date(self):
        self.db["preprocess_orders"].documents = [order(date_created="2026-08-26T12:24:27.83444Z")]
        self.db[worker.NOTIFICATIONS].documents = [{"order_id": "order-1", "status": "accepted"}]
        await self.run_worker()
        self.assertTrue(self.db["preprocess_orders"].documents[0]["sms_sent"])
        self.sms.send_simple.assert_not_called()

    async def test_invalid_mobile_records_failure_and_continues(self):
        self.db["preprocess_orders"].documents = [order("bad", phone="invalid"), order("good")]
        result = await self.run_worker()
        self.assertEqual((result["failed"], result["accepted"]), (1, 1))
        self.sms.send_simple.assert_called_once()

    async def test_exact_simple_message_and_metadata_contains_no_secrets(self):
        self.http.return_value = FakeHttpResponse(201, b'{"status":"success","data":42,"messages":"private-token"}')
        with self.assertNoLogs(worker.logger.name, level="DEBUG"):
            await self.run_worker()
        arguments = self.sms.send_simple.call_args.kwargs
        self.assertEqual(set(arguments), {"recipient", "text"})
        token = worker.token_for_order("order-1", self.settings.token_secret)
        url = self.settings.selection_base_url + "/" + token
        self.assertEqual(arguments["recipient"], "09120000000")
        self.assertEqual(arguments["text"], SMS_TEMPLATE.format(full_name="مشتری", link=url))
        payload = json.loads(self.http.call_args.args[0].data)
        self.assertEqual(payload["text"], arguments["text"])
        self.assertEqual(payload["recipients"], ["09120000000"])
        self.assertEqual(self.http.call_args.args[0].full_url, "https://api.iranpayamak.com/ws/v1/sms/simple")
        persisted = repr(self.db[worker.NOTIFICATIONS].documents + self.db[worker.SELECTIONS].documents)
        for secret in (url, token, arguments["text"], "test-key", "private-token", "09120000000"):
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
        self.sms.send_simple.assert_not_called()

    async def test_new_order_index_options_and_hint_preserve_old_index(self):
        collections = worker.worker_collections(self.db, self.settings)
        old_index = {"key": [("product_id", 1), ("date_created", 1), ("_id", 1)]}
        collections["orders"].indexes["tester_product_created"] = copy.deepcopy(old_index)
        old_partial = {"key": [("date_created", 1), ("_id", 1)], "partialFilterExpression": {"tester_gender": {"$exists": True}}}
        collections["orders"].indexes["tester_gender_created"] = copy.deepcopy(old_partial)
        await worker.create_indexes(collections)
        self.assertEqual(collections["orders"].indexes["tester_product_created"], old_index)
        self.assertEqual(collections["orders"].indexes["tester_gender_created"], old_partial)
        index = collections["orders"].indexes[worker.ORDER_INDEX]
        self.assertEqual(index["key"], [("state", 1), ("_id", 1)])
        self.assertNotIn("partialFilterExpression", index)
        await worker.check_indexes(collections)
        await self.run_worker(dry_run=True)
        self.assertEqual(collections["orders"].last_options["hint"], worker.ORDER_INDEX)

    async def test_missing_or_incompatible_new_order_index_stops_before_writes(self):
        indexes = self.db["preprocess_orders"].indexes
        original = copy.deepcopy(indexes[worker.ORDER_INDEX])
        for change in (None, {"partialFilterExpression": {"tester_gender": "female"}},
                       {"key": [("date_created", 1), ("_id", 1)]}, {"sparse": True}, {"collation": {"locale": "en"}}):
            with self.subTest(change=change):
                indexes.pop(worker.ORDER_INDEX, None)
                if change is not None:
                    indexes[worker.ORDER_INDEX] = {**original, **change}
                with self.assertRaisesRegex(worker.WorkerConfigurationError, "create-indexes"):
                    await self.run_worker()
                self.assertEqual(self.db.writes, 0)
                self.sms.send_simple.assert_not_called()

    def make_retry_due(self):
        self.notification()["next_retry_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    async def test_provider_rejection_then_second_attempt_succeeds(self):
        self.http.side_effect = HTTPError("https://provider.example", 422, "invalid", {}, io.BytesIO(b'error'))
        await self.run_worker()
        self.assertEqual(self.notification()["attempt_count"], 1)
        self.assertNotIn("sms_sent", self.db["preprocess_orders"].documents[0])
        self.make_retry_due()
        self.http.side_effect = None
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertEqual(self.notification()["attempt_count"], 2)
        self.assertEqual([attempt["result"] for attempt in self.notification()["attempts"]], ["retry_pending", "accepted"])

    async def test_timeout_then_second_attempt_succeeds_with_same_link(self):
        self.http.side_effect = TimeoutError("private transport details")
        await self.run_worker()
        self.make_retry_due()
        self.http.side_effect = None
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        texts = [call.kwargs["text"] for call in self.sms.send_simple.call_args_list]
        link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
        self.assertEqual(texts, [self.settings.sms_template.format(full_name="مشتری", link=link)] * 2)
        self.assertEqual(self.http.call_args_list[0].args[0].data, self.http.call_args_list[1].args[0].data)
        self.assertEqual(len(self.db[worker.SELECTIONS].documents), 1)
        await self.run_worker()
        self.assertEqual(self.sms.send_simple.call_count, 2)

    async def test_personalized_retry_preserves_payload_and_does_not_store_or_log_message(self):
        source = self.db["preprocess_orders"].documents[0]
        source.update(first_name="  احمد ", last_name=" فرجی  ")
        self.http.side_effect = TimeoutError("private transport details")
        with self.assertNoLogs(worker.logger.name, level="INFO"):
            await self.run_worker()
            self.make_retry_due()
            self.http.side_effect = None
            await self.run_worker()
        first, second = [call.args[0].data for call in self.http.call_args_list]
        self.assertEqual(first, second)
        text = json.loads(first)["text"]
        link = self.settings.selection_base_url + "/" + worker.token_for_order("order-1", self.settings.token_secret)
        self.assertEqual(text, SMS_TEMPLATE.format(full_name="احمد فرجی", link=link))
        stored = repr(self.db[worker.NOTIFICATIONS].documents + self.db[worker.SELECTIONS].documents)
        for private in ("احمد", "فرجی", text, link, link.rsplit("/", 1)[1]):
            self.assertNotIn(private, stored)
        self.assertEqual(self.notification()["attempt_count"], 2)

    async def test_invalid_names_do_not_remove_pending_orders_from_sms_queue(self):
        self.db["preprocess_orders"].documents = [
            order("missing"), order("invalid", first_name=None, last_name=["invalid"]),
            order("partial", first_name="  احمد ", last_name=False),
        ]
        self.assertEqual((await self.run_worker())["accepted"], 3)
        texts = [call.kwargs["text"] for call in self.sms.send_simple.call_args_list]
        self.assertEqual(sum("مشتری عزیز" in text for text in texts), 2)
        self.assertEqual(sum("احمد عزیز" in text for text in texts), 1)

    async def test_retry_refreshes_changed_name_or_template_but_keeps_token_and_selection(self):
        initial_settings = self.settings
        for change in ("name", "template", "both"):
            with self.subTest(change=change):
                self.settings = initial_settings
                source = order(first_name=" احمد ", last_name=" فرجی ")
                self.db["preprocess_orders"].documents = [source]
                self.db[worker.NOTIFICATIONS].documents.clear()
                self.db[worker.SELECTIONS].documents.clear()
                self.http.reset_mock()
                self.http.side_effect = TimeoutError("synthetic timeout")
                await self.run_worker()
                selection_before = copy.deepcopy(self.db[worker.SELECTIONS].documents[0])
                self.make_retry_due()
                if change in {"name", "both"}:
                    source.update(first_name=" سارا ", last_name=" احمدی ")
                if change in {"template", "both"}:
                    with patch.dict(os.environ, {"TESTER_SMS_TEMPLATE": "{full_name} عزیز\\nانتخاب‌های شما: {link}"}), \
                            patch.object(worker, "load_dotenv"):
                        loaded = worker.WorkerSettings.from_environment()
                    self.settings = replace(self.settings, sms_template=loaded.sms_template)
                self.http.side_effect = None
                await self.run_worker()
                link = self.settings.selection_base_url + "/" + worker.token_for_order(source["_id"], self.settings.token_secret)
                texts = [json.loads(call.args[0].data)["text"] for call in self.http.call_args_list]
                self.assertEqual(texts[0], SMS_TEMPLATE.format(full_name="احمد فرجی", link=link))
                expected_name = "سارا احمدی" if change in {"name", "both"} else "احمد فرجی"
                self.assertEqual(texts[1], self.settings.sms_template.format(full_name=expected_name, link=link))
                self.assertNotEqual(*texts)
                self.assertTrue(all(link in text for text in texts))
                self.assertEqual(self.db[worker.SELECTIONS].documents, [selection_before])
                self.assertEqual((self.notification()["attempt_count"], self.notification()["status"]), (2, "accepted"))
                await self.run_worker()
                self.assertEqual(self.http.call_count, 2)

    async def test_bad_template_stops_before_claim_without_permanent_order_failure(self):
        self.settings = replace(self.settings, sms_template="private-template {unknown}")
        with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_SMS_TEMPLATE") as error:
            await self.run_worker()
        self.assertNotIn("private-template", str(error.exception))
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents, [])
        self.http.assert_not_called()

    async def test_connection_failure_then_retry(self):
        self.http.side_effect = URLError("private network details")
        await self.run_worker()
        self.make_retry_due()
        self.http.side_effect = None
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertEqual(self.notification()["attempt_count"], 2)

    async def test_retry_exhaustion_stops_and_is_reported(self):
        self.http.side_effect = TimeoutError("timeout")
        for _ in range(3):
            await self.run_worker()
            self.make_retry_due()
        result = await self.run_worker()
        self.assertEqual(self.sms.send_simple.call_count, 3)
        self.assertEqual(self.notification()["status"], "exhausted")
        self.assertEqual(result["exhausted_total"], 1)
        report = await worker.exhausted_report(worker.worker_collections(self.db, self.settings), worker.order_filter(self.settings), 10)
        self.assertEqual(report["orders"][0]["order_id"], "order-1")
        self.assertEqual(len(self.notification()["attempts"]), 3)

    async def test_global_401_403_429_stop_run_but_preserve_retry(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                self.db = FakeDatabase([order("order-1"), order("order-2")])
                await worker.create_indexes(worker.worker_collections(self.db, self.settings))
                self.sms.send_simple.reset_mock()
                self.http.side_effect = HTTPError("https://provider.example", code, "private error", {}, io.BytesIO(b'error'))
                with self.assertLogs(worker.logger.name, level="WARNING") as captured:
                    result = await self.run_worker()
                self.assertEqual(result["halted_http_status"], code)
                self.sms.send_simple.assert_called_once()
                self.assertEqual(len(self.db[worker.NOTIFICATIONS].documents), 1)
                self.assertEqual(self.notification()["status"], "retry_pending")
                self.assertNotIn("private error", "".join(captured.output))

    async def test_crashed_send_retries_after_expiry_and_due_time(self):
        self.sms.send_simple.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            await self.run_worker()
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "retry_pending")
        self.assertEqual(self.sms.send_simple.call_count, 1)
        self.make_retry_due()
        self.sms.send_simple.side_effect = None
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertEqual(self.notification()["attempt_count"], 2)

    async def test_mongodb_failure_after_success_can_retry(self):
        notifications = self.db[worker.NOTIFICATIONS]
        notifications.fail_update = lambda update: update.get("$set", {}).get("status") == "accepted"
        await self.run_worker()
        notifications.fail_update = None
        self.notification()["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.run_worker()
        self.make_retry_due()
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "accepted")
        self.assertEqual(self.sms.send_simple.call_count, 2)

    async def test_mongodb_failure_before_claim_does_not_consume_attempt(self):
        with patch.object(worker, "claim_notification", new=AsyncMock(side_effect=AutoReconnect("offline"))):
            await self.run_worker()
        self.assertEqual(self.notification()["attempt_count"], 0)
        self.sms.send_simple.assert_not_called()
        await self.run_worker()
        self.assertEqual(self.notification()["attempt_count"], 1)

    async def test_two_workers_claim_only_one_due_retry(self):
        self.http.side_effect = TimeoutError("timeout")
        await self.run_worker()
        self.make_retry_due()
        self.http.side_effect = None
        await asyncio.gather(self.run_worker(), self.run_worker())
        self.assertEqual(self.sms.send_simple.call_count, 2)
        self.assertEqual(self.notification()["attempt_count"], 2)

    async def test_old_attempt_cannot_overwrite_new_attempt_or_sync_order(self):
        notifications = self.db[worker.NOTIFICATIONS]
        await worker.prepare_notification(notifications, "order-1")
        first = await worker.claim_notification(notifications, "order-1", 15, 3)
        await worker.schedule_retry(notifications, first, self.settings, "timeout")
        self.make_retry_due()
        second = await worker.claim_notification(notifications, "order-1", 15, 3)
        self.assertFalse(await worker.finish_notification(notifications, first, "accepted", provider_data=42))
        self.assertEqual(self.notification()["attempt_id"], second["attempt_id"])
        self.assertEqual(self.notification()["status"], "sending")
        self.assertNotIn("sms_sent", self.db["preprocess_orders"].documents[0])

    async def test_renewed_lease_prevents_stale_recovery_update(self):
        notifications = self.db[worker.NOTIFICATIONS]
        await worker.prepare_notification(notifications, "order-1")
        claim = await worker.claim_notification(notifications, "order-1", 15)
        self.assertFalse(await worker.finish_notification(notifications, claim, "retry_pending",
                                                         expired_at=datetime.now(timezone.utc), error_code="crash"))
        self.assertEqual(self.notification()["status"], "sending")

    async def test_legacy_provider_failure_recovers_but_permanent_data_failure_stays_failed(self):
        self.db["preprocess_orders"].documents = [order("retry"), order("bad"), order("old", date_created="2026-01-01T00:00:00Z", state="production")]
        self.db[worker.NOTIFICATIONS].documents = [
            {"order_id": "retry", "status": "failed", "error_code": "provider_rejected", "updated_at": datetime.now(timezone.utc) - timedelta(minutes=10)},
            {"order_id": "bad", "status": "failed", "error_code": "invalid_order_data"},
            {"order_id": "old", "status": "submission_unknown"},
        ]
        before_old = copy.deepcopy(self.db[worker.NOTIFICATIONS].documents[2])
        await self.run_worker()
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents[0]["status"], "accepted")
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents[0]["attempt_count"], 2)
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents[1]["status"], "failed")
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents[2], before_old)

    async def test_permanent_failed_is_not_recovered_if_either_error_field_marks_invalid_data(self):
        variants = [
            {"error_code": "invalid_order_data"},
            {"last_error_code": "invalid_order_data"},
            {"error_code": "provider_rejected", "last_error_code": "invalid_order_data"},
            {"error_code": "invalid_order_data", "last_error_code": "provider_rejected"},
            {"error_code": None, "last_error_code": "invalid_order_data"},
        ]
        self.db["preprocess_orders"].documents = [order(f"bad-{index}") for index in range(len(variants))]
        self.db[worker.NOTIFICATIONS].documents = [
            {"order_id": f"bad-{index}", "status": "failed", **fields} for index, fields in enumerate(variants)
        ]
        before = copy.deepcopy(self.db[worker.NOTIFICATIONS].documents)
        await self.run_worker()
        self.assertEqual(self.db[worker.NOTIFICATIONS].documents, before)
        self.sms.send_simple.assert_not_called()

    async def test_permanent_error_after_claim_sets_both_codes_and_never_retries(self):
        self.sms.send_simple.side_effect = SmsValidationError("invalid order")
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "failed")
        self.assertEqual(self.notification()["error_code"], "invalid_order_data")
        self.assertEqual(self.notification()["last_error_code"], "invalid_order_data")
        await self.run_worker()
        self.sms.send_simple.assert_called_once()

    async def test_error_fields_match_across_retry_recovery_and_success(self):
        self.http.side_effect = TimeoutError("timeout")
        await self.run_worker()
        self.assertEqual(self.notification()["error_code"], self.notification()["last_error_code"])
        self.make_retry_due()
        self.http.side_effect = None
        await self.run_worker()
        self.assertIsNone(self.notification()["error_code"])
        self.assertIsNone(self.notification()["last_error_code"])
        self.db["preprocess_orders"].documents.append(order("legacy"))
        self.db[worker.NOTIFICATIONS].documents.append({
            "order_id": "legacy", "status": "failed", "last_error_code": "provider_rejected",
            "updated_at": datetime.now(timezone.utc),
        })
        await self.run_worker()
        legacy = self.db[worker.NOTIFICATIONS].documents[1]
        self.assertEqual(legacy["status"], "retry_pending")
        self.assertEqual(legacy["error_code"], "provider_rejected")
        self.assertEqual(legacy["last_error_code"], "provider_rejected")

    async def test_lowered_max_attempts_marks_retry_exhausted(self):
        self.http.side_effect = TimeoutError("timeout")
        await self.run_worker()
        self.settings = replace(self.settings, max_attempts=1)
        await self.run_worker()
        self.assertEqual(self.notification()["status"], "exhausted")
        self.sms.send_simple.assert_called_once()


class WorkerSettingsAndTokenTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(worker, "load_dotenv")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.environment = {"TESTER_SMS_TEMPLATE": SMS_TEMPLATE}

    def test_removed_cutoff_is_not_required_or_validated(self):
        for env in ({}, {"TESTER_SMS_START_AT": "invalid"}, {"TESTER_SMS_START_AT": "2001-01-01"}):
            with patch.dict(os.environ, env, clear=True):
                settings = worker.WorkerSettings.from_environment()
            self.assertFalse(hasattr(settings, "start_at"))
            self.assertFalse(settings.sms_enabled)

    def test_message_template_preserves_exact_text_and_case_sensitive_example_link(self):
        link = "https://domain.com/aB7xQ2mN9pR4tY6kL8"
        settings = worker.WorkerSettings("preprocess_orders", sms_template=SMS_TEMPLATE)
        self.assertEqual(worker.render_sms(settings, {"first_name": "  احمد ", "last_name": " فرجی  "}, link),
                         "راستی اینو یادم رفتتتت\n\n"
                         "احمد فرجی عزیز، اون عطرهایی که می‌گفتی «باید امتحان کنم» کدوما بودن؟\n\n"
                         "تسترهای دلخواهت رو تا نیم ساعت دیگه اینجا انتخاب کن که همراه سفارشت برات بفرستیم:\n\n"
                         "https://domain.com/aB7xQ2mN9pR4tY6kL8\n\nعطر سراد")

    def test_obsolete_pattern_environment_does_not_affect_worker_settings(self):
        with patch.dict(os.environ, {**self.environment, "TESTER_PATTERN_LINK_ATTRIBUTE": "invalid old value"}, clear=True):
            settings = worker.WorkerSettings.from_environment()
        self.assertFalse(hasattr(settings, "link_attribute"))

    def test_window_setting_accepts_only_positive_integers_and_has_shared_duration(self):
        for value in ("", "0", "-1", "+1", "1.5", "true", "۱", "1_0", "9" * 30):
            with self.subTest(value=value), patch.dict(os.environ, {"TESTER_SELECTION_WINDOW_MINUTES": value}, clear=True):
                with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_SELECTION_WINDOW_MINUTES"):
                    worker.WorkerSettings.from_environment()
        for value in (True, False, 0, -1, 1.5, "30"):
            with self.subTest(value=value), self.assertRaises(worker.WorkerConfigurationError):
                worker.WorkerSettings("orders", selection_window_minutes=value)
        with patch.dict(os.environ, {"TESTER_SELECTION_WINDOW_MINUTES": "7"}, clear=True):
            settings = worker.WorkerSettings.from_environment()
        self.assertEqual(settings.selection_window, timedelta(minutes=7))

    def test_template_rejects_unknown_traversal_conversion_and_invalid_format(self):
        for template in ("", " ", "missing-link", "{unknown}{link}", "{link", "{link}}", "{}{link}",
                         "{0}{link}", "{link.__class__}", "{link[0]}", "{link!r}", "{link:10}",
                         "{link:{full_name}}", "{{link}}", "{link}", "{full_name}",
                         "{{full_name}} {link}", "{full_name} {{link}}"):
            with self.subTest(template=template), self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_SMS_TEMPLATE"):
                worker.validate_sms_template(template)
        worker.validate_sms_template("{{literal braces}} {full_name}\n{link}")

    def test_env_and_dotenv_templates_decode_newlines_without_damaging_persian(self):
        from dotenv import load_dotenv
        template = "{full_name} عزیز\\n\\n{link}\\nسراد"
        with patch.dict(os.environ, {"TESTER_SMS_TEMPLATE": template}, clear=True):
            settings = worker.WorkerSettings.from_environment()
        self.assertEqual(settings.sms_template, "{full_name} عزیز\n\n{link}\nسراد")
        folder = Path(__file__).resolve().parents[1] / "test-artifacts"
        folder.mkdir(exist_ok=True)
        path = folder / ("dotenv-template-" + uuid4().hex + ".env")
        try:
            path.write_text('TESTER_SELECTION_WINDOW_MINUTES=12\nTESTER_SMS_TEMPLATE="' + template + '"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(worker, "ENV_FILE", path), \
                    patch.object(worker, "load_dotenv", wraps=load_dotenv):
                settings = worker.WorkerSettings.from_environment()
            self.assertEqual(settings.selection_window_minutes, 12)
            self.assertEqual(settings.sms_template, "{full_name} عزیز\n\n{link}\nسراد")
        finally:
            path.unlink(missing_ok=True)

    def test_name_fallback_uses_only_valid_trimmed_name_parts(self):
        cases = [({}, "مشتری"), ({"first_name": "  احمد ", "last_name": " فرجی  "}, "احمد فرجی"),
                 ({"first_name": None, "last_name": " فرجی "}, "فرجی"),
                 ({"first_name": 12, "last_name": False}, "مشتری"),
                 ({"first_name": "None", "last_name": "123"}, "مشتری"),
                 ({"first_name": "bad\nname", "last_name": {}}, "مشتری"),
                 ({"first_name": "   ", "last_name": "علی‌زاده"}, "علی‌زاده")]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(worker.sms_full_name(source), expected)

    def test_configuration_error_is_reported_by_cli_without_secret_values(self):
        message = "TESTER_TOKEN_SECRET is incompatible with the existing Selection token hash/version; restore the original secret."
        stderr = io.StringIO()
        with patch("sys.argv", ["tester_sms", "--once"]), patch("sys.stderr", stderr), \
             patch.object(worker, "run_command", new=AsyncMock(side_effect=worker.WorkerConfigurationError(message))):
            self.assertEqual(worker.main(), 1)
        self.assertIn("TESTER_TOKEN_SECRET", stderr.getvalue())
        self.assertIn("Worker configuration error", stderr.getvalue())

    def test_cli_release_errors_return_nonzero_with_json_and_safe_log(self):
        summary = {"release_errors": 2, "accepted": 1, "released_expired": 3}
        output = io.StringIO()
        with patch("sys.argv", ["tester_sms", "--once"]), patch("sys.stdout", output), \
                patch.object(worker, "run_command", new=AsyncMock(return_value=summary)), \
                self.assertLogs(worker.logger.name, level="ERROR") as logs:
            self.assertEqual(worker.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), summary)
        self.assertIn("2 error(s)", "".join(logs.output))

    def test_cli_success_and_existing_http_failure_keep_exit_codes(self):
        cases = [({"release_errors": 0, "accepted": 1}, 0), ({"indexes": "ready"}, 0),
                 ({"release_errors": 0, "halted_http_status": 429}, 1)]
        for summary, exit_code in cases:
            output = io.StringIO()
            with self.subTest(summary=summary), patch("sys.argv", ["tester_sms", "--once"]), \
                    patch("sys.stdout", output), self.assertNoLogs(worker.logger.name, level="ERROR"), \
                    patch.object(worker, "run_command", new=AsyncMock(return_value=summary)):
                self.assertEqual(worker.main(), exit_code)
            self.assertEqual(json.loads(output.getvalue()), summary)

    def test_missing_or_invalid_settings_fail_with_setting_name(self):
        for key, invalid in (("TESTER_WORKER_BATCH_SIZE", "0"),
                             ("TESTER_SMS_ENABLED", "yes")):
            with self.subTest(key=key, invalid=invalid), patch.dict(os.environ, {**self.environment, key: invalid}, clear=True):
                with self.assertRaisesRegex(worker.WorkerConfigurationError, key):
                    worker.WorkerSettings.from_environment()

    def test_enabled_delivery_requires_url_and_long_secret(self):
        env = {**self.environment, "TESTER_SMS_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_SELECTION_BASE_URL"):
            worker.WorkerSettings.from_environment(require_delivery=True)
        with patch.dict(os.environ, {**env, "TESTER_SELECTION_BASE_URL": "https://tester.example.com"}, clear=True), \
             self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_TOKEN_SECRET"):
            worker.WorkerSettings.from_environment(require_delivery=True)

    def test_selection_base_url_is_origin_only_and_normalizes_trailing_slash(self):
        env = {**self.environment, "TESTER_SMS_ENABLED": "true", "TESTER_TOKEN_SECRET": "x" * 48}
        for base in ("https://tester.example.com", "https://tester.example.com/", "http://localhost:8000/"):
            with self.subTest(base=base), patch.dict(os.environ, {**env, "TESTER_SELECTION_BASE_URL": base}, clear=True):
                settings = worker.WorkerSettings.from_environment(require_delivery=True)
                self.assertEqual(settings.selection_base_url, base.rstrip("/"))
        for path in ("/s", "/other", "/nested/path", "/?query=x", "/#fragment"):
            with self.subTest(path=path), patch.dict(os.environ, {**env, "TESTER_SELECTION_BASE_URL": "https://tester.example.com" + path}, clear=True):
                with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_SELECTION_BASE_URL"):
                    worker.WorkerSettings.from_environment(require_delivery=True)

    def test_typed_identity_and_secret_affect_deterministic_18_character_urlsafe_token(self):
        secret = "a" * 48
        identifier = ObjectId("000000000000000000000001")
        token = worker.token_for_order(identifier, secret)
        self.assertIsInstance(token, str)
        self.assertRegex(token, r"^[A-Za-z0-9_-]{18}$")
        self.assertEqual(token, worker.token_for_order(identifier, secret))
        self.assertNotEqual(token, worker.token_for_order(str(identifier), secret))
        self.assertNotEqual(token, worker.token_for_order(identifier, "b" * 48))

    def test_urlsafe_token_fixed_hmac_vector_preserves_case(self):
        token = worker.token_for_order(ObjectId("000000000000000000000001"), "a" * 48)
        self.assertEqual(token, "3BEqKw7eBb7orHJMXi")
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 18)
        self.assertNotIn("=", token)

    def test_urlsafe_tokens_are_unique_and_cover_the_full_alphabet_for_ten_thousand_orders(self):
        tokens = [worker.token_for_order(ObjectId(f"{i:024x}"), "a" * 48) for i in range(10000)]
        self.assertEqual(len(set(tokens)), len(tokens))
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_-]{18}", token) for token in tokens))
        self.assertEqual(set("".join(tokens)), set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"))
        self.assertNotEqual(tokens, sorted(tokens))

    def test_product_id_environment_is_no_longer_required_or_used(self):
        for value in (None, "", "any-product"):
            env = dict(self.environment)
            if value is not None:
                env["TESTER_PACK_PRODUCT_ID"] = value
            with self.subTest(value=value), patch.dict(os.environ, env, clear=True):
                settings = worker.WorkerSettings.from_environment()
            self.assertFalse(hasattr(settings, "product_id"))
            match = worker.order_filter(settings)
            self.assertNotIn("product_id", match)
            self.assertNotIn("tester_gender", match)
            self.assertEqual(match["state"], "pending")

    def test_retry_settings_accept_only_positive_integers(self):
        for key in ("TESTER_SMS_RETRY_DELAY_MINUTES", "TESTER_SMS_MAX_ATTEMPTS", "TESTER_RELEASE_BATCH_SIZE"):
            for value in ("", "0", "-1", "+1", "1.5", "true", "1_000"):
                with self.subTest(key=key, value=value), patch.dict(os.environ, {**self.environment, key: value}, clear=True):
                    with self.assertRaisesRegex(worker.WorkerConfigurationError, key):
                        worker.WorkerSettings.from_environment()
        with patch.dict(os.environ, {**self.environment, "TESTER_SMS_RETRY_DELAY_MINUTES": "7", "TESTER_SMS_MAX_ATTEMPTS": "4"}, clear=True):
            settings = worker.WorkerSettings.from_environment()
        self.assertEqual((settings.retry_delay_minutes, settings.max_attempts), (7, 4))

    def test_release_settings_default_to_sms_mode_and_can_be_overridden(self):
        for value, expected in (("", None), ("true", True), ("false", False)):
            with patch.dict(os.environ, {**self.environment, "TESTER_RELEASE_ENABLED": value,
                                         "TESTER_RELEASE_BATCH_SIZE": "7"}, clear=True):
                settings = worker.WorkerSettings.from_environment()
            self.assertIs(settings.release_enabled, expected)
            self.assertEqual(settings.release_batch_size, 7)
        with patch.dict(os.environ, {**self.environment, "TESTER_RELEASE_ENABLED": "yes"}, clear=True):
            with self.assertRaisesRegex(worker.WorkerConfigurationError, "TESTER_RELEASE_ENABLED"):
                worker.WorkerSettings.from_environment()

    def test_only_exact_scalar_pending_is_eligible_regardless_of_date(self):
        settings = worker.WorkerSettings("preprocess_orders")
        match = worker.order_filter(settings)
        for state in ("pending", "Pending", "pending ", "not_seen", None, 0, True, ["pending"], {}):
            with self.subTest(state=state):
                self.assertEqual(matches(order(state=state), match), state == "pending")
        self.assertFalse(matches({"_id": "missing"}, match))
        for timestamp in ("2001", "invalid", None, False, [], "2026-10-14T00:00:00+03:30"):
            self.assertTrue(matches(order(date_created=timestamp), match))
        self.assertTrue(matches({"state": "pending"}, match))

if __name__ == "__main__":
    unittest.main()
