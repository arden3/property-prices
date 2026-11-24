"""End-to-end tests for scanner, incremental fetching, price drops, delistings, SQLite views, and reports."""

import sqlite3
from pathlib import Path

from ub_prices.config import load_config
from ub_prices.dashboard import generate_dashboard
from ub_prices.models import DealType, IndexCard, ListingDetail
from ub_prices.report import generate_report
from ub_prices.scanner import run_scan
from ub_prices.sources.base import SourceAdapter
from ub_prices.storage.sqlite_builder import build_sqlite_db
from ub_prices.storage.store import CanonicalStore


class MockAdapter(SourceAdapter):
    name = "unegui"

    def __init__(self, cards_by_deal: dict[DealType, list[IndexCard]], details: dict[str, ListingDetail]):
        self.cards_by_deal = cards_by_deal
        self.details = details
        self.detail_fetches: list[str] = []

    def build_search_url(self, deal_type: DealType, query: str, page: int = 1) -> str:
        return f"https://www.unegui.mn/mock/{deal_type.value}?q={query}&page={page}"

    def search_page(self, deal_type: DealType, query: str, page: int = 1):
        if page > 1:
            return [], False
        # Return cards only for river garden queries to simulate real search
        if "river" in query.lower() or "ривер" in query.lower():
            return self.cards_by_deal.get(deal_type, []), False
        return [], False

    def fetch_detail(self, card: IndexCard) -> ListingDetail:
        self.detail_fetches.append(card.external_id)
        return self.details[card.external_id]


