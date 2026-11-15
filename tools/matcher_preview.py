"""Read-only matcher inspection for ONE real order (no SMS, writes or PII output).

Run from repo root: python -m tools.matcher_preview --order-id MONGO_OBJECT_ID
Uses the same .env database as the application. Prints reference and suggestion
PIDs and catalog names; does not print the customer's phone or order details.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from bson import ObjectId
from pymongo import AsyncMongoClient

from app.catalog import canonical_pid, eligible_products, pid_variants
from app.services.matcher.history import PROFILE_PROJECTION, clean_pid
from app.services.matcher.scoring import profile, similarity
from app.config import MONGODB_DB_NAME, MONGODB_TIMEOUT_MS, MONGODB_URI
from app.services.matcher.history import load_sources
from app.services.matcher.recommendations import recommendations_for_order


async def inspect_order(order_id):
    client = AsyncMongoClient(MONGODB_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS, timeoutMS=MONGODB_TIMEOUT_MS)
    try:
        db = client[MONGODB_DB_NAME]
        order = await db.preprocess_orders.find_one(
            {"_id": ObjectId(order_id)}, {"_id": 1, "phone": 1, "product_id": 1}
        )
        if order is None:
            raise ValueError('Order not found')
        sources, _ = await load_sources(db, order)
        ranked = await recommendations_for_order(db, order)
        products = await eligible_products(db)
        names = {canonical_pid(row.get('pid')): row.get('name') for row in products}
        variants = list(dict.fromkeys(value for pid in ranked for value in pid_variants(pid)))
        docs = await db.fragrantica_products.find({"pid": {"$in": variants}}, PROFILE_PROJECTION).to_list() if variants else []
        profiles = {}
        for doc in sorted(docs, key=lambda row: isinstance(row.get('pid'), str)):
            pid = clean_pid(doc.get('pid'))
            if pid is not None:
                profiles[pid] = profile(doc)
        suggestions = []
        for pid in ranked:
            candidate = profiles.get(pid)
            score = max((similarity(source, candidate) for source in sources), default=0)
            suggestions.append({"pid": pid, "name": names.get(pid, ''), "score": round(score, 4)})
        return {"reference_pids": [source['pid'] for source in sources],
                "recommendations": suggestions, "total": len(ranked)}
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description='Read-only Fragrantica matcher preview')
    parser.add_argument('--order-id', required=True, help='preprocess_orders Mongo ObjectId')
    args = parser.parse_args()
    if not ObjectId.is_valid(args.order_id):
        parser.error('--order-id must be a MongoDB ObjectId')
    print(json.dumps(asyncio.run(inspect_order(args.order_id)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
