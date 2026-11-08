"""Count parsing and the shared API/HTML/worker contract, without network calls."""
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import main, selections
from app.selection_config import read_selection_count
from app.workers import tester_sms as worker
from tests.test_selections import request, settings


class SelectionCountTests(unittest.TestCase):
    def test_default_and_custom_counts(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(read_selection_count(), 6)
        for count in (1, 4, 6, 10, 498):
            with self.subTest(count=count), patch.dict(os.environ, TESTER_SELECTION_COUNT=str(count)):
                self.assertEqual(read_selection_count(), count)

    def test_invalid_counts_fail_instead_of_falling_back(self):
        for raw in ("", "0", "-1", "499", "1.5", "six", "6 ", " 6", "۶", "true", "0006"):
            with self.subTest(raw=raw), patch.dict(os.environ, TESTER_SELECTION_COUNT=raw):
                with self.assertRaisesRegex(ValueError, "TESTER_SELECTION_COUNT"):
                    read_selection_count()

    def test_api_accepts_only_configured_count_and_reports_it_in_errors(self):
        for count in (4, 6, 10):
            with self.subTest(count=count), patch.object(selections, "COUNT", count):
                pids = [str(i) for i in range(count)]
                self.assertEqual(selections.validate_pids(pids), pids)
                for invalid in (pids[:-1], pids + ["extra"]):
                    with self.assertRaises(HTTPException) as error:
                        selections.validate_pids(invalid)
                    self.assertEqual(error.exception.detail["code"], "invalid_count")
                    self.assertIn(str(count).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")), error.exception.detail["message"])

    def test_worker_receipt_schema_uses_configured_count(self):
        import json
        for count in (4, 6, 10):
            with self.subTest(count=count), patch.object(selections, "COUNT", count):
                pipeline = json.dumps(worker.release_pipeline(settings(), limit=5))
                self.assertIn(f'"minItems": {count}', pipeline)
                self.assertIn(f'"maxItems": {count}', pipeline)


class SelectionCountPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_page_routes_render_count_and_frontend_config(self):
        for count in (4, 6, 10):
            with self.subTest(count=count), patch.object(main, "TESTER_SELECTION_COUNT", count):
                for path in ("/", "/Ab09_-Ab09_-Ab09_-", "/frontend/index.html"):
                    status, body, headers = await request(main.app, path, method="GET")
                    self.assertEqual(status, 200)
                    html = body.decode("utf-8")
                    self.assertIn(f'name="tester-selection-count" content="{count}"', html)
                    self.assertIn(f'aria-valuemax="{count}"', html)
                    self.assertNotIn("__TESTER_COUNT", html)
                    self.assertEqual(headers["cache-control"], "no-store")
