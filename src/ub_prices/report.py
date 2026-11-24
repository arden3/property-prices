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


def _fmt_floor(floor: Optional[int], total: Optional[int]) -> str:
    if floor is None:
        return "—"
    if total is None:
        return f"{floor}F"
    return f"{floor}/{total}F"


def generate_report(
    config: AppConfig,
    data_dir: Path,
    output_path: Path,
    db_path: Optional[Path] = None,
    lookback_hours: int = 24,
) -> str:
    """Build SQLite DB (if needed) and generate a clean family-friendly Markdown digest."""
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

        new_rent = conn.execute(
            """
            SELECT complex_name, rooms, area_sqm, floor, total_floors, total_price_mnt,
                   payment_terms, furnished, url, title
            FROM listings
            WHERE is_active = 1 AND deal_type = 'rent' AND first_seen_at >= ?
            ORDER BY complex_name, total_price_mnt ASC
            LIMIT 40
            """,
            (cutoff_iso,),
        ).fetchall()

        new_sale = conn.execute(
            """
            SELECT complex_name, rooms, area_sqm, floor, total_floors, total_price_mnt,
                   price_per_sqm_mnt, price_anomaly, url, title
            FROM listings
            WHERE is_active = 1 AND deal_type = 'sale' AND first_seen_at >= ?
            ORDER BY complex_name, price_per_sqm_mnt ASC
            LIMIT 40
            """,
            (cutoff_iso,),
        ).fetchall()

        drops = conn.execute(
            "SELECT * FROM v_price_drops LIMIT 20"
        ).fetchall()

        rvb_rows = {
            row["complex_slug"]: dict(row)
            for row in conn.execute("SELECT * FROM v_rent_vs_buy").fetchall()
        }

        sale_by_rooms = conn.execute("SELECT * FROM v_sale_by_rooms").fetchall()
        rent_by_rooms = conn.execute("SELECT * FROM v_rent_by_rooms").fetchall()

        total_active_sale = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND deal_type = 'sale'"
        ).fetchone()[0]
        total_active_rent = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND deal_type = 'rent'"
        ).fetchone()[0]
    finally:
        conn.close()

    lines: list[str] = []
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("# Ulaanbaatar Property Prices — Daily Digest")
    lines.append("")
    lines.append(f"_Generated: {generated_at} · Scope: 12 Khan-Uul / Zaisan complexes · 3–5 өрөө (2–4 bed) · ≥ 60 m²_")
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
        f"- **Database Totals (Active):** {total_active_sale} sale listings · {total_active_rent} rent listings"
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

    # 3. New Rental Listings
    lines.append(f"## New Rental Listings (Last {lookback_hours}h)")
    lines.append("")
    if new_rent:
        lines.append("| Complex | Rooms | Area | Floor | Rent/mo | Terms | Furnished | Link |")
        lines.append("| :--- | ---: | ---: | ---: | ---: | :--- | :--- | :--- |")
        for r in new_rent:
            lines.append(
                f"| {r['complex_name']} | {r['rooms']} өрөө | {_fmt_area(r['area_sqm'])} | "
                f"{_fmt_floor(r['floor'], r['total_floors'])} | "
                f"{_fmt_mnt_millions(r['total_price_mnt'])} | "
                f"{r['payment_terms'] or '—'} | {r['furnished'] or '—'} | "
                f"[View]({r['url']}) |"
            )
    else:
        lines.append("_No new rental listings in this window._")
    lines.append("")

    # 4. New Sale Listings
    lines.append(f"## New Sale Listings (Last {lookback_hours}h)")
    lines.append("")
    if new_sale:
        lines.append("| Complex | Rooms | Area | Floor | Total Price | ₮/m² | Flag | Link |")
        lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | :--- | :--- |")
        for r in new_sale:
            flag = "⚠️ anomaly" if r["price_anomaly"] else ""
            lines.append(
                f"| {r['complex_name']} | {r['rooms']} өрөө | {_fmt_area(r['area_sqm'])} | "
                f"{_fmt_floor(r['floor'], r['total_floors'])} | "
                f"{_fmt_mnt_millions(r['total_price_mnt'])} | "
                f"{_fmt_sqm_mnt(r['price_per_sqm_mnt'])} | {flag} | "
                f"[View]({r['url']}) |"
            )
    else:
        lines.append("_No new sale listings in this window._")
    lines.append("")

    # 5. Price Drops
    lines.append("## Active Price Drops")
    lines.append("")
    if drops:
        lines.append("| Deal | Complex | Rooms | Area | First Price | Current | Drop | Days | Link |")
        lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |")
        for d in drops:
            lines.append(
                f"| {d['deal_type']} | {d['complex_name']} | {d['rooms']} өрөө | "
                f"{_fmt_area(d['area_sqm'])} | {_fmt_mnt_millions(d['first_price_mnt'])} | "
                f"{_fmt_mnt_millions(d['current_price_mnt'])} | -{d['drop_pct']:.1f}% | "
                f"{d['days_tracked']}d | [View]({d['url']}) |"
            )
    else:
        lines.append("_No active price drops recorded yet (tracked automatically as daily scans accumulate)._")
    lines.append("")

    # 6. Breakdown by Rooms (only if data exists)
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