def test_full_lifecycle_scan_price_drop_delisting_and_views(tmp_path: Path):
    cfg = load_config()
    data_dir = tmp_path / "data"
    report_path = tmp_path / "reports" / "latest.md"

    sale_card_1 = IndexCard(
        source="unegui",
        external_id="1001",
        url="https://www.unegui.mn/adv/1001/",
        deal_type=DealType.SALE,
        title="River Garden 1 хотхонд 120мкв 4 өрөө",
        rubric_slug="4-r",
        raw_price_text="960 сая ₮",
        price_without_currency="960000000",
    )
    # Duplicate broker listing of the exact same physical unit at a slightly higher price
    sale_card_dup = IndexCard(
        source="unegui",
        external_id="1002",
        url="https://www.unegui.mn/adv/1002/",
        deal_type=DealType.SALE,
        title="Ривер гарден 1 хотхонд 120м2 4 өрөө зарна",
        rubric_slug="4-r",
        raw_price_text="980 сая ₮",
        price_without_currency="980000000",
    )
    # Too small (55m2) -> should be filtered out after detail fetch
    sale_card_small = IndexCard(
        source="unegui",
        external_id="1003",
        url="https://www.unegui.mn/adv/1003/",
        deal_type=DealType.SALE,
        title="River Garden 55мкв 3 өрөө",
        rubric_slug="3-r",
        raw_price_text="450 сая ₮",
        price_without_currency="450000000",
    )
    rent_card_1 = IndexCard(
        source="unegui",
        external_id="2001",
        url="https://www.unegui.mn/adv/2001/",
        deal_type=DealType.RENT,
        title="River Garden 120мкв 4 өрөө түрээслүүлнэ 3+1",
        rubric_slug="4-r",
        raw_price_text="6 сая ₮",
        price_without_currency="6000000",
    )

    details = {
        "1001": ListingDetail(
            source="unegui",
            external_id="1001",
            url="https://www.unegui.mn/adv/1001/",
            deal_type=DealType.SALE,
            title=sale_card_1.title,
            description="Урагшаа болон баруун харсан цонхтой, дулаан зогсоолтой",
            location_text="Улаанбаатар — Хан-Уул — River Garden",
            rubric_slug="4-r",
            raw_price_text="960 сая ₮",
            start_price_text="1,050,000,000 ₮",
            price_without_currency="960000000",
            features={"Талбай": "120 м²", "Хэдэн давхарт": "6", "Барилгын давхар": "9", "Гараж": "Байгаа"},
        ),
        "1002": ListingDetail(
            source="unegui",
            external_id="1002",
            url="https://www.unegui.mn/adv/1002/",
            deal_type=DealType.SALE,
            title=sale_card_dup.title,
            location_text="Улаанбаатар — Хан-Уул — River Garden",
            rubric_slug="4-r",
            raw_price_text="980 сая ₮",
            price_without_currency="980000000",
            features={"Талбай": "120 м²", "Хэдэн давхарт": "6", "Барилгын давхар": "10"},
        ),
        "1003": ListingDetail(
            source="unegui",
            external_id="1003",
            url="https://www.unegui.mn/adv/1003/",
            deal_type=DealType.SALE,
            title=sale_card_small.title,
            location_text="Улаанбаатар — Хан-Уул — River Garden",
            rubric_slug="3-r",
            raw_price_text="450 сая ₮",
            price_without_currency="450000000",
            features={"Талбай": "55 м²", "Хэдэн давхарт": "2", "Барилгын давхар": "9"},
        ),
        "2001": ListingDetail(
            source="unegui",
            external_id="2001",
            url="https://www.unegui.mn/adv/2001/",
            deal_type=DealType.RENT,
            title=rent_card_1.title,
            location_text="Улаанбаатар — Хан-Уул — River Garden",
            rubric_slug="4-r",
            raw_price_text="6 сая ₮",
            price_without_currency="6000000",
            features={"Талбай": "120 м²", "Хэдэн давхарт": "4", "Барилгын давхар": "9"},
        ),
    }

    # --- RUN 1: Initial backfill ---
    adapter1 = MockAdapter(
        {DealType.SALE: [sale_card_1, sale_card_dup, sale_card_small], DealType.RENT: [rent_card_1]},
        details,
    )
    run1 = run_scan(cfg, data_dir, adapter=adapter1, max_pages=2)
    assert run1.status == "ok"
    assert run1.new_listings == 3  # 1003 filtered out (<60m2)
    assert run1.details_fetched == 4

    # --- RUN 2: Price drop on 1001 (960M -> 900M), 1002 disappears (missed_scans=1), 2001 unchanged ---
    sale_card_1_dropped = sale_card_1.model_copy(
        update={"raw_price_text": "900 сая ₮", "price_without_currency": "900000000"}
    )
    details_run2 = dict(details)
    details_run2["1001"] = details["1001"].model_copy(
        update={"raw_price_text": "900 сая ₮", "price_without_currency": "900000000"}
    )
    adapter2 = MockAdapter(
        {DealType.SALE: [sale_card_1_dropped], DealType.RENT: [rent_card_1]},
        details_run2,
    )
    run2 = run_scan(cfg, data_dir, adapter=adapter2, max_pages=2)
    assert run2.status == "ok"
    assert adapter2.detail_fetches == ["1001"]  # 2001 skipped because price unchanged!
    assert run2.price_changes == 1
    assert run2.delisted_count == 0  # 1002 only missed 1 scan so far

    # --- RUN 3: 1002 still missing -> missed_scans=2 -> delisted! ---
    adapter3 = MockAdapter(
        {DealType.SALE: [sale_card_1_dropped], DealType.RENT: [rent_card_1]},
        details_run2,
    )
    run3 = run_scan(cfg, data_dir, adapter=adapter3, max_pages=2)
    assert run3.delisted_count == 1
    assert adapter3.detail_fetches == []  # zero detail requests when nothing changed!

    store = CanonicalStore(data_dir)
    stored = store.load_listings()
    assert stored[("unegui", "1002")].is_active is False
    assert stored[("unegui", "1001")].total_price_mnt == 900_000_000

    # --- Verify SQLite views ---
    db_path = build_sqlite_db(data_dir)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rvb = conn.execute(
            "SELECT * FROM v_rent_vs_buy WHERE complex_slug = 'river-garden'"
        ).fetchone()
        assert rvb is not None
        assert rvb["sale_units"] == 1
        assert rvb["sale_median_sqm_mnt"] == 7_500_000  # 900M / 120m2
        assert rvb["median_monthly_rent_mnt"] == 6_000_000
        # 50k/sqm * 12 / 7.5M = 8.0% gross yield, 12.5 yrs price-to-rent
        assert rvb["gross_yield_pct_sqm"] == 8.0
        assert rvb["price_to_rent_years"] == 12.5

        drops = conn.execute("SELECT * FROM v_price_drops").fetchall()
        assert len(drops) == 1
        assert drops[0]["first_price_mnt"] == 960_000_000
        assert drops[0]["current_price_mnt"] == 900_000_000
        assert drops[0]["drop_pct"] == 6.25

        cuts = conn.execute("SELECT * FROM v_seller_price_cuts").fetchall()
        assert len(cuts) == 1
        assert cuts[0]["start_price_mnt"] == 1_050_000_000

        shortlist = conn.execute(
            "SELECT * FROM v_family_shortlist WHERE external_id = '1001'"
        ).fetchone()
        assert shortlist is not None
        assert shortlist["faces_south"] == 1
        assert shortlist["faces_west"] == 1
        assert shortlist["parking_status"] == "included"
        assert shortlist["floor_tier"] == "high"
        assert shortlist["family_score"] == 6
    finally:
        conn.close()

    # --- Verify Markdown Report & Interactive HTML Dashboard ---
    md = generate_report(cfg, data_dir, report_path, db_path=db_path)
    assert "# Ulaanbaatar Property Prices — Daily Digest" in md
    assert "River Garden" in md
    assert "Family Shortlist Top Matches" in md
    assert "8.00%" in md

    dash_path = tmp_path / "docs" / "index.html"
    generate_dashboard(cfg, data_dir, dash_path, db_path=db_path)
    html = dash_path.read_text(encoding="utf-8")
    assert "Ulaanbaatar Family Property Tracker" in html
    assert "River Garden" in html
    assert "South, West" in html
