"""Real application routing and privacy headers; no database/network calls."""

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.main import app
from app.selection_security import RedactSelectionAccessLog
from tests.test_selections import request


class SelectionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_token_page_and_refresh_have_private_headers_and_absolute_assets(self):
        token = "Ab09_-" * 4
        for _ in range(2):
            status, body, headers = await request(app, "/" + token, method="GET")
            self.assertEqual(status, 200)
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(headers["referrer-policy"], "no-referrer")
            self.assertIn("noindex", headers["x-robots-tag"])
            self.assertIn(b'/frontend/app.js', body)
            self.assertIn(b'/frontend/styles.css', body)
            self.assertNotIn(token.encode(), body)

    async def test_invalid_or_old_paths_do_not_open_the_selection_page(self):
        for path in ("/short", "/" + "A" * 23, "/" + "A" * 25, "/" + "." * 24, "/s/" + "A" * 24, "/foo/bar"):
            with self.subTest(path=path):
                self.assertEqual((await request(app, path, method="GET"))[0], 404)

    async def test_static_routes_and_api_are_not_captured_by_token_route(self):
        self.assertEqual((await request(app, "/health", method="GET"))[:2], (200, {"status": "ok"}))
        for path in ("/", "/frontend/app.js", "/frontend/styles.css", "/frontend/logo.webp", "/docs", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual((await request(app, path, method="GET"))[0], 200)
        for path in ("/api", "/api/", "/api/not-found", "/frontend/not-found.css"):
            self.assertEqual((await request(app, path, method="GET"))[0], 404)
        self.assertEqual((await request(app, "/frontend", method="GET"))[0], 307)
        self.assertEqual((await request(app, "/api/tester/selection/context", {"token": "bad"}))[0], 400)
        # Exercise the actual catalog route without connecting to MongoDB.
        sentinel = object()
        original = getattr(app.state, "catalog", sentinel)
        app.state.catalog = SimpleNamespace(get=AsyncMock(), encoded=b'{"products":[],"total":0}')
        try:
            self.assertEqual((await request(app, "/api/tester/catalog", method="GET"))[:2], (200, {"products": [], "total": 0}))
        finally:
            if original is sentinel:
                del app.state.catalog
            else:
                app.state.catalog = original

    async def test_token_redirect_and_wrong_method_are_also_private(self):
        token = "A" * 24
        for method, path, expected in (("GET", f"/{token}/", 307), ("POST", f"/{token}", 405)):
            status, _, headers = await request(app, path, method=method)
            self.assertEqual(status, expected)
            self.assertEqual(headers["cache-control"], "no-store")

    def test_access_log_redacts_root_tokens_encoded_tokens_and_query_but_preserves_static_paths(self):
        token = "A" * 24
        for target in ("/" + token, "/" + token + "/?x=secret", "/%41" + "A" * 23 + "?x=secret"):
            record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                       ("127.0.0.1", "GET", target, "1.1", 200), None)
            RedactSelectionAccessLog().filter(record)
            self.assertIn("/[redacted]", record.getMessage())
            self.assertNotIn(token, record.getMessage())
            self.assertNotIn("secret", record.getMessage())
        for target in ("/health", "/api/tester/catalog", "/frontend/app.js"):
            record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                       ("127.0.0.1", "GET", target, "1.1", 200), None)
            RedactSelectionAccessLog().filter(record)
            self.assertEqual(record.args[2], target)
