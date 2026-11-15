"""Fresh, stock-filtered candidate matching; returns only public catalog PIDs."""
from __future__ import annotations

from app.catalog import eligible_products, pid_variants
from app.services.matcher.history import PROFILE_PROJECTION, clean_pid, load_sources
from app.services.matcher.scoring import profile, rank_recommendations


async def recommendations_for_order(db, current_order):
    sources, bought = await load_sources(db, current_order)
    if not sources:
        return []
    # The candidate pool is always the EXACT current tester catalog boundary.
    # Historical reference perfumes are NOT constrained by vol_idx/stock.
    products = await eligible_products(db)
    pids = {clean_pid(product.get("pid")) for product in products}
    pids.discard(None)
    pids.difference_update(bought)
    if not pids:
        return []
    variants = list(dict.fromkeys(value for pid in sorted(pids) for value in pid_variants(pid)))
    fragrances = await db["fragrantica_products"].find({"pid": {"$in": variants}}, PROFILE_PROJECTION).to_list()
    candidates = {}
    for fragrance in sorted(fragrances, key=lambda row: isinstance(row.get("pid"), str)):
        pid = clean_pid(fragrance.get("pid"))
        if pid in pids:
            item = profile(fragrance)
            if item is not None:
                candidates[pid] = item
    return rank_recommendations(sources, candidates, bought)
