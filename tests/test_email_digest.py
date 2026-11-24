"""Unit tests for the 3-day family email digest module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ub_prices.config import load_config
from ub_prices.email_digest import (
    collect_digest_data,
    is_fresh_find,
    render_email_digest,
    send_email_smtp,
)
from ub_prices.models import DealType, ListingRecord
from ub_prices.storage.sqlite_builder import build_sqlite_db
from ub_prices.storage.store import CanonicalStore


def test_is_fresh_find_mongolian_labels_and_fallback():
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Fresh Mongolian published labels
    assert is_fresh_find("Өнөөдөр 14:20", "2026-04-01T00:00:00+00:00", 3, now) is True
    assert is_fresh_find("Өчигдөр", "2026-04-01T00:00:00+00:00", 3, now) is True
    assert is_fresh_find("2 цагийн өмнө", "2026-04-01T00:00:00+00:00", 3, now) is True
    assert is_fresh_find("3 өдрийн өмнө", "2026-04-01T00:00:00+00:00", 3, now) is True

    # Stale Mongolian published labels (even if newly scraped today)
    assert is_fresh_find("4 өдрийн өмнө", "2026-04-20T10:00:00+00:00", 3, now) is False
    assert is_fresh_find("2 долоо хоногийн өмнө", "2026-04-20T10:00:00+00:00", 3, now) is False
    assert is_fresh_find("1 сарын өмнө", "2026-04-20T10:00:00+00:00", 3, now) is False

    # Respects custom lookback_days for explicit day counts
    assert is_fresh_find("5 өдрийн өмнө", "2026-04-01T00:00:00+00:00", 7, now) is True

    # Fallback to first_seen_at when published_label is blank
    assert is_fresh_find("", "2026-04-19T00:00:00+00:00", 3, now) is True
    assert is_fresh_find("", "2026-04-10T00:00:00+00:00", 3, now) is False


def test_collect_and_render_digest_filters_budget_and_ranks(tmp_path: Path):
    cfg = load_config()
    data_dir = tmp_path / "data"
    store = CanonicalStore(data_dir)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    records = [
        # Fresh in-budget rent with high match score (South+West + parking + high floor = 6/6)
        ListingRecord(
            source="unegui",
            external_id="r1",
            url="https://www.unegui.mn/adv/r1/",
            complex_slug="river-garden",
            complex_name="River Garden",
            deal_type=DealType.RENT,
            title="River Garden 4 өрөө 130м2 урагшаа баруун",
            rooms=4,
            area_sqm=130.0,
            floor=7,
            total_floors=9,
            floor_tier="high",
            faces_south=True,
            faces_west=True,
            window_facing="South, West",
            parking_status="included",
            total_price_mnt=5_500_000,
            price_per_sqm_mnt=42_308,
            start_price_mnt=6_000_000,
            payment_terms="3+1",
            published_label="Өнөөдөр",
            first_seen_at="2026-04-20T08:00:00+00:00",
            last_seen_at="2026-04-20T08:00:00+00:00",
            is_active=True,
            unit_fingerprint="fp-r1",
        ),
        # Fresh OVERPRICED rent (> 6.1M) -> must be excluded from digest highlights
        ListingRecord(
            source="unegui",
            external_id="r_over",
            url="https://www.unegui.mn/adv/r_over/",
            complex_slug="river-garden",
            complex_name="River Garden",
            deal_type=DealType.RENT,
            title="River Garden 5 өрөө luxury rent",
            rooms=5,
            area_sqm=210.0,
            floor=8,
            total_floors=9,
            floor_tier="high",
            total_price_mnt=9_000_000,
            price_per_sqm_mnt=42_857,
            published_label="Өнөөдөр",
            first_seen_at="2026-04-20T08:00:00+00:00",
            last_seen_at="2026-04-20T08:00:00+00:00",
            is_active=True,
            unit_fingerprint="fp-r-over",
        ),
        # Fresh in-budget sale (< 1.2B)
        ListingRecord(
            source="unegui",
            external_id="s1",
            url="https://www.unegui.mn/adv/s1/",
            complex_slug="japan-town",
            complex_name="Japan Town",
            deal_type=DealType.SALE,
            title="Japan Town 3 өрөө 98м2 зарна",
            rooms=3,
            area_sqm=98.0,
            floor=5,
            total_floors=7,
            floor_tier="high",
            faces_south=True,
            window_facing="South",
            parking_status="available",
            total_price_mnt=980_000_000,
            price_per_sqm_mnt=10_000_000,
            published_label="Өчигдөр",
            first_seen_at="2026-04-19T08:00:00+00:00",
            last_seen_at="2026-04-20T08:00:00+00:00",
            is_active=True,
            unit_fingerprint="fp-s1",
        ),
        # Fresh OVERPRICED sale (>= 1.2B) -> must be excluded from digest highlights
        ListingRecord(
            source="unegui",
            external_id="s_over",
            url="https://www.unegui.mn/adv/s_over/",
            complex_slug="buddha-vista",
            complex_name="Buddha Vista",
            deal_type=DealType.SALE,
            title="Buddha Vista 4 өрөө 190м2",
            rooms=4,
            area_sqm=190.0,
            floor=10,
            total_floors=15,
            total_price_mnt=1_500_000_000,
            price_per_sqm_mnt=7_894_737,
            published_label="Өнөөдөр",
            first_seen_at="2026-04-20T08:00:00+00:00",
            last_seen_at="2026-04-20T08:00:00+00:00",
            is_active=True,
            unit_fingerprint="fp-s-over",
        ),
        # Old listing (2 weeks ago) -> excluded from fresh finds
        ListingRecord(
            source="unegui",
            external_id="s_old",
            url="https://www.unegui.mn/adv/s_old/",
            complex_slug="king-tower",
            complex_name="King Tower",
            deal_type=DealType.SALE,
            title="King Tower 3 өрөө 85м2",
            rooms=3,
            area_sqm=85.0,
            floor=3,
            total_floors=15,
            total_price_mnt=550_000_000,
            price_per_sqm_mnt=6_470_588,
            published_label="2 долоо хоногийн өмнө",
            first_seen_at="2026-04-20T08:00:00+00:00",
            last_seen_at="2026-04-20T08:00:00+00:00",
            is_active=True,
            unit_fingerprint="fp-s-old",
        ),
    ]
    store.save_listings({(r.source, r.external_id): r for r in records})
    db_path = build_sqlite_db(data_dir)

    data = collect_digest_data(cfg, data_dir, db_path=db_path, lookback_days=3, top_n=5, now_utc=now)
    assert data["fresh_rents_total"] == 1
    assert data["fresh_sales_total"] == 1
    assert [r["external_id"] for r in data["top_rents"]] == ["r1"]
    assert [r["external_id"] for r in data["top_sales"]] == ["s1"]
    assert len(data["top_cuts"]) == 1
    assert data["top_cuts"][0]["external_id"] == "r1"
    assert len(data["complexes"]) == 8

    subject, plain_body, html_body = render_email_digest(data)
    assert "1 new rentals · 1 new sales" in subject
    assert "River Garden" in plain_body
    assert "Japan Town" in plain_body
    assert "Buddha Vista 4 өрөө 190м2" not in html_body
    assert "luxury rent" not in html_body
    assert "Match 6/6" in html_body
    assert "Top In-Budget Price Cuts" in html_body
    assert "8-Complex Median Snapshot" in html_body


def test_send_email_smtp_uses_ssl_and_recipients(monkeypatch):
    mock_server = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_server
    mock_smtp_cls = MagicMock(return_value=mock_ctx)
    monkeypatch.setattr("ub_prices.email_digest.smtplib.SMTP_SSL", mock_smtp_cls)

    send_email_smtp(
        subject="Test Digest",
        plain_body="Plain text body",
        html_body="<h1>HTML body</h1>",
        smtp_user="sender@example.com",
        smtp_password="app-password-16ch",
        recipients=["user1@example.com", "user2@example.com"],
    )

    mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 465, timeout=30)
    mock_server.login.assert_called_once_with("sender@example.com", "app-password-16ch")
    assert mock_server.sendmail.call_count == 1
    args = mock_server.sendmail.call_args[0]
    assert args[0] == "sender@example.com"
    assert args[1] == ["user1@example.com", "user2@example.com"]
    assert "Subject: Test Digest" in args[2]
    assert "user1@example.com, user2@example.com" in args[2]


def test_send_email_smtp_validates_required_inputs():
    with pytest.raises(ValueError, match="SMTP_USER"):
        send_email_smtp("Sub", "txt", "<p>h</p>", "", "pw", ["a@example.com"])
    with pytest.raises(ValueError, match="No recipients"):
        send_email_smtp("Sub", "txt", "<p>h</p>", "u@example.com", "pw", [])
