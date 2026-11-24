"""Smart multi-signal property clustering to collapse multi-broker duplicate ads."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from rapidfuzz import fuzz

from ub_prices.models import ListingRecord


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def _price_diff_ratio(a: ListingRecord, b: ListingRecord) -> float | None:
    pa, pb = a.total_price_mnt, b.total_price_mnt
    if not pa or not pb or pa <= 0 or pb <= 0:
        return None
    return abs(pa - pb) / max(pa, pb)


def _area_diff(a: ListingRecord, b: ListingRecord) -> float | None:
    if a.area_sqm is None or b.area_sqm is None:
        return None
    return abs(a.area_sqm - b.area_sqm)


def _are_same_property(a: ListingRecord, b: ListingRecord) -> bool:
    """Decide whether two listings in the same (complex_slug, deal_type) are the same unit."""
    if a.complex_slug != b.complex_slug or a.deal_type != b.deal_type:
        return False

    ad = _area_diff(a, b)
    if ad is None:
        return False

    pr = _price_diff_ratio(a, b)
    room_diff = abs(a.rooms - b.rooms) if (a.rooms is not None and b.rooms is not None) else 0
    floor_diff = abs(a.floor - b.floor) if (a.floor is not None and b.floor is not None) else None

    # Rule A: Same floor + close area (handles 138 vs 139 m², or 3r vs 4r at same price)
    if floor_diff == 0 and ad <= 2.5:
        if room_diff == 0 and (pr is None or pr <= 0.12):
            return True
        if room_diff <= 1 and pr is not None and pr <= 0.03:
            return True

    # Rule B: Broker floor-fudging (±1–2 floors or missing floor), close area, nearly identical price
    # e.g., Marshall Town 5/6F vs 6/6F at 522M, Buddha Vista 4F vs 5F at 6.0M
    if ad <= 2.5 and room_diff <= 1 and pr is not None and pr <= 0.025:
        if floor_diff is None or floor_diff <= 2:
            return True

    # Rule C: Rare large units / duplexes / penthouses (>= 180 m²) with close area & price
    # e.g., Buddha Vista 236 m² duplex listed on 11/18F vs 18/18F at 1.35B–1.38B
    max_area = max(a.area_sqm or 0.0, b.area_sqm or 0.0)
    if max_area >= 180.0 and ad <= 4.0 and room_diff <= 1 and pr is not None and pr <= 0.03:
        return True

    # Rule D: Shared photo URL or near-identical copied broker description
    if ad <= 6.0 and room_diff <= 1 and (pr is None or pr <= 0.05):
        if a.image_urls and b.image_urls and (set(a.image_urls) & set(b.image_urls)):
            return True
        da, db = (a.description or "").strip(), (b.description or "").strip()
        if len(da) >= 50 and len(db) >= 50:
            if fuzz.token_set_ratio(da.lower(), db.lower()) >= 84.0:
                return True

    return False


def assign_property_clusters(records: Iterable[ListingRecord]) -> dict[tuple[str, str], str]:
    """Compute a deterministic cluster_id per listing key (source, external_id)."""
    by_bucket: dict[tuple[str, str], list[ListingRecord]] = defaultdict(list)
    for rec in records:
        by_bucket[(rec.complex_slug, rec.deal_type.value)].append(rec)

    result: dict[tuple[str, str], str] = {}

    for (slug, deal), group in by_bucket.items():
        # Deterministic order by external_id so cluster IDs are stable across rebuilds
        group.sort(key=lambda r: (r.source, int(r.external_id) if r.external_id.isdigit() else 0, r.external_id))
        n = len(group)
        uf = _UnionFind(n)

        for i in range(n):
            ri = group[i]
            if ri.price_anomaly:
                continue
            for j in range(i + 1, n):
                rj = group[j]
                if rj.price_anomaly:
                    continue
                if _are_same_property(ri, rj):
                    uf.union(i, j)

        # Group indices by root to assign clean readable cluster IDs
        components: dict[int, list[ListingRecord]] = defaultdict(list)
        for idx, rec in enumerate(group):
            components[uf.find(idx)].append(rec)

        for root, members in components.items():
            # Anchor cluster ID to the lowest external_id in the cluster for stability
            anchor = members[0]
            cid = f"{slug}|{deal}|{anchor.external_id}"
            for m in members:
                result[m.key] = cid

    return result
