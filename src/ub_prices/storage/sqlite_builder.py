"""Compile canonical JSONL/CSV files into an analytical SQLite database with views."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ub_prices.storage.store import CanonicalStore

SCHEMA_SQL = """
CREATE TABLE listings (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    deal_type TEXT NOT NULL,
    complex_slug TEXT NOT NULL,
    complex_name TEXT NOT NULL,
    zone TEXT,
    match_confidence TEXT,
    match_reason TEXT,
    title TEXT NOT NULL,
    district TEXT,
    location_text TEXT,
    lat REAL,
    lng REAL,
    rooms INTEGER,
    area_sqm REAL,
    floor INTEGER,
    total_floors INTEGER,
    floor_tier TEXT,
    year_built INTEGER,
    balcony TEXT,
    garage TEXT,
    parking_status TEXT,
    window_count INTEGER,
    window_facing TEXT,
    faces_south INTEGER NOT NULL DEFAULT 0,
    faces_west INTEGER NOT NULL DEFAULT 0,
    faces_east INTEGER NOT NULL DEFAULT 0,
    faces_north INTEGER NOT NULL DEFAULT 0,
    payment_terms TEXT,
    furnished TEXT,
    raw_price_text TEXT,
    start_price_text TEXT,
    start_price_mnt INTEGER,
    currency TEXT,
    price_basis TEXT,
    total_price_mnt INTEGER,
    price_per_sqm_mnt INTEGER,
    price_anomaly INTEGER NOT NULL DEFAULT 0,
    price_anomaly_reason TEXT,
    unit_fingerprint TEXT,
    seller_id TEXT,
    seller_name TEXT,
    is_business INTEGER,
    published_label TEXT,
    description TEXT,
    image_urls_json TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    missed_scans INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, external_id)
);

