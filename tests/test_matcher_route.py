"""API contract tests using the repository's existing synthetic ASGI harness."""
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_selections as fixtures


class RecommendationRouteTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.SelectionTests.asyncSetUp
    call = fixtures.SelectionTests.call
    stored_order = fixtures.SelectionTests.stored_order
    selection = fixtures.SelectionTests.selection

    async def test_token_scoped_readonly_only_returns_public_pids(self):
        original = self.stored_order.copy()
        with patch('app.services.matcher.recommendations.recommendations_for_order',
                   new_callable=AsyncMock, return_value=['101', '102']) as matcher:
            status, body, headers = await self.call('recommendations')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'pids': ['101', '102']})
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['referrer-policy'], 'no-referrer')
        self.assertEqual(self.stored_order, original)
        matcher.assert_awaited_once()

    async def test_invalid_link_does_not_query_matcher(self):
        with patch('app.services.matcher.recommendations.recommendations_for_order',
                   new_callable=AsyncMock) as matcher:
            status, _, _ = await self.call('recommendations', token='INVALID')
        self.assertEqual(status, 400)
        matcher.assert_not_awaited()

    async def test_no_accepted_sms_returns_no_recommendations(self):
        self.db[fixtures.worker.NOTIFICATIONS].documents[0]['status'] = 'prepared'
        with patch('app.services.matcher.recommendations.recommendations_for_order',
                   new_callable=AsyncMock) as matcher:
            status, body, _ = await self.call('recommendations')
        self.assertEqual((status, body), (200, {'pids': []}))
        matcher.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
