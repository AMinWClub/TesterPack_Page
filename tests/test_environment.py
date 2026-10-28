import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.environment import read_environment
from app.workers import tester_sms as worker


ENV = {
    "MONGODB_URI": "mongodb://localhost:27017",
    "MONGODB_DB_NAME_LOCAL": "test_local",
    "MONGODB_DB_NAME_PRODUCTION": "test_production",
    "TESTER_SELECTION_BASE_URL_LOCAL": "http://localhost:8000/",
    "TESTER_SELECTION_BASE_URL_PRODUCTION": "https://tester.example.com/",
}


class EnvironmentTests(unittest.TestCase):
    def test_both_environments_and_safe_default(self):
        for name, database, url in ((None, "test_local", "http://localhost:8000"),
                                    ("local", "test_local", "http://localhost:8000"),
                                    ("prod", "test_production", "https://tester.example.com")):
            values = {**ENV, **({"APP_ENV": name} if name else {})}
            with self.subTest(name=name), patch.dict(os.environ, values, clear=True):
                selected = read_environment()
                self.assertEqual((selected.mongodb_db_name, selected.selection_base_url), (database, url))

    def test_invalid_or_missing_settings_never_fall_back(self):
        for changes, message in (({"APP_ENV": "production"}, "APP_ENV"),
                                 ({"APP_ENV": ""}, "APP_ENV"),
                                 ({"MONGODB_DB_NAME_LOCAL": ""}, "MONGODB_DB_NAME_LOCAL"),
                                 ({"TESTER_SELECTION_BASE_URL_LOCAL": ""}, "TESTER_SELECTION_BASE_URL_LOCAL"),
                                 ({"APP_ENV": "prod", "MONGODB_DB_NAME_PRODUCTION": ""}, "MONGODB_DB_NAME_PRODUCTION"),
                                 ({"APP_ENV": "prod", "TESTER_SELECTION_BASE_URL_PRODUCTION": "https://test/path"}, "TESTER_SELECTION_BASE_URL_PRODUCTION")):
            with self.subTest(changes=changes), patch.dict(os.environ, {**ENV, **changes, "MONGODB_DB_NAME": "legacy"}, clear=True):
                with self.assertRaisesRegex(ValueError, message):
                    read_environment()

    def test_worker_uses_selected_origin(self):
        with patch.dict(os.environ, {**ENV, "APP_ENV": "prod"}, clear=True), patch.object(worker, "load_dotenv"):
            self.assertEqual(worker.WorkerSettings.from_environment().selection_base_url, "https://tester.example.com")

    def test_fastapi_and_sms_cli_select_same_environment_without_network(self):
        code = '''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import app.main as main
from app.workers import tester_sms as worker
import os
expected = os.environ["EXPECTED_DATABASE"]
async def check():
    client = MagicMock()
    client.admin.command = AsyncMock()
    client.close = AsyncMock()
    with patch.object(main, "AsyncMongoClient", return_value=client):
        async with main.lifespan(main.app):
            client.__getitem__.assert_called_with(expected)
    client = MagicMock()
    client.admin.command = AsyncMock()
    client.close = AsyncMock()
    args = SimpleNamespace(create_indexes=False, report_exhausted=False, limit=None, dry_run=True)
    with patch.object(worker, "AsyncMongoClient", return_value=client), patch.object(worker, "run_once", new=AsyncMock(return_value={})):
        await worker.run_command(args)
        client.__getitem__.assert_called_with(expected)
asyncio.run(check())
'''
        for name, database in (("local", "test_local"), ("prod", "test_production")):
            with self.subTest(name=name):
                result = subprocess.run([sys.executable, "-c", code], env={**os.environ, **ENV,
                    "APP_ENV": name, "EXPECTED_DATABASE": database}, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)


class PaidCommandEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_uses_selected_database_with_mock_client(self):
        for name, expected in (("local", "test_local"), ("prod", "test_production")):
            client = MagicMock()
            client.admin.command = AsyncMock()
            client.close = AsyncMock()
            args = SimpleNamespace(create_paid_index=True, limit=None, dry_run=False)
            with self.subTest(name=name), patch.dict(os.environ, {**ENV, "APP_ENV": name}, clear=True), \
                    patch.object(worker, "load_dotenv"), patch.object(worker, "AsyncMongoClient", return_value=client), \
                    patch.object(worker, "create_paid_index", new=AsyncMock()):
                await worker.run_paid_command(args)
                client.__getitem__.assert_called_once_with(expected)

    async def test_invalid_environment_fails_before_client_is_created(self):
        args = SimpleNamespace(create_paid_index=True, limit=None, dry_run=False)
        with patch.dict(os.environ, {**ENV, "APP_ENV": "prod", "MONGODB_DB_NAME_PRODUCTION": ""}, clear=True), \
                patch.object(worker, "load_dotenv"), patch.object(worker, "AsyncMongoClient") as client:
            with self.assertRaisesRegex(worker.WorkerConfigurationError, "MONGODB_DB_NAME_PRODUCTION"):
                await worker.run_paid_command(args)
            client.assert_not_called()
