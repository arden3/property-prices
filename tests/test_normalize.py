"""Tests for Mongolian price normalization, 3-5 room & >=60m² filtering, and fingerprints."""

import pytest

from ub_prices.config import FilterConfig
from ub_prices.models import DealType, PriceBasis
from ub_prices.normalize import (
    compute_unit_fingerprint,
    normalize_price,
    parse_area_sqm,
    parse_furnished,
    parse_rent_terms,
    parse_rooms,
    passes_listing_filters,
)


def test_room_parsing_and_5plus_disambiguation():
    assert parse_rooms("3-r", "River Garden 117мкв байр") == 3
    assert parse_rooms("4-r", "4 өрөө орон сууц") == 4
    assert parse_rooms("5-r", "River Garden 240мкв 5 өрөө") == 5
    # 5-r rubric on Unegui means "5+ өрөө"; title reveals 6 rooms -> must return 6 so filter excludes it
    assert parse_rooms("5-r", "Jardin Residence 320мкв 6 өрөө дуплекс") == 6
    assert parse_rooms("2-r", "2 өрөө байр") == 2


def test_filter_enforces_3_to_5_rooms_and_min_60_sqm():
    cfg = FilterConfig()  # rooms 3-5, min_area 60, no max
    assert passes_listing_filters(3, 60.0, cfg) == (True, "")
    assert passes_listing_filters(5, 340.0, cfg) == (True, "")
    assert passes_listing_filters(2, 80.0, cfg)[0] is False
    assert passes_listing_filters(6, 250.0, cfg)[0] is False
    assert passes_listing_filters(3, 59.5, cfg)[0] is False
    assert passes_listing_filters(3, None, cfg)[0] is False


def test_area_parsing_prefers_structured_feature():
    assert parse_area_sqm({"Талбай": "117.5 м²"}, "3 өрөө байр") == 117.5
    assert parse_area_sqm({}, "River Garden 120,5мкв 4 өрөө") == 120.5
    assert parse_area_sqm({}, "King Tower 4 өрөө", "Талбай 135m2 цэлгэр") == 135.0


def test_sale_price_total_vs_per_sqm_disambiguation():
    # Standard total price: 830M MNT for 117.5 sqm -> ~7.06M/sqm
    basis, total, sqm, anomaly, _ = normalize_price(
        DealType.SALE, "830 сая ₮", "830000000", "MNT", area_sqm=117.5
    )
    assert basis == PriceBasis.TOTAL
    assert total == 830_000_000
    assert sqm == round(830_000_000 / 117.5)
    assert anomaly is False

    # Seller entered per-sqm price (7.5 сая ₮) for 120 sqm apartment -> detected as PER_SQM
    basis, total, sqm, anomaly, _ = normalize_price(
        DealType.SALE, "7.5 сая ₮", "7500000", "MNT", area_sqm=120.0
    )
    assert basis == PriceBasis.PER_SQM
    assert sqm == 7_500_000
    assert total == 900_000_000
    assert anomaly is False

    # Billion + million Mongolian text format
    basis, total, sqm, anomaly, _ = normalize_price(
        DealType.SALE, "1 тэрбум 250 сая ₮", None, "MNT", area_sqm=140.0
    )
    assert basis == PriceBasis.TOTAL
    assert total == 1_250_000_000
    assert anomaly is False

    # Suspiciously low typo (e.g. 850,000 MNT total) flagged as anomaly
    basis, total, sqm, anomaly, reason = normalize_price(
        DealType.SALE, "850,000 ₮", "850000", "MNT", area_sqm=100.0
    )
    assert anomaly is True
    assert reason != ""


def test_rent_price_and_terms_parsing():
    basis, monthly, per_sqm, anomaly, _ = normalize_price(
        DealType.RENT, "5.5 сая ₮", "5500000", "MNT", area_sqm=110.0
    )
    assert basis == PriceBasis.MONTHLY
    assert monthly == 5_500_000
    assert per_sqm == 50_000
    assert anomaly is False

    assert parse_rent_terms({}, "3 өрөө түрээслүүлнэ 3+1", "") == "3+1"
    assert parse_rent_terms({"Төлбөрийн нөхцөл": "6 + 1"}, "", "") == "6+1"
    assert parse_furnished("Бүрэн тавилгатай 4 өрөө", "") == "fully_furnished"
    assert parse_furnished("", "Тавилгагүй цэвэрхэн байр") == "unfurnished"


def test_unit_fingerprint_clusters_broker_duplicates():
    fp1 = compute_unit_fingerprint("river-garden", DealType.SALE, 4, 120.1, 6, 15)
    fp2 = compute_unit_fingerprint("river-garden", DealType.SALE, 4, 119.8, 6, 15)
    assert fp1 == fp2 == "river-garden|sale|4r|120sqm|6/15"
