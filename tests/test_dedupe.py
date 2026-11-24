"""Tests for smart multi-signal property clustering (dedupe.py)."""

from ub_prices.dedupe import assign_property_clusters
from ub_prices.models import DealType, ListingRecord
from ub_prices.normalize import enrich_listing_record


def _rec(
    ext_id: str,
    complex_slug: str = "river-garden",
    deal: DealType = DealType.SALE,
    rooms: int = 4,
    area: float = 120.0,
    floor: int | None = 5,
    total_floors: int | None = 16,
    price: int = 900_000_000,
    desc: str = "",
) -> ListingRecord:
    r = ListingRecord(
        source="unegui",
        external_id=ext_id,
        url=f"https://www.unegui.mn/adv/{ext_id}/",
        deal_type=deal,
        complex_slug=complex_slug,
        complex_name=complex_slug,
        title=f"{complex_slug} {rooms} өрөө {area}мкв",
        rooms=rooms,
        area_sqm=area,
        floor=floor,
        total_floors=total_floors,
        total_price_mnt=price,
        description=desc,
    )
    return enrich_listing_record(r)


def test_clusters_broker_floor_fudging_at_same_price():
    # e.g., 5/6F vs 6/6F, same 116 m², same 522M MNT -> MUST merge
    a = _rec("101", rooms=4, area=116.0, floor=5, total_floors=6, price=522_000_000)
    b = _rec("102", rooms=4, area=116.0, floor=6, total_floors=6, price=522_000_000)
    clusters = assign_property_clusters([a, b])
    assert clusters[a.key] == clusters[b.key]


def test_clusters_area_boundary_and_room_disagreement_on_same_floor():
    # Time Square 138 vs 139 m² on 11F, and Seoul Royal County 200 m² 4r vs 5r on 5F at 5.5M rent
    ts1 = _rec("201", complex_slug="time-square", deal=DealType.RENT, rooms=4, area=138.0, floor=11, price=6_000_000)
    ts2 = _rec("202", complex_slug="time-square", deal=DealType.RENT, rooms=4, area=139.0, floor=11, price=6_000_000)
    src1 = _rec("301", complex_slug="seoul-royal-county", deal=DealType.RENT, rooms=4, area=200.0, floor=5, total_floors=6, price=5_500_000)
    src2 = _rec("302", complex_slug="seoul-royal-county", deal=DealType.RENT, rooms=5, area=200.0, floor=5, total_floors=6, price=5_500_000)

    clusters = assign_property_clusters([ts1, ts2, src1, src2])
    assert clusters[ts1.key] == clusters[ts2.key]
    assert clusters[src1.key] == clusters[src2.key]


def test_swapped_floor_and_total_floors_auto_fixes_and_clusters():
    # Broker accidentally entered 14/9F instead of 9/14F for the exact same 149 m² 5.2M flat
    a = _rec("401", complex_slug="time-square", deal=DealType.RENT, rooms=4, area=149.0, floor=9, total_floors=16, price=5_200_000)
    b = _rec("402", complex_slug="time-square", deal=DealType.RENT, rooms=4, area=149.0, floor=14, total_floors=9, price=5_200_000)
    assert b.floor == 9 and b.total_floors == 14
    clusters = assign_property_clusters([a, b])
    assert clusters[a.key] == clusters[b.key]


def test_clusters_rare_large_duplex_across_floor_guesses():
    a = _rec("501", complex_slug="buddha-vista", rooms=5, area=236.0, floor=11, total_floors=18, price=1_350_000_000)
    b = _rec("502", complex_slug="buddha-vista", rooms=5, area=236.0, floor=18, total_floors=18, price=1_380_000_000)
    clusters = assign_property_clusters([a, b])
    assert clusters[a.key] == clusters[b.key]


def test_does_not_merge_distinct_apartments_on_far_apart_floors():
    # Two genuinely different 120 m² River Garden units: floor 3 (1.05B) vs floor 13 (1.15B)
    a = _rec("601", rooms=4, area=120.0, floor=3, total_floors=16, price=1_050_000_000)
    b = _rec("602", rooms=4, area=120.0, floor=13, total_floors=16, price=1_150_000_000)
    clusters = assign_property_clusters([a, b])
    assert clusters[a.key] != clusters[b.key]
