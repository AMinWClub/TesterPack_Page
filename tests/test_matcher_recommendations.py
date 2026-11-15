"""Pure fake-DB tests: no MongoDB, SMS or network access required."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.services.matcher.history import load_sources
from app.services.matcher.recommendations import recommendations_for_order
from tests.test_matcher_scoring import perfume


class Cursor:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)

    async def to_list(self):
        return copy.deepcopy(self.rows)


class Collection:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.calls = 0

    def find(self, query, projection=None):
        self.calls += 1
        if 'phone' in query:
            rows = [row for row in self.rows if row['phone'] in query['phone']['$in']
                    and row['_id'] != query['_id']['$ne']]
        elif 'pid' in query:
            rows = [row for row in self.rows if row['pid'] in query['pid']['$in']]
        else:
            raise AssertionError(f'unexpected query: {query}')
        return Cursor(rows)


class Database(dict):
    pass


def fragrantica(pid, gender='female'):
    return {'pid': str(pid), **perfume(gender=gender)}


class RecommendationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.current = {'_id': ObjectId(), 'phone': '09370562333', 'product_id': ['9000']}
        self.rows = [
            {'_id': ObjectId(), 'phone': '09370562333', 'date_paid': now.isoformat(),
             'product_id': [str(i) for i in range(1, 11)]},
            {'_id': ObjectId(), 'phone': '09370562333', 'date_paid': (now - timedelta(days=10)).isoformat(),
             'product_id': ['91', '92']},
            {'_id': ObjectId(), 'phone': '09370562333', 'date_paid': (now - timedelta(days=20)).isoformat(),
             'product_id': ['93']},
        ]
        self.fragrances = [fragrantica(i) for i in range(1, 11)]
        self.fragrances += [fragrantica(i) for i in range(100, 130)]
        self.db = Database({'preprocess_orders': Collection(self.rows),
                            'fragrantica_products': Collection(self.fragrances)})

    async def test_recent_order_with_ten_products_yields_three_stable_random_references(self):
        first, purchased = await load_sources(self.db, self.current)
        second, _ = await load_sources(self.db, self.current)
        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)
        self.assertTrue(set(map(str, range(1, 11))).issubset(purchased))
        self.assertIn('9000', purchased)
        self.assertEqual(self.db['preprocess_orders'].calls, 2)
        self.assertEqual(self.db['fragrantica_products'].calls, 2)

    async def test_history_scans_older_orders_until_three_valid_refs(self):
        self.db['fragrantica_products'] = Collection([fragrantica('1'), fragrantica('91'), fragrantica('93')])
        sources, _ = await load_sources(self.db, self.current)
        self.assertEqual(len(sources), 3)

    async def test_invalid_unpaid_and_future_orders_are_not_references(self):
        self.rows.append({'_id': ObjectId(), 'phone': self.current['phone'], 'date_paid': '', 'product_id': ['999']})
        self.rows.append({'_id': ObjectId(), 'phone': self.current['phone'],
                          'date_paid': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                          'product_id': ['999']})
        self.db['preprocess_orders'] = Collection(self.rows)
        self.db['fragrantica_products'] = Collection(self.fragrances + [fragrantica('999')])
        refs, bought = await load_sources(self.db, self.current)
        self.assertEqual(len(refs), 3)
        self.assertNotIn('999', bought)

    async def test_only_current_catalog_and_in_stock_candidates_are_returned(self):
        catalog = [{'pid': str(i), 'vol_idx': i} for i in range(100, 125)]
        with patch('app.services.matcher.recommendations.eligible_products', new_callable=AsyncMock, return_value=catalog):
            pids = await recommendations_for_order(self.db, self.current)
        self.assertGreaterEqual(len(pids), 10)
        self.assertLessEqual(len(pids), 20)
        self.assertTrue(set(pids).issubset({str(i) for i in range(100, 125)}))
        self.assertTrue(set(pids).isdisjoint({str(i) for i in range(1, 11)}))

    async def test_missing_purchase_profiles_does_not_make_guesses(self):
        self.db['fragrantica_products'] = Collection([fragrantica(i) for i in range(100, 125)])
        with patch('app.services.matcher.recommendations.eligible_products', new_callable=AsyncMock) as eligible:
            self.assertEqual(await recommendations_for_order(self.db, self.current), [])
            eligible.assert_not_awaited()

    async def test_query_is_not_broad_without_a_matching_phone(self):
        self.db['preprocess_orders'] = Collection([{
            '_id': ObjectId(), 'phone': '09120000000', 'date_paid': datetime.now(timezone.utc).isoformat(), 'product_id': ['1']
        }])
        self.assertEqual((await load_sources(self.db, self.current))[0], [])


if __name__ == '__main__':
    unittest.main()
