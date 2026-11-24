"""Generate a colorful, mobile-friendly Rent/Buy split HTML dashboard for GitHub Pages."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ub_prices.config import AppConfig
from ub_prices.storage.sqlite_builder import build_sqlite_db


def _load_dashboard_payload(
    config: AppConfig, data_dir: Path, db_path: Path
) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        latest_run = conn.execute(
            "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        dedup_rows: list[dict[str, Any]] = []
        for r in conn.execute(
            """
            SELECT
                source, external_id, url, deal_type, complex_slug, complex_name, zone,
                title, rooms, area_sqm, floor, total_floors, floor_tier,
                year_built, window_facing, faces_south, faces_west,
                parking_status, payment_terms, furnished,
                raw_price_text, start_price_text, start_price_mnt,
                total_price_mnt, price_per_sqm_mnt, price_basis,
                seller_cut_pct, tracked_drop_pct, effective_drop_pct,
                family_score, duplicate_listing_count, duplicate_urls,
                unit_fingerprint, first_seen_at, last_seen_at, published_label
            FROM v_family_shortlist
            ORDER BY family_score DESC, total_price_mnt ASC
            """
        ).fetchall():
            dedup_rows.append(dict(r))

        raw_active_count = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND price_anomaly = 0"
        ).fetchone()[0]

        rvb_map = {
            r["complex_slug"]: dict(r)
            for r in conn.execute("SELECT * FROM v_rent_vs_buy").fetchall()
        }
        complexes_summary: list[dict[str, Any]] = []
        for c in config.complexes:
            row = rvb_map.get(c.slug, {})
            complexes_summary.append(
                {
                    "slug": c.slug,
                    "name": c.name,
                    "zone": c.zone,
                    "banner_image": c.banner_image,
                    "accent_color": c.accent_color,
                    "sale_units": row.get("sale_units", 0),
                    "sale_median_sqm_mnt": row.get("sale_median_sqm_mnt"),
                    "sale_median_total_mnt": row.get("sale_median_total_mnt"),
                    "rent_units": row.get("rent_units", 0),
                    "median_monthly_rent_mnt": row.get("median_monthly_rent_mnt"),
                    "median_rent_per_sqm_mnt": row.get("median_rent_per_sqm_mnt"),
                    "gross_yield_pct_sqm": row.get("gross_yield_pct_sqm"),
                    "price_to_rent_years": row.get("price_to_rent_years"),
                }
            )

        trend_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT
                    substr(observed_at, 1, 10) AS day,
                    deal_type,
                    COUNT(DISTINCT external_id) AS obs_count,
                    CAST(ROUND(AVG(price_per_sqm_mnt)) AS INTEGER) AS avg_sqm_mnt,
                    CAST(ROUND(AVG(total_price_mnt)) AS INTEGER) AS avg_total_mnt
                FROM price_history
                WHERE price_anomaly = 0 AND total_price_mnt IS NOT NULL
                GROUP BY substr(observed_at, 1, 10), deal_type
                ORDER BY day ASC
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "latest_run": dict(latest_run) if latest_run else None,
        "raw_active_count": raw_active_count,
        "dedup_active_count": len(dedup_rows),
        "complexes": complexes_summary,
        "listings": dedup_rows,
        "trends": trend_rows,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ulaanbaatar Family Property Tracker — Rent &amp; Buy</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg-top: #fff9f2;
      --bg-bot: #f1f5f9;
      --surface: #ffffff;
      --surface-soft: #f8fafc;
      --border: #e2e8f0;
      --text: #1e293b;
      --muted: #64748b;
      --rent: #0284c7;
      --rent-dark: #0369a1;
      --rent-soft: #e0f2fe;
      --buy: #d97706;
      --buy-dark: #b45309;
      --buy-soft: #fef3c7;
      --teal: #0f766e;
      --teal-soft: #ccfbf1;
      --green: #15803d;
      --green-soft: #dcfce7;
      --rose: #be123c;
      --rose-soft: #ffe4e6;
      --radius: 16px;
      --shadow: 0 2px 10px rgba(15, 23, 42, 0.06);
      --shadow-hover: 0 8px 22px rgba(15, 23, 42, 0.11);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bot) 420px, var(--bg-bot) 100%);
      color: var(--text);
      line-height: 1.45;
      min-height: 100vh;
      -webkit-font-smoothing: antialiased;
    }
    .hero {
      background: linear-gradient(125deg, #0f766e 0%, #0284c7 52%, #6366f1 100%);
      color: #fff;
      padding: 24px 16px 26px;
      box-shadow: 0 4px 18px rgba(15, 23, 42, 0.14);
    }
    .hero-inner {
      max-width: 1280px;
      margin: 0 auto;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    .hero h1 {
      margin: 0;
      font-size: clamp(1.4rem, 2.8vw, 2rem);
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .hero-sub {
      color: rgba(255, 255, 255, 0.88);
      font-size: 0.92rem;
      margin-top: 4px;
    }
    .meta-pill {
      background: rgba(255, 255, 255, 0.16);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.28);
      border-radius: 999px;
      padding: 7px 15px;
      font-size: 0.82rem;
      color: #fff;
      font-weight: 500;
    }
    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 20px 16px 64px;
    }

    /* Main Mode Switcher: RENT vs BUY vs COMPARE */
    .mode-bar {
      display: grid;
      grid-template-columns: 1fr 1fr auto;
      gap: 10px;
      margin-bottom: 20px;
    }
    .mode-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      padding: 15px 18px;
      border-radius: var(--radius);
      border: 2px solid var(--border);
      background: var(--surface);
      color: var(--text);
      font-size: 1.05rem;
      font-weight: 800;
      cursor: pointer;
      box-shadow: var(--shadow);
      transition: all 0.18s ease;
    }
    .mode-btn:hover { transform: translateY(-1px); box-shadow: var(--shadow-hover); }
    .mode-btn .count-pill {
      font-size: 0.8rem;
      font-weight: 700;
      padding: 2px 9px;
      border-radius: 999px;
      background: #f1f5f9;
      color: var(--muted);
    }
    .mode-btn.active-rent {
      background: linear-gradient(135deg, #0284c7, #0ea5e9);
      border-color: #0284c7;
      color: #fff;
    }
    .mode-btn.active-rent .count-pill { background: rgba(255,255,255,0.22); color: #fff; }
    .mode-btn.active-buy {
      background: linear-gradient(135deg, #d97706, #f59e0b);
      border-color: #d97706;
      color: #fff;
    }
    .mode-btn.active-buy .count-pill { background: rgba(255,255,255,0.22); color: #fff; }
    .mode-btn.active-compare {
      background: linear-gradient(135deg, #0f766e, #14b8a6);
      border-color: #0f766e;
      color: #fff;
    }
    .mode-btn.active-compare .count-pill { background: rgba(255,255,255,0.22); color: #fff; }

    /* 12-Complex Visual Photo Strip */
    .section-title {
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      font-weight: 750;
      margin: 0 0 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .complex-strip {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(185px, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }
    .c-tile {
      position: relative;
      height: 82px;
      border-radius: 13px;
      overflow: hidden;
      cursor: pointer;
      border: 2.5px solid transparent;
      box-shadow: var(--shadow);
      transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
      background-size: cover;
      background-position: center;
    }
    .c-tile:hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }
    .c-tile.selected { border-color: #0f172a; box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.35); }
    .c-tile.dimmed { opacity: 0.45; }
    .c-tile-scrim {
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(15, 23, 42, 0.18) 0%, rgba(15, 23, 42, 0.78) 100%);
      padding: 9px 11px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      color: #fff;
    }
    .c-tile-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
    }
    .c-tile-dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      border: 1.5px solid rgba(255,255,255,0.85);
      flex-shrink: 0;
    }
    .c-tile-cnt {
      font-size: 0.72rem;
      font-weight: 700;
      background: rgba(255,255,255,0.22);
      backdrop-filter: blur(4px);
      padding: 1px 7px;
      border-radius: 999px;
    }
    .c-tile-name {
      font-weight: 750;
      font-size: 0.9rem;
      line-height: 1.15;
      text-shadow: 0 1px 3px rgba(0,0,0,0.5);
    }
    .c-tile-stat {
      font-size: 0.75rem;
      color: rgba(255,255,255,0.9);
      font-weight: 600;
    }

    /* KPIs */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .kpi {
      background: var(--surface);
      border: 1px solid var(--border);
      border-top: 4px solid var(--teal);
      border-radius: var(--radius);
      padding: 13px 15px;
      box-shadow: var(--shadow);
    }
    .kpi.k-rent { border-top-color: var(--rent); }
    .kpi.k-buy { border-top-color: var(--buy); }
    .kpi.k-sun { border-top-color: #eab308; }
    .kpi.k-cut { border-top-color: #e11d48; }
    .kpi-label {
      font-size: 0.74rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 700;
    }
    .kpi-value {
      font-size: 1.45rem;
      font-weight: 800;
      margin-top: 3px;
      letter-spacing: -0.02em;
    }
    .kpi-sub { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }

    /* Filters Card */
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
      box-shadow: var(--shadow);
      margin-bottom: 18px;
    }
    .card h2 { margin: 0 0 12px; font-size: 1.05rem; font-weight: 750; }
    .filters {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      align-items: end;
    }
    .field label {
      display: block;
      font-size: 0.74rem;
      font-weight: 700;
      color: var(--muted);
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .field select, .field input {
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--surface-soft);
      font-size: 0.9rem;
      color: var(--text);
    }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      align-items: center;
    }
    .chip-label { font-size: 0.8rem; font-weight: 700; color: var(--muted); margin-right: 4px; }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 13px;
      border-radius: 999px;
      border: 1.5px solid var(--border);
      background: var(--surface-soft);
      font-size: 0.84rem;
      font-weight: 650;
      cursor: pointer;
      user-select: none;
      transition: all 0.15s ease;
    }
    .chip.on {
      background: var(--teal-soft);
      border-color: var(--teal);
      color: #115e59;
    }
    .chip-reset {
      margin-left: auto;
      background: transparent;
      border: none;
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: underline;
    }

    /* Apartment Cards with Short & Wide Rectangular Complex Photo Banner */
    .listing-cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(325px, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }
    .l-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      transition: transform 0.16s ease, box-shadow 0.16s ease;
    }
    .l-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }
    .c-banner {
      position: relative;
      height: 88px;
      width: 100%;
      background-size: cover;
      background-position: center;
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      padding: 10px 13px;
      color: #fff;
    }
    .c-banner::before {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(15,23,42,0.12) 0%, rgba(15,23,42,0.74) 100%);
    }
    .c-banner > * { position: relative; z-index: 1; }
    .c-banner-name {
      font-weight: 800;
      font-size: 1.04rem;
      line-height: 1.15;
      text-shadow: 0 1px 4px rgba(0,0,0,0.55);
    }
    .c-banner-zone {
      font-size: 0.74rem;
      color: rgba(255,255,255,0.88);
      text-shadow: 0 1px 3px rgba(0,0,0,0.45);
    }
    .score-pill {
      font-weight: 750;
      font-size: 0.76rem;
      color: #fff;
      background: rgba(15, 118, 110, 0.9);
      border: 1px solid rgba(255,255,255,0.35);
      padding: 3px 9px;
      border-radius: 999px;
      backdrop-filter: blur(4px);
      white-space: nowrap;
    }
    .l-body {
      padding: 13px 14px 14px;
      display: flex;
      flex-direction: column;
      gap: 9px;
      flex: 1;
    }
    .l-price-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
    }
    .l-price {
      font-weight: 850;
      font-size: 1.32rem;
      letter-spacing: -0.02em;
    }
    .l-price.rent-col { color: var(--rent-dark); }
    .l-price.buy-col { color: var(--buy-dark); }
    .l-sqm { font-size: 0.82rem; font-weight: 650; color: var(--muted); }
    .l-specs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      font-size: 0.86rem;
      color: #334155;
      background: #f8fafc;
      border-radius: 10px;
      padding: 7px 10px;
      border: 1px solid #f1f5f9;
    }
    .badges-wrap { display: flex; flex-wrap: wrap; gap: 5px; }
    .badge {
      display: inline-block;
      padding: 3px 9px;
      border-radius: 999px;
      font-size: 0.74rem;
      font-weight: 700;
    }
    .b-good { background: var(--green-soft); color: var(--green); }
    .b-sun { background: #fef9c3; color: #854d0e; }
    .b-cut { background: var(--rose-soft); color: var(--rose); }
    .b-neutral { background: #f1f5f9; color: #475569; }
    .l-title {
      font-size: 0.84rem;
      color: var(--muted);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .l-footer {
      margin-top: auto;
      padding-top: 8px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .l-meta-row {
      display: flex;
      justify-content: space-between;
      font-size: 0.76rem;
      color: var(--muted);
    }
    .btn-cta {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 11px;
      font-weight: 750;
      font-size: 0.88rem;
      color: #fff;
      transition: transform 0.12s ease, filter 0.12s ease;
      box-shadow: 0 2px 6px rgba(15, 23, 42, 0.14);
    }
    .btn-cta:hover { filter: brightness(1.06); transform: translateY(-1px); }
    .btn-cta.rent-btn { background: linear-gradient(135deg, #0284c7, #0ea5e9); }
    .btn-cta.buy-btn { background: linear-gradient(135deg, #d97706, #f59e0b); }

    /* Charts & Tables */
    .charts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 18px;
    }
    .chart-wrap { position: relative; height: 310px; }
    .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    th, td { text-align: left; padding: 10px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
    th {
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      background: var(--surface-soft);
    }
    th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
    tr:hover td { background: #f8fafc; }
    .tbl-btn {
      display: inline-block;
      background: #0f766e;
      color: #fff;
      text-decoration: none;
      padding: 4px 10px;
      border-radius: 8px;
      font-weight: 650;
      font-size: 0.78rem;
    }
    .tbl-btn:hover { background: #115e59; }
    .empty { text-align: center; color: var(--muted); padding: 36px 16px; font-size: 0.95rem; }
    .hidden { display: none !important; }
    @media (max-width: 720px) {
      .mode-bar { grid-template-columns: 1fr 1fr; }
      .mode-bar .mode-compare { grid-column: 1 / -1; }
      .complex-strip { grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); }
      .c-tile { height: 74px; }
    }
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div>
        <h1>Ulaanbaatar Family Apartment Finder</h1>
        <div class="hero-sub">12 Khan-Uul &amp; Zaisan Complexes · 3–5 өрөө (2–4 Bed) · ≥ 60 m² · Deduplicated</div>
      </div>
      <div class="meta-pill" id="metaPill">Loading...</div>
    </div>
  </header>

  <main class="container">
    <!-- 2 MAIN SECTIONS (RENT / BUY) + COMPARISON -->
    <nav class="mode-bar">
      <button class="mode-btn active-rent" id="btnModeRent" onclick="switchMode('rent')">
        <span>🏠 Rent (Түрээс)</span>
        <span class="count-pill" id="cntRent">0</span>
      </button>
      <button class="mode-btn" id="btnModeBuy" onclick="switchMode('sale')">
        <span>🔑 Buy (Зарна)</span>
        <span class="count-pill" id="cntBuy">0</span>
      </button>
      <button class="mode-btn mode-compare" id="btnModeCompare" onclick="switchMode('compare')">
        <span>📊 Rent vs. Buy &amp; Cuts</span>
      </button>
    </nav>

    <!-- RENT / BUY MAIN VIEW -->
    <section id="viewDeal">
      <div class="section-title">
        <span id="stripLabel">Browse by Complex (click a photo to filter)</span>
        <button class="chip-reset" id="btnClearComplex" style="display:none;" onclick="selectComplex('all')">Show all 12 complexes</button>
      </div>
      <div class="complex-strip" id="complexStrip"></div>

      <section class="kpi-grid" id="kpiGrid"></section>

      <div class="card">
        <div class="filters">
          <div class="field">
            <label for="fComplex">Complex</label>
            <select id="fComplex"><option value="all">All 12 Complexes</option></select>
          </div>
          <div class="field">
            <label for="fRooms">Rooms (өрөө)</label>
            <select id="fRooms">
              <option value="all">3–5 өрөө (All)</option>
              <option value="3">3 өрөө (2 Bed)</option>
              <option value="4">4 өрөө (3 Bed)</option>
              <option value="5">5 өрөө (4 Bed)</option>
            </select>
          </div>
          <div class="field">
            <label for="fMinArea">Min Area (m²)</label>
            <input id="fMinArea" type="number" min="60" step="10" placeholder="60" />
          </div>
          <div class="field">
            <label for="fMaxPrice" id="lblMaxPrice">Max Rent (M ₮/mo)</label>
            <input id="fMaxPrice" type="number" min="0" step="1" placeholder="Any" />
          </div>
          <div class="field">
            <label for="fSort">Sort By</label>
            <select id="fSort">
              <option value="family">Family Match Score</option>
              <option value="price_asc">Lowest Price</option>
              <option value="sqm_asc">Lowest ₮/m²</option>
              <option value="cut_desc">Biggest Price Cut %</option>
              <option value="area_desc">Largest Area (m²)</option>
              <option value="newest">Newest First</option>
            </select>
          </div>
        </div>
        <div class="chip-row">
          <span class="chip-label">Family Preferences:</span>
          <span class="chip" data-key="southWest">☀️ South / West Windows</span>
          <span class="chip" data-key="parking">🚗 Interior Parking</span>
          <span class="chip" data-key="highFloor">🏢 Higher Floor</span>
          <span class="chip" data-key="priceCut">📉 Price Cut Only</span>
          <button class="chip-reset" id="btnReset">Reset filters</button>
        </div>
      </div>

      <div id="resultCount" class="section-title" style="text-transform:none;letter-spacing:0;"></div>
      <div class="listing-cards" id="listingCards"></div>

      <div class="charts-grid">
        <div class="card">
          <h2 id="dealBarTitle">Median Monthly Rent by Complex</h2>
          <div class="chart-wrap"><canvas id="chartDealBar"></canvas></div>
        </div>
        <div class="card">
          <h2 id="dealScatterTitle">Value Map: Area (m²) vs. Monthly Rent (click a dot to open)</h2>
          <div class="chart-wrap"><canvas id="chartDealScatter"></canvas></div>
        </div>
      </div>
    </section>

    <!-- RENT VS BUY & PRICE CUTS COMPARISON VIEW -->
    <section id="viewCompare" class="hidden">
      <div class="card">
        <h2>12-Complex Rent vs. Buy Yield Comparison (Deduplicated Units)</h2>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Complex</th>
                <th>Zone</th>
                <th class="num">Rent Units</th>
                <th class="num">Median Rent/mo</th>
                <th class="num">Sale Units</th>
                <th class="num">Median Sale ₮/m²</th>
                <th class="num">Median Total</th>
                <th class="num">Gross Yield</th>
                <th class="num">Price / Annual Rent</th>
              </tr>
            </thead>
            <tbody id="rvbBody"></tbody>
          </table>
        </div>
      </div>

      <div class="charts-grid">
        <div class="card">
          <h2>📉 Rent Price Cuts</h2>
          <div class="table-scroll">
            <table>
              <thead>
                <tr><th>Complex</th><th>Unit</th><th class="num">Was</th><th class="num">Now</th><th class="num">Cut</th><th>Listing</th></tr>
              </thead>
              <tbody id="rentCutsBody"></tbody>
            </table>
          </div>
        </div>
        <div class="card">
          <h2>📉 Buy Price Cuts</h2>
          <div class="table-scroll">
            <table>
              <thead>
                <tr><th>Complex</th><th>Unit</th><th class="num">Was</th><th class="num">Now</th><th class="num">Cut</th><th>Listing</th></tr>
              </thead>
              <tbody id="saleCutsBody"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Daily Average Asking Trends Over Time</h2>
        <div class="chart-wrap"><canvas id="chartTrends"></canvas></div>
      </div>
    </section>
  </main>

  <script>
    const DATA = __DASHBOARD_JSON__;
    const COMPLEX_BY_SLUG = Object.fromEntries(DATA.complexes.map((c) => [c.slug, c]));

    const state = {
      mode: "rent", // "rent" | "sale" | "compare"
      complex: "all",
      rooms: "all",
      minArea: "",
      maxPrice: "",
      sort: "family",
      southWest: false,
      parking: false,
      highFloor: false,
      priceCut: false,
    };

    const fmtM = (v) => {
      if (v == null) return "—";
      if (v >= 1e9) return (v / 1e9).toFixed(2) + "B ₮";
      if (v >= 1e6) return (v / 1e6).toFixed(1) + "M ₮";
      return v.toLocaleString() + " ₮";
    };
    const fmtSqm = (v) => (v == null ? "—" : (v / 1e6).toFixed(2) + "M ₮/m²");
    const median = (arr) => {
      const nums = arr.filter((x) => typeof x === "number" && !Number.isNaN(x)).sort((a, b) => a - b);
      if (!nums.length) return null;
      const mid = Math.floor(nums.length / 2);
      return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
    };

    const rentAll = DATA.listings.filter((l) => l.deal_type === "rent");
    const saleAll = DATA.listings.filter((l) => l.deal_type === "sale");
    document.getElementById("cntRent").textContent = rentAll.length;
    document.getElementById("cntBuy").textContent = saleAll.length;
    document.getElementById("metaPill").textContent =
      `Updated ${DATA.generated_at} · ${DATA.dedup_active_count} unique units (from ${DATA.raw_active_count} ads)`;

    // Populate complex dropdown
    const fComplex = document.getElementById("fComplex");
    DATA.complexes.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.slug;
      opt.textContent = c.name;
      fComplex.appendChild(opt);
    });

    function bgStyle(c) {
      const accent = (c && c.accent_color) || "#0f766e";
      if (c && c.banner_image) {
        return `background-image: url('${c.banner_image}'); background-color: ${accent};`;
      }
      return `background: linear-gradient(135deg, ${accent}, #1e293b);`;
    }

    function renderComplexStrip() {
      const isRent = state.mode === "rent";
      document.getElementById("stripLabel").textContent =
        isRent ? "12 Complexes — Rental Snapshot (click a photo to filter)" : "12 Complexes — Purchase Snapshot (click a photo to filter)";
      document.getElementById("btnClearComplex").style.display = state.complex === "all" ? "none" : "inline";

      document.getElementById("complexStrip").innerHTML = DATA.complexes.map((c) => {
        const cnt = isRent ? c.rent_units : c.sale_units;
        const stat = isRent
          ? (c.median_monthly_rent_mnt ? `${fmtM(c.median_monthly_rent_mnt)}/mo` : "No rentals yet")
          : (c.sale_median_sqm_mnt ? fmtSqm(c.sale_median_sqm_mnt) : "No sales yet");
        const sel = state.complex === c.slug ? "selected" : (state.complex !== "all" ? "dimmed" : "");
        return `
          <div class="c-tile ${sel}" style="${bgStyle(c)}" onclick="selectComplex('${c.slug}')">
            <div class="c-tile-scrim">
              <div class="c-tile-top">
                <span class="c-tile-dot" style="background:${c.accent_color};"></span>
                <span class="c-tile-cnt">${cnt} ${cnt === 1 ? "unit" : "units"}</span>
              </div>
              <div>
                <div class="c-tile-name">${c.name}</div>
                <div class="c-tile-stat">${stat}</div>
              </div>
            </div>
          </div>
        `;
      }).join("");
    }

    function selectComplex(slug) {
      state.complex = state.complex === slug ? "all" : slug;
      document.getElementById("fComplex").value = state.complex;
      renderDealSection();
    }

    function getFiltered() {
      const deal = state.mode === "sale" ? "sale" : "rent";
      return DATA.listings.filter((l) => {
        if (l.deal_type !== deal) return false;
        if (state.complex !== "all" && l.complex_slug !== state.complex) return false;
        if (state.rooms !== "all" && String(l.rooms) !== state.rooms) return false;
        if (state.minArea && (l.area_sqm || 0) < Number(state.minArea)) return false;
        if (state.maxPrice && (l.total_price_mnt || 0) > Number(state.maxPrice) * 1e6) return false;
        if (state.southWest && !(l.faces_south || l.faces_west)) return false;
        if (state.parking && !(l.parking_status === "included" || l.parking_status === "available")) return false;
        if (state.highFloor && l.floor_tier !== "high") return false;
        if (state.priceCut && !(l.effective_drop_pct && l.effective_drop_pct > 0)) return false;
        return true;
      }).sort((a, b) => {
        switch (state.sort) {
          case "sqm_asc": return (a.price_per_sqm_mnt || 1e18) - (b.price_per_sqm_mnt || 1e18);
          case "price_asc": return (a.total_price_mnt || 1e18) - (b.total_price_mnt || 1e18);
          case "cut_desc": return (b.effective_drop_pct || 0) - (a.effective_drop_pct || 0);
          case "area_desc": return (b.area_sqm || 0) - (a.area_sqm || 0);
          case "newest": return (b.first_seen_at || "").localeCompare(a.first_seen_at || "");
          default:
            return (b.family_score - a.family_score)
              || ((a.price_per_sqm_mnt || 1e18) - (b.price_per_sqm_mnt || 1e18));
        }
      });
    }

    function renderKPIs(items) {
      const isRent = state.mode === "rent";
      const medTotal = median(items.map((x) => x.total_price_mnt));
      const medSqm = median(items.map((x) => x.price_per_sqm_mnt));
      const swCnt = items.filter((x) => x.faces_south || x.faces_west).length;
      const cutCnt = items.filter((x) => x.effective_drop_pct && x.effective_drop_pct > 0).length;

      document.getElementById("kpiGrid").innerHTML = isRent ? `
        <div class="kpi k-rent">
          <div class="kpi-label">Unique Rentals Shown</div>
          <div class="kpi-value">${items.length}</div>
          <div class="kpi-sub">3–5 өрөө · ≥ 60 m²</div>
        </div>
        <div class="kpi k-rent">
          <div class="kpi-label">Median Monthly Rent</div>
          <div class="kpi-value">${fmtM(medTotal)}</div>
          <div class="kpi-sub">${medSqm ? Math.round(medSqm / 1000) + "k ₮/m²/mo" : "—"}</div>
        </div>
        <div class="kpi k-sun">
          <div class="kpi-label">South / West Windows</div>
          <div class="kpi-value">${swCnt}</div>
          <div class="kpi-sub">Afternoon sun matches</div>
        </div>
        <div class="kpi k-cut">
          <div class="kpi-label">Rent Price Cuts</div>
          <div class="kpi-value">${cutCnt}</div>
          <div class="kpi-sub">Landlord reduced asking</div>
        </div>
      ` : `
        <div class="kpi k-buy">
          <div class="kpi-label">Unique Sales Shown</div>
          <div class="kpi-value">${items.length}</div>
          <div class="kpi-sub">3–5 өрөө · ≥ 60 m²</div>
        </div>
        <div class="kpi k-buy">
          <div class="kpi-label">Median Sale ₮/m²</div>
          <div class="kpi-value">${fmtSqm(medSqm)}</div>
          <div class="kpi-sub">Median total: ${fmtM(medTotal)}</div>
        </div>
        <div class="kpi k-sun">
          <div class="kpi-label">South / West Windows</div>
          <div class="kpi-value">${swCnt}</div>
          <div class="kpi-sub">Afternoon sun matches</div>
        </div>
        <div class="kpi k-cut">
          <div class="kpi-label">Sale Price Cuts</div>
          <div class="kpi-value">${cutCnt}</div>
          <div class="kpi-sub">Seller reduced asking</div>
        </div>
      `;
    }

    function renderCards(items) {
      const isRent = state.mode === "rent";
      document.getElementById("resultCount").textContent =
        `Showing ${items.length} unique ${isRent ? "rental" : "for-sale"} apartments`;

      const wrap = document.getElementById("listingCards");
      if (!items.length) {
        wrap.innerHTML = `<div class="empty" style="grid-column:1/-1;">No ${isRent ? "rental" : "sale"} apartments match your current filters. Try clearing one filter above.</div>`;
        return;
      }

      wrap.innerHTML = items.map((l) => {
        const comp = COMPLEX_BY_SLUG[l.complex_slug] || {};
        const floorTxt = l.floor != null
          ? (l.total_floors ? `${l.floor}/${l.total_floors}F` : `${l.floor}F`)
          : "Floor —";
        const floorBadge = l.floor_tier === "high" ? `<span class="badge b-good">🏢 High floor</span>` : "";
        const winBadge = l.window_facing
          ? `<span class="badge ${(l.faces_south || l.faces_west) ? "b-sun" : "b-neutral"}">☀️ ${l.window_facing}</span>`
          : "";
        const parkBadge = l.parking_status === "included"
          ? `<span class="badge b-good">🚗 Parking incl.</span>`
          : (l.parking_status === "available" ? `<span class="badge b-good">🚗 Parking avail.</span>` : "");
        const cutBadge = l.effective_drop_pct > 0
          ? `<span class="badge b-cut">📉 -${l.effective_drop_pct.toFixed(1)}% cut</span>`
          : "";
        const dupBadge = l.duplicate_listing_count > 1
          ? `<span class="badge b-neutral">👥 ${l.duplicate_listing_count} realtor ads</span>`
          : "";
        const extraSpec = isRent
          ? [l.payment_terms, l.furnished ? l.furnished.replace("_", " ") : ""].filter(Boolean).join(" · ")
          : (l.year_built ? `Built ${l.year_built}` : "");

        return `
          <article class="l-card">
            <div class="c-banner" style="${bgStyle(comp)}">
              <div>
                <div class="c-banner-name">${l.complex_name}</div>
                <div class="c-banner-zone">${l.zone || ""}</div>
              </div>
              <span class="score-pill">Match ${l.family_score}/6</span>
            </div>
            <div class="l-body">
              <div class="l-price-row">
                <div class="l-price ${isRent ? "rent-col" : "buy-col"}">${fmtM(l.total_price_mnt)}${isRent ? "/mo" : ""}</div>
                <div class="l-sqm">${isRent ? (l.price_per_sqm_mnt ? Math.round(l.price_per_sqm_mnt/1000) + "k ₮/m²" : "") : fmtSqm(l.price_per_sqm_mnt)}</div>
              </div>
              <div class="l-specs">
                <strong>${l.rooms} өрөө</strong> · <strong>${l.area_sqm} m²</strong> · <span>${floorTxt}</span>${extraSpec ? ` · <span>${extraSpec}</span>` : ""}
              </div>
              <div class="badges-wrap">${winBadge}${parkBadge}${floorBadge}${cutBadge}${dupBadge}</div>
              <div class="l-title">${l.title}</div>
              <div class="l-footer">
                <div class="l-meta-row">
                  <span>Posted: ${l.published_label || l.first_seen_at.slice(0, 10)}</span>
                  <span>ID #${l.external_id}</span>
                </div>
                <a class="btn-cta ${isRent ? "rent-btn" : "buy-btn"}" href="${l.url}" target="_blank" rel="noopener">
                  <span>📷 View Photos &amp; Full Listing on Unegui ↗</span>
                </a>
              </div>
            </div>
          </article>
        `;
      }).join("");
    }

    let dealBarChart = null;
    let dealScatterChart = null;
    function renderDealCharts() {
      if (typeof Chart === "undefined") return;
      const isRent = state.mode === "rent";

      document.getElementById("dealBarTitle").textContent =
        isRent ? "Median Monthly Rent (M ₮) by Complex" : "Median Sale Price (M ₮ / m²) by Complex";
      document.getElementById("dealScatterTitle").textContent =
        isRent ? "Rent Value Map: Area (m²) vs. Monthly Rent (click a dot to open)" : "Buy Value Map: Area (m²) vs. Total Price (click a dot to open)";

      if (dealBarChart) dealBarChart.destroy();
      const validC = DATA.complexes.filter((c) => isRent ? c.median_monthly_rent_mnt : c.sale_median_sqm_mnt);
      dealBarChart = new Chart(document.getElementById("chartDealBar"), {
        type: "bar",
        data: {
          labels: validC.map((c) => c.name),
          datasets: [{
            label: isRent ? "Median Rent (M ₮/mo)" : "Median Sale (M ₮/m²)",
            data: validC.map((c) => +((isRent ? c.median_monthly_rent_mnt : c.sale_median_sqm_mnt) / 1e6).toFixed(2)),
            backgroundColor: validC.map((c) => c.accent_color || (isRent ? "#0284c7" : "#d97706")),
            borderRadius: 7,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true, title: { display: true, text: isRent ? "M ₮ / month" : "M ₮ / m²" } } },
        },
      });

      if (dealScatterChart) dealScatterChart.destroy();
      const deal = isRent ? "rent" : "sale";
      const datasets = DATA.complexes.map((c) => {
        const pts = DATA.listings
          .filter((l) => l.deal_type === deal && l.complex_slug === c.slug && l.area_sqm && l.total_price_mnt)
          .map((l) => ({
            x: l.area_sqm,
            y: +(l.total_price_mnt / 1e6).toFixed(1),
            url: l.url,
            label: `${l.complex_name} · ${l.rooms} өрөө · ${l.area_sqm}m² · ${fmtM(l.total_price_mnt)}`,
          }));
        return {
          label: c.name,
          data: pts,
          backgroundColor: c.accent_color || "#0f766e",
          pointRadius: 5.5,
          pointHoverRadius: 8,
        };
      }).filter((ds) => ds.data.length > 0);

      dealScatterChart = new Chart(document.getElementById("chartDealScatter"), {
        type: "scatter",
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          onClick: (_, els) => {
            if (!els.length) return;
            const pt = dealScatterChart.data.datasets[els[0].datasetIndex].data[els[0].index];
            if (pt && pt.url) window.open(pt.url, "_blank", "noopener");
          },
          plugins: { tooltip: { callbacks: { label: (ctx) => ctx.raw.label } } },
          scales: {
            x: { title: { display: true, text: "Area (m²)" } },
            y: { title: { display: true, text: isRent ? "Monthly Rent (M ₮)" : "Total Price (M ₮)" } },
          },
        },
      });
    }

    let trendsRendered = false;
    function renderCompareSection() {
      document.getElementById("rvbBody").innerHTML = DATA.complexes.map((c) => `
        <tr>
          <td><span style="display:inline-block;width:10px;height:10px;border-radius:99px;background:${c.accent_color};margin-right:7px;"></span><strong>${c.name}</strong></td>
          <td style="color:var(--muted);">${c.zone}</td>
          <td class="num">${c.rent_units}</td>
          <td class="num">${fmtM(c.median_monthly_rent_mnt)}</td>
          <td class="num">${c.sale_units}</td>
          <td class="num">${fmtSqm(c.sale_median_sqm_mnt)}</td>
          <td class="num">${fmtM(c.sale_median_total_mnt)}</td>
          <td class="num"><strong>${c.gross_yield_pct_sqm != null ? c.gross_yield_pct_sqm.toFixed(2) + "%" : "—"}</strong></td>
          <td class="num">${c.price_to_rent_years != null ? c.price_to_rent_years.toFixed(1) + " yrs" : "—"}</td>
        </tr>
      `).join("");

      const renderCuts = (deal, tbodyId) => {
        const cuts = DATA.listings
          .filter((l) => l.deal_type === deal && l.effective_drop_pct > 0)
          .sort((a, b) => b.effective_drop_pct - a.effective_drop_pct);
        const body = document.getElementById(tbodyId);
        if (!cuts.length) {
          body.innerHTML = `<tr><td colspan="6" class="empty">No active cuts yet.</td></tr>`;
          return;
        }
        body.innerHTML = cuts.map((l) => {
          const orig = l.start_price_mnt || Math.round(l.total_price_mnt / (1 - l.effective_drop_pct / 100));
          return `
            <tr>
              <td><strong>${l.complex_name}</strong></td>
              <td>${l.rooms} өрөө · ${l.area_sqm}m²</td>
              <td class="num">${fmtM(orig)}</td>
              <td class="num"><strong>${fmtM(l.total_price_mnt)}</strong></td>
              <td class="num"><span class="badge b-cut">-${l.effective_drop_pct.toFixed(1)}%</span></td>
              <td><a class="tbl-btn" href="${l.url}" target="_blank" rel="noopener">Open ↗</a></td>
            </tr>
          `;
        }).join("");
      };
      renderCuts("rent", "rentCutsBody");
      renderCuts("sale", "saleCutsBody");

      if (!trendsRendered && typeof Chart !== "undefined") {
        trendsRendered = true;
        const days = [...new Set(DATA.trends.map((t) => t.day))].sort();
        const saleByDay = Object.fromEntries(DATA.trends.filter((t) => t.deal_type === "sale").map((t) => [t.day, +(t.avg_sqm_mnt / 1e6).toFixed(2)]));
        const rentByDay = Object.fromEntries(DATA.trends.filter((t) => t.deal_type === "rent").map((t) => [t.day, +(t.avg_total_mnt / 1e6).toFixed(2)]));
        new Chart(document.getElementById("chartTrends"), {
          type: "line",
          data: {
            labels: days,
            datasets: [
              { label: "Avg Sale (M ₮/m²)", data: days.map((d) => saleByDay[d] ?? null), borderColor: "#d97706", backgroundColor: "#d97706", tension: 0.25 },
              { label: "Avg Rent (M ₮/mo)", data: days.map((d) => rentByDay[d] ?? null), borderColor: "#0284c7", backgroundColor: "#0284c7", tension: 0.25 },
            ],
          },
          options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: false } } },
        });
      }
    }

    function renderDealSection() {
      const isRent = state.mode === "rent";
      document.getElementById("lblMaxPrice").textContent = isRent ? "Max Rent (M ₮/mo)" : "Max Total Price (M ₮)";
      renderComplexStrip();
      const items = getFiltered();
      renderKPIs(items);
      renderCards(items);
      renderDealCharts();
    }

    function switchMode(mode) {
      state.mode = mode;
      state.maxPrice = "";
      document.getElementById("fMaxPrice").value = "";

      document.getElementById("btnModeRent").className = "mode-btn" + (mode === "rent" ? " active-rent" : "");
      document.getElementById("btnModeBuy").className = "mode-btn" + (mode === "sale" ? " active-buy" : "");
      document.getElementById("btnModeCompare").className = "mode-btn mode-compare" + (mode === "compare" ? " active-compare" : "");

      const isCompare = mode === "compare";
      document.getElementById("viewDeal").classList.toggle("hidden", isCompare);
      document.getElementById("viewCompare").classList.toggle("hidden", !isCompare);

      if (isCompare) {
        renderCompareSection();
      } else {
        renderDealSection();
      }
    }

    // Bind filter controls
    ["fComplex", "fRooms", "fSort"].forEach((id) => {
      document.getElementById(id).addEventListener("change", (e) => {
        state[id.slice(1).toLowerCase()] = e.target.value;
        renderDealSection();
      });
    });
    document.getElementById("fMinArea").addEventListener("input", (e) => { state.minArea = e.target.value; renderDealSection(); });
    document.getElementById("fMaxPrice").addEventListener("input", (e) => { state.maxPrice = e.target.value; renderDealSection(); });
    document.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const k = chip.dataset.key;
        state[k] = !state[k];
        chip.classList.toggle("on", state[k]);
        renderDealSection();
      });
    });
    document.getElementById("btnReset").addEventListener("click", () => {
      Object.assign(state, { complex: "all", rooms: "all", minArea: "", maxPrice: "", sort: "family", southWest: false, parking: false, highFloor: false, priceCut: false });
      document.getElementById("fComplex").value = "all";
      document.getElementById("fRooms").value = "all";
      document.getElementById("fMinArea").value = "";
      document.getElementById("fMaxPrice").value = "";
      document.getElementById("fSort").value = "family";
      document.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
      renderDealSection();
    });

    // Default to Rent section (rent-first-then-buy workflow)
    switchMode("rent");
  </script>
</body>
</html>
"""


def generate_dashboard(
    config: AppConfig,
    data_dir: Path,
    output_path: Path,
    db_path: Optional[Path] = None,
) -> Path:
    """Compile interactive self-contained HTML dashboard at docs/index.html."""
    target_db = db_path or (data_dir / "ub_prices.db")
    if not target_db.exists():
        build_sqlite_db(data_dir, target_db)

    payload = _load_dashboard_payload(config, data_dir, target_db)
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DASHBOARD_JSON__", safe_json)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
