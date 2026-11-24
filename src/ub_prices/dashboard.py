"""Generate a self-contained, mobile-friendly interactive HTML dashboard for GitHub Pages."""

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

        # Deduplicated active listings (with broker count, photos, & family preference fields)
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
                image_urls_json, unit_fingerprint, first_seen_at, last_seen_at, published_label
            FROM v_family_shortlist
            ORDER BY family_score DESC, total_price_mnt ASC
            """
        ).fetchall():
            row_dict = dict(r)
            raw_imgs = row_dict.pop("image_urls_json", "[]")
            try:
                row_dict["image_urls"] = json.loads(raw_imgs) if raw_imgs else []
            except json.JSONDecodeError:
                row_dict["image_urls"] = []
            dedup_rows.append(row_dict)

        # Raw active non-anomaly listings count (for transparency badge)
        raw_active_count = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND price_anomaly = 0"
        ).fetchone()[0]

        # Rent vs buy summary per complex
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

        # Daily historical median trend (grows over time as daily scans accumulate)
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
  <title>Ulaanbaatar Family Property Tracker — 12 Complexes</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #f6f5f1;
      --surface: #ffffff;
      --surface-soft: #f9fafb;
      --border: #e5e7eb;
      --text: #18181b;
      --muted: #6b7280;
      --accent: #0f766e;
      --accent-soft: #ccfbf1;
      --accent-dark: #115e59;
      --blue: #2563eb;
      --blue-soft: #dbeafe;
      --amber: #b45309;
      --amber-soft: #fef3c7;
      --rose: #be123c;
      --rose-soft: #ffe4e6;
      --green: #15803d;
      --green-soft: #dcfce7;
      --radius: 14px;
      --shadow: 0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
      -webkit-font-smoothing: antialiased;
    }
    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 20px 16px 64px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 20px;
    }
    h1 {
      margin: 0;
      font-size: clamp(1.35rem, 2.5vw, 1.85rem);
      font-weight: 750;
      letter-spacing: -0.02em;
    }
    .subtitle {
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }
    .meta-pill {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 14px;
      font-size: 0.82rem;
      color: var(--muted);
      box-shadow: var(--shadow);
    }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .kpi {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 14px 16px;
      box-shadow: var(--shadow);
    }
    .kpi-label {
      font-size: 0.78rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 600;
    }
    .kpi-value {
      font-size: 1.45rem;
      font-weight: 750;
      margin-top: 4px;
      letter-spacing: -0.02em;
    }
    .kpi-sub {
      font-size: 0.78rem;
      color: var(--muted);
      margin-top: 2px;
    }
    .tabs {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 10px;
    }
    .tab-btn {
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      padding: 8px 16px;
      border-radius: 999px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .tab-btn.active {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
      box-shadow: var(--shadow);
      margin-bottom: 16px;
    }
    .card h2 {
      margin: 0 0 12px;
      font-size: 1.05rem;
      font-weight: 700;
    }
    .filters {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      align-items: end;
    }
    .field label {
      display: block;
      font-size: 0.76rem;
      font-weight: 600;
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
    .chip-label {
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--muted);
      margin-right: 4px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--surface-soft);
      font-size: 0.84rem;
      font-weight: 600;
      cursor: pointer;
      user-select: none;
      transition: all 0.15s ease;
    }
    .chip.on {
      background: var(--accent-soft);
      border-color: var(--accent);
      color: var(--accent-dark);
    }
    .chip-reset {
      margin-left: auto;
      background: transparent;
      border: none;
      color: var(--muted);
      font-size: 0.82rem;
      cursor: pointer;
      text-decoration: underline;
    }
    .charts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }
    .chart-wrap {
      position: relative;
      height: 310px;
    }
    .table-scroll {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }
    th, td {
      text-align: left;
      padding: 10px 10px;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    th {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      background: var(--surface-soft);
      position: sticky;
      top: 0;
    }
    th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
    tr:hover td { background: #fafaf9; }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.74rem;
      font-weight: 650;
      margin-right: 4px;
    }
    .b-rent { background: var(--blue-soft); color: var(--blue); }
    .b-sale { background: var(--amber-soft); color: var(--amber); }
    .b-good { background: var(--green-soft); color: var(--green); }
    .b-cut { background: var(--rose-soft); color: var(--rose); }
    .b-neutral { background: #f3f4f6; color: #374151; }
    .listing-cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
      gap: 12px;
    }
    .l-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
    }
    .l-body {
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      flex: 1;
    }
    .photo-box {
      position: relative;
      width: 100%;
      aspect-ratio: 16 / 10;
      background: #ecebe6;
      overflow: hidden;
      cursor: pointer;
    }
    .photo-box img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .photo-nav {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      background: rgba(15, 23, 42, 0.62);
      color: #fff;
      border: none;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      font-size: 1rem;
      line-height: 1;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .photo-nav:hover { background: rgba(15, 23, 42, 0.88); }
    .photo-prev { left: 8px; }
    .photo-next { right: 8px; }
    .photo-count {
      position: absolute;
      bottom: 8px;
      right: 8px;
      background: rgba(15, 23, 42, 0.68);
      color: #fff;
      font-size: 0.72rem;
      font-weight: 650;
      padding: 2px 7px;
      border-radius: 999px;
    }
    .lightbox {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.86);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .lightbox.open { display: flex; }
    .lightbox img {
      max-width: 92vw;
      max-height: 86vh;
      border-radius: 10px;
      object-fit: contain;
    }
    .lb-close {
      position: fixed;
      top: 16px;
      right: 20px;
      background: rgba(255,255,255,0.18);
      color: #fff;
      border: none;
      width: 38px;
      height: 38px;
      border-radius: 999px;
      font-size: 1.4rem;
      cursor: pointer;
    }
    .l-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
    }
    .l-complex { font-weight: 750; font-size: 0.98rem; }
    .l-price {
      font-weight: 800;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
      text-align: right;
    }
    .l-sqm { font-size: 0.78rem; color: var(--muted); text-align: right; }
    .l-specs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 0.84rem;
      color: #3f3f46;
    }
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
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      font-size: 0.8rem;
    }
    .btn-link {
      display: inline-block;
      background: var(--accent);
      color: #fff;
      text-decoration: none;
      padding: 6px 12px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 0.82rem;
    }
    .btn-link:hover { background: var(--accent-dark); }
    .score-pill {
      font-weight: 700;
      font-size: 0.78rem;
      color: var(--accent-dark);
      background: var(--accent-soft);
      padding: 2px 8px;
      border-radius: 999px;
    }
    .empty {
      text-align: center;
      color: var(--muted);
      padding: 36px 16px;
      font-size: 0.95rem;
    }
    @media (max-width: 640px) {
      .container { padding: 14px 12px 48px; }
      .chart-wrap { height: 260px; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Ulaanbaatar Property Prices</h1>
        <div class="subtitle">12 Khan-Uul &amp; Zaisan Complexes · 3–5 өрөө (2–4 Bed) · ≥ 60 m² · Deduplicated</div>
      </div>
      <div class="meta-pill" id="metaPill">Loading...</div>
    </header>

    <section class="kpi-grid" id="kpiGrid"></section>

    <nav class="tabs">
      <button class="tab-btn active" data-tab="explorer">Apartments Explorer</button>
      <button class="tab-btn" data-tab="overview">12-Complex Rent vs. Buy</button>
      <button class="tab-btn" data-tab="drops">Price Cuts &amp; Trends</button>
    </nav>

    <!-- TAB 1: APARTMENTS EXPLORER -->
    <section class="tab-panel active" id="tab-explorer">
      <div class="card">
        <div class="filters">
          <div class="field">
            <label for="fDeal">Deal Type</label>
            <select id="fDeal">
              <option value="all">All (Rent + Sale)</option>
              <option value="rent">Rent (Түрээс)</option>
              <option value="sale">Sale (Зарна)</option>
            </select>
          </div>
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
            <label for="fMaxPrice">Max Price (M ₮)</label>
            <input id="fMaxPrice" type="number" min="0" step="1" placeholder="Any" />
          </div>
          <div class="field">
            <label for="fSort">Sort By</label>
            <select id="fSort">
              <option value="family">Family Match Score</option>
              <option value="sqm_asc">Lowest ₮/m²</option>
              <option value="price_asc">Lowest Price</option>
              <option value="cut_desc">Biggest Price Cut %</option>
              <option value="area_desc">Largest Area (m²)</option>
              <option value="newest">Newest First</option>
            </select>
          </div>
        </div>
        <div class="chip-row">
          <span class="chip-label">Family Preferences:</span>
          <span class="chip" id="cSouthWest" data-key="southWest">☀️ South / West Windows</span>
          <span class="chip" id="cParking" data-key="parking">🚗 Interior Parking</span>
          <span class="chip" id="cHighFloor" data-key="highFloor">🏢 Higher Floor</span>
          <span class="chip" id="cPriceCut" data-key="priceCut">📉 Price Cut Only</span>
          <button class="chip-reset" id="btnReset">Reset filters</button>
        </div>
      </div>

      <div id="resultCount" class="subtitle" style="margin-bottom: 10px;"></div>
      <div class="listing-cards" id="listingCards"></div>
    </section>

    <!-- TAB 2: 12-COMPLEX RENT VS BUY -->
    <section class="tab-panel" id="tab-overview">
      <div class="charts-grid">
        <div class="card">
          <h2>Median Sale ₮/m² by Complex</h2>
          <div class="chart-wrap"><canvas id="chartSaleSqm"></canvas></div>
        </div>
        <div class="card">
          <h2>Median Monthly Rent (M ₮) by Complex</h2>
          <div class="chart-wrap"><canvas id="chartRentMo"></canvas></div>
        </div>
      </div>

      <div class="card">
        <h2>Value Scatter: Area (m²) vs. Price (Click any dot to open listing)</h2>
        <div style="margin-bottom: 8px;">
          <select id="scatterDeal" style="padding:6px 10px;border-radius:8px;border:1px solid var(--border);">
            <option value="sale">Sale Listings (Total M ₮)</option>
            <option value="rent">Rent Listings (Monthly M ₮)</option>
          </select>
        </div>
        <div class="chart-wrap" style="height: 340px;"><canvas id="chartScatter"></canvas></div>
      </div>

      <div class="card">
        <h2>12-Complex Market &amp; Rent-vs-Buy Summary (Deduplicated Units)</h2>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Complex</th>
                <th>Zone</th>
                <th class="num">Sale Units</th>
                <th class="num">Median Sale ₮/m²</th>
                <th class="num">Median Total</th>
                <th class="num">Rent Units</th>
                <th class="num">Median Rent/mo</th>
                <th class="num">Gross Yield</th>
                <th class="num">Price / Annual Rent</th>
              </tr>
            </thead>
            <tbody id="rvbBody"></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- TAB 3: PRICE CUTS & TRENDS -->
    <section class="tab-panel" id="tab-drops">
      <div class="card">
        <h2>Active Seller Price Cuts (Tracked Drops + Unegui Start-Price Reductions)</h2>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Deal</th>
                <th>Complex</th>
                <th>Unit</th>
                <th>Preferences</th>
                <th class="num">Original</th>
                <th class="num">Current</th>
                <th class="num">Cut</th>
                <th class="num">Ads</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody id="dropsBody"></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2>Daily Average Asking ₮/m² Over Time</h2>
        <div class="subtitle" style="margin-bottom: 10px;">Grows automatically as each day's scan records new observations.</div>
        <div class="chart-wrap"><canvas id="chartTrends"></canvas></div>
      </div>
    </section>
  </div>

  <div class="lightbox" id="lightbox" onclick="closeLightbox(event)">
    <button class="lb-close" onclick="closeLightbox(event)">×</button>
    <img id="lbImg" src="" alt="Apartment photo" referrerpolicy="no-referrer" />
  </div>

  <script>
    const DATA = __DASHBOARD_JSON__;

    const state = {
      deal: "all",
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

    // Populate complex dropdown
    const fComplex = document.getElementById("fComplex");
    DATA.complexes.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.slug;
      opt.textContent = `${c.name} (${c.sale_units + c.rent_units})`;
      fComplex.appendChild(opt);
    });

    // Meta pill
    const run = DATA.latest_run;
    document.getElementById("metaPill").textContent =
      `Updated ${DATA.generated_at} · ${DATA.dedup_active_count} unique units (from ${DATA.raw_active_count} ads)`;

    function getFiltered() {
      return DATA.listings.filter((l) => {
        if (state.deal !== "all" && l.deal_type !== state.deal) return false;
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
      const sales = items.filter((i) => i.deal_type === "sale");
      const rents = items.filter((i) => i.deal_type === "rent");
      const cuts = items.filter((i) => i.effective_drop_pct && i.effective_drop_pct > 0);
      const swMatches = items.filter((i) => i.faces_south || i.faces_west);
      const medSaleSqm = median(sales.map((s) => s.price_per_sqm_mnt));
      const medRent = median(rents.map((r) => r.total_price_mnt));

      document.getElementById("kpiGrid").innerHTML = `
        <div class="kpi">
          <div class="kpi-label">Unique Units Shown</div>
          <div class="kpi-value">${items.length}</div>
          <div class="kpi-sub">${sales.length} sale · ${rents.length} rent</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Median Sale ₮/m²</div>
          <div class="kpi-value">${fmtSqm(medSaleSqm)}</div>
          <div class="kpi-sub">Deduplicated active sales</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Median Monthly Rent</div>
          <div class="kpi-value">${fmtM(medRent)}</div>
          <div class="kpi-sub">Long-term rentals (3–5 өрөө)</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">South / West Facing</div>
          <div class="kpi-value">${swMatches.length}</div>
          <div class="kpi-sub">Mentioned in description</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Active Price Cuts</div>
          <div class="kpi-value">${cuts.length}</div>
          <div class="kpi-sub">Seller or tracked drops</div>
        </div>
      `;
    }

    function parkingBadge(status) {
      if (status === "included") return `<span class="badge b-good">🚗 Parking incl.</span>`;
      if (status === "available") return `<span class="badge b-good">🚗 Parking avail.</span>`;
      if (status === "none") return `<span class="badge b-neutral">No garage</span>`;
      return "";
    }

    function renderCards(items) {
      document.getElementById("resultCount").textContent =
        `Showing ${items.length} unique apartments (sorted by ${document.getElementById("fSort").selectedOptions[0].textContent})`;

      const wrap = document.getElementById("listingCards");
      if (!items.length) {
        wrap.innerHTML = `<div class="empty" style="grid-column:1/-1;">No apartments match your current filters. Try relaxing one toggle.</div>`;
        return;
      }

      wrap.innerHTML = items.map((l) => {
        const dealBadge = l.deal_type === "rent"
          ? `<span class="badge b-rent">RENT</span>`
          : `<span class="badge b-sale">SALE</span>`;
        const floorTxt = l.floor != null
          ? (l.total_floors ? `${l.floor}/${l.total_floors}F` : `${l.floor}F`)
          : "Floor —";
        const floorBadge = l.floor_tier === "high" ? `<span class="badge b-good">High floor</span>` : "";
        const winBadge = l.window_facing
          ? `<span class="badge ${(l.faces_south || l.faces_west) ? "b-good" : "b-neutral"}">☀️ ${l.window_facing}</span>`
          : "";
        const cutBadge = l.effective_drop_pct > 0
          ? `<span class="badge b-cut">-${l.effective_drop_pct.toFixed(1)}% cut</span>`
          : "";
        const dupBadge = l.duplicate_listing_count > 1
          ? `<span class="badge b-neutral">${l.duplicate_listing_count} realtor ads</span>`
          : "";
        const termsTxt = l.deal_type === "rent"
          ? [l.payment_terms, l.furnished ? l.furnished.replace("_", " ") : ""].filter(Boolean).join(" · ")
          : (l.year_built ? `Built ${l.year_built}` : "");

        const imgs = Array.isArray(l.image_urls) ? l.image_urls : [];
        const photoHtml = imgs.length
          ? `<div class="photo-box" data-idx="0" data-imgs='${JSON.stringify(imgs).replace(/'/g, "&#39;")}'>
               <img src="${imgs[0]}" alt="${l.complex_name}" loading="lazy" referrerpolicy="no-referrer" onclick="openLightbox(this.src)" onerror="this.closest('.photo-box').style.display='none'" />
               ${imgs.length > 1 ? `<button class="photo-nav photo-prev" onclick="stepPhoto(event, -1)" aria-label="Previous photo">‹</button><button class="photo-nav photo-next" onclick="stepPhoto(event, 1)" aria-label="Next photo">›</button><span class="photo-count">1 / ${imgs.length}</span>` : ""}
             </div>`
          : "";

        return `
          <article class="l-card">
            ${photoHtml}
            <div class="l-body">
              <div class="l-top">
                <div>
                  <div>${dealBadge} <span class="score-pill">Match ${l.family_score}/6</span></div>
                  <div class="l-complex" style="margin-top:4px;">${l.complex_name}</div>
                </div>
                <div>
                  <div class="l-price">${fmtM(l.total_price_mnt)}${l.deal_type === "rent" ? "/mo" : ""}</div>
                  <div class="l-sqm">${l.deal_type === "sale" ? fmtSqm(l.price_per_sqm_mnt) : (l.price_per_sqm_mnt ? Math.round(l.price_per_sqm_mnt/1000) + "k ₮/m²" : "")}</div>
                </div>
              </div>
              <div class="l-specs">
                <strong>${l.rooms} өрөө</strong> · <strong>${l.area_sqm} m²</strong> · ${floorTxt} ${termsTxt ? "· " + termsTxt : ""}
              </div>
              <div>${winBadge}${parkingBadge(l.parking_status)}${floorBadge}${cutBadge}${dupBadge}</div>
              <div class="l-title">${l.title}</div>
              <div class="l-footer">
                <span style="color:var(--muted);">${l.published_label || l.first_seen_at.slice(0, 10)}</span>
                <a class="btn-link" href="${l.url}" target="_blank" rel="noopener">Open on Unegui ↗</a>
              </div>
            </div>
          </article>
        `;
      }).join("");
    }

    function stepPhoto(ev, delta) {
      ev.stopPropagation();
      const box = ev.currentTarget.closest(".photo-box");
      const imgs = JSON.parse(box.dataset.imgs || "[]");
      if (imgs.length <= 1) return;
      const next = (Number(box.dataset.idx || 0) + delta + imgs.length) % imgs.length;
      box.dataset.idx = String(next);
      box.querySelector("img").src = imgs[next];
      const counter = box.querySelector(".photo-count");
      if (counter) counter.textContent = `${next + 1} / ${imgs.length}`;
    }

    function openLightbox(src) {
      document.getElementById("lbImg").src = src;
      document.getElementById("lightbox").classList.add("open");
    }

    function closeLightbox(ev) {
      if (ev.target.id === "lightbox" || ev.target.classList.contains("lb-close")) {
        document.getElementById("lightbox").classList.remove("open");
      }
    }

    function renderRvbTable() {
      document.getElementById("rvbBody").innerHTML = DATA.complexes.map((c) => `
        <tr>
          <td><strong>${c.name}</strong></td>
          <td style="color:var(--muted);">${c.zone}</td>
          <td class="num">${c.sale_units}</td>
          <td class="num">${fmtSqm(c.sale_median_sqm_mnt)}</td>
          <td class="num">${fmtM(c.sale_median_total_mnt)}</td>
          <td class="num">${c.rent_units}</td>
          <td class="num">${fmtM(c.median_monthly_rent_mnt)}</td>
          <td class="num">${c.gross_yield_pct_sqm != null ? c.gross_yield_pct_sqm.toFixed(2) + "%" : "—"}</td>
          <td class="num">${c.price_to_rent_years != null ? c.price_to_rent_years.toFixed(1) + " yrs" : "—"}</td>
        </tr>
      `).join("");
    }

    function renderDropsTable() {
      const cuts = DATA.listings
        .filter((l) => l.effective_drop_pct && l.effective_drop_pct > 0)
        .sort((a, b) => b.effective_drop_pct - a.effective_drop_pct);

      const body = document.getElementById("dropsBody");
      if (!cuts.length) {
        body.innerHTML = `<tr><td colspan="9" class="empty">No active price cuts yet. Daily scans will surface reductions automatically.</td></tr>`;
        return;
      }
      body.innerHTML = cuts.map((l) => {
        const orig = l.start_price_mnt || Math.round(l.total_price_mnt / (1 - l.effective_drop_pct / 100));
        const prefs = [l.window_facing, l.parking_status === "included" ? "Parking" : "", l.floor_tier === "high" ? "High F" : ""].filter(Boolean).join(", ");
        return `
          <tr>
            <td><span class="badge ${l.deal_type === "rent" ? "b-rent" : "b-sale"}">${l.deal_type.toUpperCase()}</span></td>
            <td><strong>${l.complex_name}</strong></td>
            <td>${l.rooms} өрөө · ${l.area_sqm} m² · ${l.floor || "?"}F</td>
            <td style="color:var(--muted);">${prefs || "—"}</td>
            <td class="num">${fmtM(orig)}</td>
            <td class="num"><strong>${fmtM(l.total_price_mnt)}</strong></td>
            <td class="num"><span class="badge b-cut">-${l.effective_drop_pct.toFixed(1)}%</span></td>
            <td class="num">${l.duplicate_listing_count}x</td>
            <td><a href="${l.url}" target="_blank" rel="noopener">View ↗</a></td>
          </tr>
        `;
      }).join("");
    }

    let scatterChart = null;
    function renderCharts() {
      if (typeof Chart === "undefined") return;

      const saleC = DATA.complexes.filter((c) => c.sale_median_sqm_mnt);
      new Chart(document.getElementById("chartSaleSqm"), {
        type: "bar",
        data: {
          labels: saleC.map((c) => c.name),
          datasets: [{
            label: "Median Sale M ₮/m²",
            data: saleC.map((c) => +(c.sale_median_sqm_mnt / 1e6).toFixed(2)),
            backgroundColor: "#0f766e",
            borderRadius: 6,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true, title: { display: true, text: "M ₮ / m²" } } },
        },
      });

      const rentC = DATA.complexes.filter((c) => c.median_monthly_rent_mnt);
      new Chart(document.getElementById("chartRentMo"), {
        type: "bar",
        data: {
          labels: rentC.map((c) => c.name),
          datasets: [{
            label: "Median Rent M ₮/mo",
            data: rentC.map((c) => +(c.median_monthly_rent_mnt / 1e6).toFixed(2)),
            backgroundColor: "#2563eb",
            borderRadius: 6,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true, title: { display: true, text: "M ₮ / month" } } },
        },
      });

      renderScatter("sale");

      // Trend line chart
      const days = [...new Set(DATA.trends.map((t) => t.day))].sort();
      const saleByDay = Object.fromEntries(DATA.trends.filter((t) => t.deal_type === "sale").map((t) => [t.day, +(t.avg_sqm_mnt / 1e6).toFixed(2)]));
      const rentByDay = Object.fromEntries(DATA.trends.filter((t) => t.deal_type === "rent").map((t) => [t.day, +(t.avg_total_mnt / 1e6).toFixed(2)]));
      new Chart(document.getElementById("chartTrends"), {
        type: "line",
        data: {
          labels: days,
          datasets: [
            { label: "Avg Sale (M ₮/m²)", data: days.map((d) => saleByDay[d] ?? null), borderColor: "#0f766e", tension: 0.25 },
            { label: "Avg Rent (M ₮/mo)", data: days.map((d) => rentByDay[d] ?? null), borderColor: "#2563eb", tension: 0.25 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { y: { beginAtZero: false } },
        },
      });
    }

    function renderScatter(deal) {
      if (typeof Chart === "undefined") return;
      if (scatterChart) scatterChart.destroy();
      const palette = ["#0f766e","#2563eb","#d97706","#dc2626","#7c3aed","#059669","#db2777","#4f46e5","#ea580c","#0284c7","#65a30d","#9333ea"];
      const datasets = DATA.complexes.map((c, idx) => {
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
          backgroundColor: palette[idx % palette.length],
          pointRadius: 5,
          pointHoverRadius: 7,
        };
      }).filter((ds) => ds.data.length > 0);

      scatterChart = new Chart(document.getElementById("chartScatter"), {
        type: "scatter",
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          onClick: (_, elements) => {
            if (!elements.length) return;
            const el = elements[0];
            const pt = scatterChart.data.datasets[el.datasetIndex].data[el.index];
            if (pt && pt.url) window.open(pt.url, "_blank", "noopener");
          },
          plugins: {
            tooltip: {
              callbacks: { label: (ctx) => ctx.raw.label },
            },
          },
          scales: {
            x: { title: { display: true, text: "Area (m²)" } },
            y: { title: { display: true, text: deal === "sale" ? "Total Price (M ₮)" : "Monthly Rent (M ₮)" } },
          },
        },
      });
    }

    function updateAll() {
      const items = getFiltered();
      renderKPIs(items);
      renderCards(items);
    }

    // Wire inputs
    ["fDeal", "fComplex", "fRooms", "fSort"].forEach((id) => {
      document.getElementById(id).addEventListener("change", (e) => {
        state[id.slice(1).toLowerCase()] = e.target.value;
        updateAll();
      });
    });
    document.getElementById("fMinArea").addEventListener("input", (e) => { state.minArea = e.target.value; updateAll(); });
    document.getElementById("fMaxPrice").addEventListener("input", (e) => { state.maxPrice = e.target.value; updateAll(); });
    document.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const k = chip.dataset.key;
        state[k] = !state[k];
        chip.classList.toggle("on", state[k]);
        updateAll();
      });
    });
    document.getElementById("btnReset").addEventListener("click", () => {
      Object.assign(state, { deal: "all", complex: "all", rooms: "all", minArea: "", maxPrice: "", sort: "family", southWest: false, parking: false, highFloor: false, priceCut: false });
      document.getElementById("fDeal").value = "all";
      document.getElementById("fComplex").value = "all";
      document.getElementById("fRooms").value = "all";
      document.getElementById("fMinArea").value = "";
      document.getElementById("fMaxPrice").value = "";
      document.getElementById("fSort").value = "family";
      document.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
      updateAll();
    });
    document.getElementById("scatterDeal").addEventListener("change", (e) => renderScatter(e.target.value));

    // Tabs
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
      });
    });

    updateAll();
    renderRvbTable();
    renderDropsTable();
    renderCharts();
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
    # Prevent closing </script> sequences inside JSON strings
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DASHBOARD_JSON__", safe_json)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
