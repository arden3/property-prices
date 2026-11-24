"""Generate daily Markdown digest (reports/latest.md + GitHub Actions Job Summary)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ub_prices.config import AppConfig
from ub_prices.storage.sqlite_builder import build_sqlite_db


def _fmt_mnt_millions(val: Optional[int]) -> str:
    if val is None:
        return "—"
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.2f}B ₮"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M ₮"
    return f"{val:,} ₮"


def _fmt_sqm_mnt(val: Optional[int]) -> str:
    if val is None:
        return "—"
    return f"{val / 1_000_000:.2f}M ₮/m²"


def _fmt_area(val: Optional[float]) -> str:
    if val is None:
        return "—"
    return f"{val:.1f} m²"


def _fmt_floor(floor: Optional[int], total: Optional[int], tier: str = "") -> str:
    if floor is None:
        return "—"
    base = f"{floor}F" if total is None else f"{floor}/{total}F"
    return f"{base} (high)" if tier == "high" else base


def _fmt_parking(status: str) -> str:
    return {
        "included": "Incl.",
        "available": "Avail.",
        "none": "No",
    }.get(status or "", "—")


def generate_report(
    config: AppConfig,
    data_dir: Path,
    output_path: Path,
    db_path: Optional[Path] = None,
    lookback_hours: int = 24,
) -> str:
    """Build SQLite DB (if needed) and generate a deduplicated family-focused Markdown digest."""
    target_db = db_path or (data_dir / "ub_prices.db")
    if not target_db.exists():
        build_sqlite_db(data_dir, target_db)

    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    try:
        latest_run = conn.execute(
            "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        ).replace(microsecond=0).isoformat()

        # Family shortlist top picks (South + West, Interior Parking, Higher Floor)
        shortlist_rent = conn.execute(
            """
            SELECT * FROM v_family_shortlist
            WHERE deal_type = 'rent' AND family_score >= 3
            ORDER BY family_score DESC, total_price_mnt ASC
            LIMIT 15
            """
        ).fetchall()

        shortlist_sale = conn.execute(
            """
            SELECT * FROM v_family_shortlist
            WHERE deal_type = 'sale' AND family_score >= 3
            ORDER BY family_score DESC, price_per_sqm_mnt ASC
            LIMIT 15
            """
        ).fetchall()

        # Deduplicated new listings in lookback window
        new_rent = conn.execute(
            """
            SELECT * FROM v_family_shortlist
            WHERE deal_type = 'rent' AND first_seen_at >= ?
            ORDER BY family_score DESC, complex_name, total_price_mnt ASC
            LIMIT 40
            """,
            (cutoff_iso,),
        ).fetchall()

        new_sale = conn.execute(
            """
            SELECT * FROM v_family_shortlist
            WHERE deal_type = 'sale' AND first_seen_at >= ?
            ORDER BY family_score DESC, complex_name, price_per_sqm_mnt ASC
            LIMIT 40
            """,
            (cutoff_iso,),
        ).fetchall()

        # Combined price cuts (both tracked daily drops and Unegui start_price cuts)
        price_cuts = conn.execute(
            """
            SELECT * FROM v_family_shortlist
            WHERE effective_drop_pct > 0
            ORDER BY effective_drop_pct DESC, total_price_mnt ASC
            LIMIT 25
            """
        ).fetchall()

        rvb_rows = {
            row["complex_slug"]: dict(row)
            for row in conn.execute("SELECT * FROM v_rent_vs_buy").fetchall()
        }

        sale_by_rooms = conn.execute("SELECT * FROM v_sale_by_rooms").fetchall()
        rent_by_rooms = conn.execute("SELECT * FROM v_rent_by_rooms").fetchall()

        raw_sale = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND deal_type = 'sale'"
        ).fetchone()[0]
        raw_rent = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND deal_type = 'rent'"
        ).fetchone()[0]
        dedup_sale = conn.execute(
            "SELECT COUNT(*) FROM v_active_deduped WHERE deal_type = 'sale'"
        ).fetchone()[0]
        dedup_rent = conn.execute(
            "SELECT COUNT(*) FROM v_active_deduped WHERE deal_type = 'rent'"
        ).fetchone()[0]
    finally:
        conn.close()

    lines: list[str] = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("# Ulaanbaatar Property Prices — Daily Digest")
    lines.append("")
    lines.append(
        f"_Generated: {generated_at} · Scope: 12 Khan-Uul / Zaisan complexes · 3–5 өрөө (2–4 bed) · ≥ 60 m²_"
    )
    lines.append("")

    # 1. Scan Health
    lines.append("## Latest Scan Status")
    if latest_run:
        lines.append(
            f"- **Status:** `{latest_run['status']}` · **Source:** `{latest_run['source']}` · "
            f"**Run ID:** `{latest_run['run_id']}`"
        )
        lines.append(
            f"- **Activity:** {latest_run['requests_made']} requests · "
            f"{latest_run['matched_candidates']} matched ads · "
            f"**+{latest_run['new_listings']} new** · "
            f"**{latest_run['price_changes']} price changes** · "
            f"**{latest_run['delisted_count']} delisted**"
        )
        if latest_run["notes"]:
            lines.append(f"- **Notes:** `{latest_run['notes']}`")
    else:
        lines.append("- No scans recorded yet.")
    lines.append(
        f"- **Active Inventory:** **{dedup_sale} unique sale units** ({raw_sale} broker ads) · "
        f"**{dedup_rent} unique rent units** ({raw_rent} broker ads)"
    )
    lines.append("")

    # 2. All-12-Complex Market Snapshot (Rent vs. Buy)
    lines.append("## 12-Complex Market Snapshot (Deduplicated Active Units)")
    lines.append("")
    lines.append(
        "| Complex | Zone | Sale Units | Median Sale ₮/m² | Median Sale Total | Rent Units | Median Rent/mo | Est. Gross Yield | Price / Annual Rent |"
    )
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for comp in config.complexes:
        row = rvb_rows.get(comp.slug)
        if row:
            yld = f"{row['gross_yield_pct_sqm']:.2f}%" if row["gross_yield_pct_sqm"] is not None else "—"
            ptr = f"{row['price_to_rent_years']:.1f} yrs" if row["price_to_rent_years"] is not None else "—"
            lines.append(
                f"| **{comp.name}** | {comp.zone} | {row['sale_units']} | "
                f"{_fmt_sqm_mnt(row['sale_median_sqm_mnt'])} | "
                f"{_fmt_mnt_millions(row['sale_median_total_mnt'])} | "
                f"{row['rent_units']} | "
                f"{_fmt_mnt_millions(row['median_monthly_rent_mnt'])} | "
                f"{yld} | {ptr} |"
            )
        else:
            lines.append(f"| **{comp.name}** | {comp.zone} | 0 | — | — | 0 | — | — | — |")
    lines.append("")

    # 3. Family Shortlist Top Matches (South + West, Interior Parking, Higher Floor)
    lines.append("## Family Shortlist Top Matches (South/West · Parking · Higher Floor)")
    lines.append("")
    if shortlist_rent:
        lines.append("### Top Rent Matches")
        lines.append("| Score | Complex | Rooms | Area | Floor | Windows | Parking | Rent/mo | Terms | Ads | Link |")
        lines.append("| :---: | :--- | ---: | ---: | :--- | :--- | :--- | ---: | :--- | ---: | :--- |")
        for r in shortlist_rent:
            lines.append(
                f"| {r['family_score']}/6 | {r['complex_name']} | {r['rooms']} өрөө | "
                f"{_fmt_area(r['area_sqm'])} | {_fmt_floor(r['floor'], r['total_floors'], r['floor_tier'])} | "
                f"{r['window_facing'] or '—'} | {_fmt_parking(r['parking_status'])} | "
                f"{_fmt_mnt_millions(r['total_price_mnt'])} | {r['payment_terms'] or '—'} | "
                f"{r['duplicate_listing_count']}x | [View]({r['url']}) |"
            )
        lines.append("")
    if shortlist_sale:
        lines.append("### Top Sale Matches")
        lines.append("| Score | Complex | Rooms | Area | Floor | Windows | Parking | Total Price | ₮/m² | Ads | Link |")
        lines.append("| :---: | :--- | ---: | ---: | :--- | :--- | :--- | ---: | ---: | ---: | :--- |")
        for r in shortlist_sale:
            lines.append(
                f"| {r['family_score']}/6 | {r['complex_name']} | {r['rooms']} өрөө | "
                f"{_fmt_area(r['area_sqm'])} | {_fmt_floor(r['floor'], r['total_floors'], r['floor_tier'])} | "
                f"{r['window_facing'] or '—'} | {_fmt_parking(r['parking_status'])} | "
                f"{_fmt_mnt_millions(r['total_price_mnt'])} | {_fmt_sqm_mnt(r['price_per_sqm_mnt'])} | "
                f"{r['duplicate_listing_count']}x | [View]({r['url']}) |"
            )
        lines.append("")
    if not shortlist_rent and not shortlist_sale:
        lines.append("_No high-scoring shortlist matches yet._")
        lines.append("")

    # 4. Active Price Cuts (Tracked + Unegui start_price)
    lines.append("## Active Price Cuts (Tracked Drops & Seller Start-Price Reductions)")
    lines.append("")
    if price_cuts:
        lines.append("| Deal | Complex | Rooms | Area | Floor | Original | Current | Cut | Ads | Link |")
        lines.append("| :--- | :--- | ---: | ---: | :--- | ---: | ---: | ---: | ---: | :--- |")
        for c in price_cuts:
            orig = c["start_price_mnt"]
            if orig is None and c["effective_drop_pct"]:
                orig = int(round(c["total_price_mnt"] / (1.0 - c["effective_drop_pct"] / 100.0)))
            lines.append(
                f"| {c['deal_type']} | {c['complex_name']} | {c['rooms']} өрөө | "
                f"{_fmt_area(c['area_sqm'])} | {_fmt_floor(c['floor'], c['total_floors'])} | "
                f"{_fmt_mnt_millions(orig)} | {_fmt_mnt_millions(c['total_price_mnt'])} | "
                f"-{c['effective_drop_pct']:.1f}% | {c['duplicate_listing_count']}x | [View]({c['url']}) |"
            )
    else:
        lines.append("_No active price cuts recorded yet._")
    lines.append("")

    # 5. Deduplicated New Listings (Last 24h)
    lines.append(f"## New Unique Rental Listings (Last {lookback_hours}h)")
    lines.append("")
    if new_rent:
        lines.append("| Complex | Rooms | Area | Floor | Windows | Parking | Rent/mo | Terms | Ads | Link |")
        lines.append("| :--- | ---: | ---: | :--- | :--- | :--- | ---: | :--- | ---: | :--- |")
        for r in new_rent:
            lines.append(
                f"| {r['complex_name']} | {r['rooms']} өрөө | {_fmt_area(r['area_sqm'])} | "
                f"{_fmt_floor(r['floor'], r['total_floors'], r['floor_tier'])} | "
                f"{r['window_facing'] or '—'} | {_fmt_parking(r['parking_status'])} | "
                f"{_fmt_mnt_millions(r['total_price_mnt'])} | {r['payment_terms'] or '—'} | "
                f"{r['duplicate_listing_count']}x | [View]({r['url']}) |"
            )
    else:
        lines.append("_No new rental listings in this window._")
    lines.append("")

    lines.append(f"## New Unique Sale Listings (Last {lookback_hours}h)")
    lines.append("")
    if new_sale:
        lines.append("| Complex | Rooms | Area | Floor | Windows | Parking | Total Price | ₮/m² | Ads | Link |")
        lines.append("| :--- | ---: | ---: | :--- | :--- | :--- | ---: | ---: | ---: | :--- |")
        for r in new_sale:
            lines.append(
                f"| {r['complex_name']} | {r['rooms']} өрөө | {_fmt_area(r['area_sqm'])} | "
                f"{_fmt_floor(r['floor'], r['total_floors'], r['floor_tier'])} | "
                f"{r['window_facing'] or '—'} | {_fmt_parking(r['parking_status'])} | "
                f"{_fmt_mnt_millions(r['total_price_mnt'])} | {_fmt_sqm_mnt(r['price_per_sqm_mnt'])} | "
                f"{r['duplicate_listing_count']}x | [View]({r['url']}) |"
            )
    else:
        lines.append("_No new sale listings in this window._")
    lines.append("")

    # 6. Breakdown by Rooms
    if sale_by_rooms or rent_by_rooms:
        lines.append("## Breakdown by Room Count (3–5 өрөө)")
        lines.append("")
        if sale_by_rooms:
            lines.append("### Sale")
            lines.append("| Complex | Rooms | Unique Units | Avg Area | Min ₮/m² | Avg ₮/m² | Max ₮/m² | Avg Total |")
            lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
            for row in sale_by_rooms:
                lines.append(
                    f"| {row['complex_name']} | {row['rooms']} өрөө | {row['active_unique_units']} | "
                    f"{_fmt_area(row['avg_area_sqm'])} | {_fmt_sqm_mnt(row['min_sqm_mnt'])} | "
                    f"{_fmt_sqm_mnt(row['avg_sqm_mnt'])} | {_fmt_sqm_mnt(row['max_sqm_mnt'])} | "
                    f"{_fmt_mnt_millions(row['avg_total_mnt'])} |"
                )
            lines.append("")
        if rent_by_rooms:
            lines.append("### Rent")
            lines.append("| Complex | Rooms | Unique Units | Avg Area | Min Rent/mo | Avg Rent/mo | Max Rent/mo |")
            lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for row in rent_by_rooms:
                lines.append(
                    f"| {row['complex_name']} | {row['rooms']} өрөө | {row['active_unique_units']} | "
                    f"{_fmt_area(row['avg_area_sqm'])} | {_fmt_mnt_millions(row['min_monthly_rent_mnt'])} | "
                    f"{_fmt_mnt_millions(row['avg_monthly_rent_mnt'])} | "
                    f"{_fmt_mnt_millions(row['max_monthly_rent_mnt'])} |"
                )
            lines.append("")

    md = "\n".join(lines).rstrip() + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    return md