CREATE TABLE price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    deal_type TEXT NOT NULL,
    complex_slug TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_price_text TEXT,
    currency TEXT,
    price_basis TEXT,
    total_price_mnt INTEGER,
    price_per_sqm_mnt INTEGER,
    price_anomaly INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE scrape_runs (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    is_full_scan INTEGER NOT NULL DEFAULT 0,
    requests_made INTEGER NOT NULL DEFAULT 0,
    index_cards_seen INTEGER NOT NULL DEFAULT 0,
    matched_candidates INTEGER NOT NULL DEFAULT 0,
    details_fetched INTEGER NOT NULL DEFAULT 0,
    new_listings INTEGER NOT NULL DEFAULT 0,
    price_changes INTEGER NOT NULL DEFAULT 0,
    delisted_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE INDEX idx_listings_complex_deal ON listings (complex_slug, deal_type, is_active);
CREATE INDEX idx_listings_fingerprint ON listings (unit_fingerprint);
CREATE INDEX idx_price_history_key ON price_history (source, external_id, observed_at);

-- Deduplicate active, non-anomaly multi-realtor ads by unit_fingerprint.
-- Picks lowest asking price per unit, breaking ties by richest metadata (orientation/parking)
-- and freshest last_seen_at. Aggregates broker ad count and URLs across duplicates.
CREATE VIEW v_active_deduped AS
WITH fp_stats AS (
    SELECT
        unit_fingerprint,
        COUNT(*) AS duplicate_listing_count,
        GROUP_CONCAT(url, ' | ') AS duplicate_urls,
        MAX(faces_south) AS any_south,
        MAX(faces_west) AS any_west,
        MAX(faces_east) AS any_east,
        MAX(faces_north) AS any_north,
        MAX(CASE WHEN parking_status = 'included' THEN 2 WHEN parking_status = 'available' THEN 1 ELSE 0 END) AS best_parking_rank,
        MAX(start_price_mnt) AS max_start_price_mnt,
        MAX(CASE WHEN image_urls_json != '[]' THEN image_urls_json ELSE NULL END) AS any_image_urls_json
    FROM listings
    WHERE is_active = 1
      AND price_anomaly = 0
      AND total_price_mnt IS NOT NULL
    GROUP BY unit_fingerprint
),
ranked AS (
    SELECT
        l.*,
        s.duplicate_listing_count,
        s.duplicate_urls,
        s.any_south,
        s.any_west,
        s.any_east,
        s.any_north,
        s.best_parking_rank,
        COALESCE(l.start_price_mnt, s.max_start_price_mnt) AS effective_start_price_mnt,
        CASE
            WHEN l.image_urls_json != '[]' THEN l.image_urls_json
            ELSE COALESCE(s.any_image_urls_json, '[]')
        END AS effective_image_urls_json,
        ROW_NUMBER() OVER (
            PARTITION BY l.unit_fingerprint
            ORDER BY
                l.total_price_mnt ASC,
                (CASE WHEN l.image_urls_json != '[]' THEN 1 ELSE 0 END) DESC,
                (CASE WHEN l.window_facing != '' THEN 1 ELSE 0 END) DESC,
                (CASE WHEN l.parking_status IN ('included', 'available') THEN 1 ELSE 0 END) DESC,
                l.last_seen_at DESC
        ) AS rn
    FROM listings l
    JOIN fp_stats s ON s.unit_fingerprint = l.unit_fingerprint
    WHERE l.is_active = 1
      AND l.price_anomaly = 0
      AND l.total_price_mnt IS NOT NULL
)
SELECT * FROM ranked WHERE rn = 1;

-- Exact median sale prices per complex (using SQLite window functions)
CREATE VIEW v_sale_medians AS
WITH ordered_sqm AS (
    SELECT
        complex_slug,
        complex_name,
        zone,
        price_per_sqm_mnt,
        total_price_mnt,
        ROW_NUMBER() OVER (PARTITION BY complex_slug ORDER BY price_per_sqm_mnt) AS rn_sqm,
        ROW_NUMBER() OVER (PARTITION BY complex_slug ORDER BY total_price_mnt) AS rn_tot,
        COUNT(*) OVER (PARTITION BY complex_slug) AS cnt
    FROM v_active_deduped
    WHERE deal_type = 'sale' AND price_per_sqm_mnt IS NOT NULL
)
SELECT
    complex_slug,
    MAX(complex_name) AS complex_name,
    MAX(zone) AS zone,
    MAX(cnt) AS active_unique_units,
    CAST(ROUND(AVG(CASE WHEN rn_sqm IN ((cnt + 1) / 2, (cnt + 2) / 2) THEN price_per_sqm_mnt END)) AS INTEGER) AS median_sqm_mnt,
    CAST(ROUND(AVG(CASE WHEN rn_tot IN ((cnt + 1) / 2, (cnt + 2) / 2) THEN total_price_mnt END)) AS INTEGER) AS median_total_mnt,
    MIN(price_per_sqm_mnt) AS min_sqm_mnt,
    MAX(price_per_sqm_mnt) AS max_sqm_mnt
FROM ordered_sqm
GROUP BY complex_slug;

-- Exact median monthly rent per complex
CREATE VIEW v_rent_medians AS
WITH ordered_rent AS (
    SELECT
        complex_slug,
        complex_name,
        zone,
        total_price_mnt AS monthly_rent_mnt,
        price_per_sqm_mnt AS rent_per_sqm_mnt,
        ROW_NUMBER() OVER (PARTITION BY complex_slug ORDER BY total_price_mnt) AS rn_rent,
        ROW_NUMBER() OVER (PARTITION BY complex_slug ORDER BY price_per_sqm_mnt) AS rn_sqm,
        COUNT(*) OVER (PARTITION BY complex_slug) AS cnt
    FROM v_active_deduped
    WHERE deal_type = 'rent'
)
SELECT
    complex_slug,
    MAX(complex_name) AS complex_name,
    MAX(zone) AS zone,
    MAX(cnt) AS active_unique_units,
    CAST(ROUND(AVG(CASE WHEN rn_rent IN ((cnt + 1) / 2, (cnt + 2) / 2) THEN monthly_rent_mnt END)) AS INTEGER) AS median_monthly_rent_mnt,
    CAST(ROUND(AVG(CASE WHEN rn_sqm IN ((cnt + 1) / 2, (cnt + 2) / 2) THEN rent_per_sqm_mnt END)) AS INTEGER) AS median_rent_per_sqm_mnt,
    MIN(monthly_rent_mnt) AS min_monthly_rent_mnt,
    MAX(monthly_rent_mnt) AS max_monthly_rent_mnt
FROM ordered_rent
GROUP BY complex_slug;

-- Per complex & room count breakdown for sale listings
CREATE VIEW v_sale_by_rooms AS
SELECT
    complex_slug,
    complex_name,
    rooms,
    COUNT(*) AS active_unique_units,
    CAST(ROUND(AVG(area_sqm), 1) AS REAL) AS avg_area_sqm,
    MIN(price_per_sqm_mnt) AS min_sqm_mnt,
    CAST(ROUND(AVG(price_per_sqm_mnt)) AS INTEGER) AS avg_sqm_mnt,
    MAX(price_per_sqm_mnt) AS max_sqm_mnt,
    CAST(ROUND(AVG(total_price_mnt)) AS INTEGER) AS avg_total_mnt
FROM v_active_deduped
WHERE deal_type = 'sale' AND price_per_sqm_mnt IS NOT NULL
GROUP BY complex_slug, complex_name, rooms
ORDER BY complex_slug, rooms;

-- Per complex & room count breakdown for rent listings
CREATE VIEW v_rent_by_rooms AS
SELECT
    complex_slug,
    complex_name,
    rooms,
    COUNT(*) AS active_unique_units,
    CAST(ROUND(AVG(area_sqm), 1) AS REAL) AS avg_area_sqm,
    MIN(total_price_mnt) AS min_monthly_rent_mnt,
    CAST(ROUND(AVG(total_price_mnt)) AS INTEGER) AS avg_monthly_rent_mnt,
    MAX(total_price_mnt) AS max_monthly_rent_mnt
FROM v_active_deduped
WHERE deal_type = 'rent'
GROUP BY complex_slug, complex_name, rooms
ORDER BY complex_slug, rooms;

-- Rent-first-then-buy comparison per complex (gross yield & price-to-rent ratio)
CREATE VIEW v_rent_vs_buy AS
WITH all_complexes AS (
    SELECT complex_slug, complex_name, zone FROM v_sale_medians
    UNION
    SELECT complex_slug, complex_name, zone FROM v_rent_medians
)
SELECT
    c.complex_slug,
    c.complex_name,
    c.zone,
    COALESCE(s.active_unique_units, 0) AS sale_units,
    s.median_sqm_mnt AS sale_median_sqm_mnt,
    s.median_total_mnt AS sale_median_total_mnt,
    COALESCE(r.active_unique_units, 0) AS rent_units,
    r.median_monthly_rent_mnt,
    r.median_rent_per_sqm_mnt,
    CASE
        WHEN s.median_sqm_mnt > 0 AND r.median_rent_per_sqm_mnt > 0
        THEN ROUND((r.median_rent_per_sqm_mnt * 12.0 / s.median_sqm_mnt) * 100.0, 2)
        ELSE NULL
    END AS gross_yield_pct_sqm,
    CASE
        WHEN s.median_sqm_mnt > 0 AND r.median_rent_per_sqm_mnt > 0
        THEN ROUND(s.median_sqm_mnt / (r.median_rent_per_sqm_mnt * 12.0), 1)
        ELSE NULL
    END AS price_to_rent_years
FROM all_complexes c
LEFT JOIN v_sale_medians s ON s.complex_slug = c.complex_slug
LEFT JOIN v_rent_medians r ON r.complex_slug = c.complex_slug
ORDER BY c.complex_slug;

-- Tracked price drops observed across daily scans
CREATE VIEW v_price_drops AS
WITH first_obs AS (
    SELECT
        source,
        external_id,
        total_price_mnt AS first_price_mnt,
        observed_at AS first_observed_at,
        ROW_NUMBER() OVER (PARTITION BY source, external_id ORDER BY observed_at ASC, id ASC) AS rn
    FROM price_history
    WHERE price_anomaly = 0 AND total_price_mnt IS NOT NULL
)
SELECT
    l.source,
    l.external_id,
    l.deal_type,
    l.complex_slug,
    l.complex_name,
    l.rooms,
    l.area_sqm,
    f.first_price_mnt,
    l.total_price_mnt AS current_price_mnt,
    (f.first_price_mnt - l.total_price_mnt) AS drop_mnt,
    ROUND(((f.first_price_mnt - l.total_price_mnt) * 100.0) / f.first_price_mnt, 2) AS drop_pct,
    CAST(ROUND(julianday(l.last_seen_at) - julianday(l.first_seen_at)) AS INTEGER) AS days_tracked,
    l.url,
    l.title
FROM listings l
JOIN first_obs f
  ON f.source = l.source AND f.external_id = l.external_id AND f.rn = 1
WHERE l.is_active = 1
  AND l.price_anomaly = 0
  AND l.total_price_mnt IS NOT NULL
  AND l.total_price_mnt < f.first_price_mnt
ORDER BY drop_pct DESC, drop_mnt DESC;

-- Retroactive price cuts already marked by sellers via Unegui's start_price (deduplicated)
CREATE VIEW v_seller_price_cuts AS
SELECT
    d.source,
    d.external_id,
    d.deal_type,
    d.complex_slug,
    d.complex_name,
    d.rooms,
    d.area_sqm,
    d.floor,
    d.total_floors,
    d.effective_start_price_mnt AS start_price_mnt,
    d.total_price_mnt AS current_price_mnt,
    (d.effective_start_price_mnt - d.total_price_mnt) AS cut_mnt,
    ROUND(((d.effective_start_price_mnt - d.total_price_mnt) * 100.0) / d.effective_start_price_mnt, 2) AS cut_pct,
    d.duplicate_listing_count,
    d.url,
    d.title
FROM v_active_deduped d
WHERE d.effective_start_price_mnt IS NOT NULL
  AND d.effective_start_price_mnt > d.total_price_mnt
ORDER BY cut_pct DESC, cut_mnt DESC;

-- Family Shortlist View (deduplicated 1-per-unit):
-- Scores South (+2) + West (+1) windows, interior parking (included +2 / available +1),
-- and higher floor tier (+1). Ignores mortgage completely. Max score = 6.
CREATE VIEW v_family_shortlist AS
WITH first_obs AS (
    SELECT
        source,
        external_id,
        total_price_mnt AS first_price_mnt,
        ROW_NUMBER() OVER (PARTITION BY source, external_id ORDER BY observed_at ASC, id ASC) AS rn
    FROM price_history
    WHERE price_anomaly = 0 AND total_price_mnt IS NOT NULL
),
scored AS (
    SELECT
        d.source,
        d.external_id,
        d.url,
        d.deal_type,
        d.complex_slug,
        d.complex_name,
        d.zone,
        d.title,
        d.rooms,
        d.area_sqm,
        d.floor,
        d.total_floors,
        d.floor_tier,
        d.year_built,
        CASE
            WHEN d.window_facing != '' THEN d.window_facing
            ELSE trim(
                (CASE WHEN d.any_south = 1 THEN 'South, ' ELSE '' END) ||
                (CASE WHEN d.any_west = 1 THEN 'West, ' ELSE '' END) ||
                (CASE WHEN d.any_east = 1 THEN 'East, ' ELSE '' END) ||
                (CASE WHEN d.any_north = 1 THEN 'North, ' ELSE '' END),
                ', '
            )
        END AS window_facing,
        d.any_south AS faces_south,
        d.any_west AS faces_west,
        CASE
            WHEN d.best_parking_rank = 2 THEN 'included'
            WHEN d.best_parking_rank = 1 THEN 'available'
            ELSE d.parking_status
        END AS parking_status,
        d.payment_terms,
        d.furnished,
        d.raw_price_text,
        d.start_price_text,
        d.effective_start_price_mnt AS start_price_mnt,
        d.total_price_mnt,
        d.price_per_sqm_mnt,
        d.price_basis,
        CASE
            WHEN d.effective_start_price_mnt IS NOT NULL AND d.effective_start_price_mnt > d.total_price_mnt
            THEN ROUND(((d.effective_start_price_mnt - d.total_price_mnt) * 100.0) / d.effective_start_price_mnt, 2)
            ELSE 0.0
        END AS seller_cut_pct,
        CASE
            WHEN f.first_price_mnt IS NOT NULL AND f.first_price_mnt > d.total_price_mnt
            THEN ROUND(((f.first_price_mnt - d.total_price_mnt) * 100.0) / f.first_price_mnt, 2)
            ELSE 0.0
        END AS tracked_drop_pct,
        (
            (CASE WHEN d.any_south = 1 THEN 2 ELSE 0 END) +
            (CASE WHEN d.any_west = 1 THEN 1 ELSE 0 END) +
            d.best_parking_rank +
            (CASE WHEN d.floor_tier = 'high' THEN 1 ELSE 0 END)
        ) AS family_score,
        d.duplicate_listing_count,
        d.duplicate_urls,
        d.effective_image_urls_json AS image_urls_json,
        d.unit_fingerprint,
        d.first_seen_at,
        d.last_seen_at,
        d.published_label
    FROM v_active_deduped d
    LEFT JOIN first_obs f
      ON f.source = d.source AND f.external_id = d.external_id AND f.rn = 1
)
SELECT
    s.*,
    CASE
        WHEN s.seller_cut_pct >= s.tracked_drop_pct THEN s.seller_cut_pct
        ELSE s.tracked_drop_pct
    END AS effective_drop_pct
FROM scored s;
"""


def build_sqlite_db(data_dir: Path, db_path: Path | None = None) -> Path:
    """Rebuild SQLite database cleanly from canonical JSONL/CSV files."""
    store = CanonicalStore(data_dir)
    target = db_path or (data_dir / "ub_prices.db")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = target.with_suffix(".db.tmp")
    if tmp_db.exists():
        tmp_db.unlink()

    listings = store.load_listings()
    history = store.load_price_history()
    runs = store.load_scrape_runs()

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.executescript(SCHEMA_SQL)

        conn.executemany(
            """
            INSERT INTO listings (
                source, external_id, url, deal_type, complex_slug, complex_name, zone,
                match_confidence, match_reason, title, district, location_text, lat, lng,
                rooms, area_sqm, floor, total_floors, floor_tier, year_built, balcony,
                garage, parking_status, window_count, window_facing, faces_south,
                faces_west, faces_east, faces_north, payment_terms, furnished,
                raw_price_text, start_price_text, start_price_mnt, currency,
                price_basis, total_price_mnt, price_per_sqm_mnt, price_anomaly,
                price_anomaly_reason, unit_fingerprint, seller_id, seller_name, is_business,
                published_label, description, image_urls_json, first_seen_at, last_seen_at, missed_scans, is_active
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    r.source, r.external_id, r.url, r.deal_type.value, r.complex_slug,
                    r.complex_name, r.zone, r.match_confidence, r.match_reason, r.title,
                    r.district, r.location_text, r.lat, r.lng, r.rooms, r.area_sqm,
                    r.floor, r.total_floors, r.floor_tier, r.year_built, r.balcony,
                    r.garage, r.parking_status, r.window_count, r.window_facing,
                    int(r.faces_south), int(r.faces_west), int(r.faces_east), int(r.faces_north),
                    r.payment_terms, r.furnished, r.raw_price_text, r.start_price_text,
                    r.start_price_mnt, r.currency, r.price_basis.value, r.total_price_mnt,
                    r.price_per_sqm_mnt, int(r.price_anomaly), r.price_anomaly_reason,
                    r.unit_fingerprint, r.seller_id, r.seller_name,
                    None if r.is_business is None else int(r.is_business),
                    r.published_label, r.description,
                    json.dumps(r.image_urls[:5], ensure_ascii=False),
                    r.first_seen_at, r.last_seen_at,
                    r.missed_scans, int(r.is_active),
                )
                for r in listings.values()
            ],
        )

        conn.executemany(
            """
            INSERT INTO price_history (
                source, external_id, deal_type, complex_slug, observed_at,
                raw_price_text, currency, price_basis, total_price_mnt,
                price_per_sqm_mnt, price_anomaly
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    o.source, o.external_id, o.deal_type.value, o.complex_slug, o.observed_at,
                    o.raw_price_text, o.currency, o.price_basis.value, o.total_price_mnt,
                    o.price_per_sqm_mnt, int(o.price_anomaly),
                )
                for o in history
            ],
        )

        conn.executemany(
            """
            INSERT INTO scrape_runs (
                run_id, source, started_at, finished_at, status, is_full_scan,
                requests_made, index_cards_seen, matched_candidates, details_fetched,
                new_listings, price_changes, delisted_count, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.run_id, s.source, s.started_at, s.finished_at, s.status,
                    int(s.is_full_scan), s.requests_made, s.index_cards_seen,
                    s.matched_candidates, s.details_fetched, s.new_listings,
                    s.price_changes, s.delisted_count, s.notes,
                )
                for s in runs
            ],
        )

        conn.commit()
    finally:
        conn.close()

    tmp_db.replace(target)
    return target
