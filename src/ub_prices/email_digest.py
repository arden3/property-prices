"""Build and send a 3-day HTML email digest of the best new UB apartment finds."""

from __future__ import annotations

import os
import re
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Any, Optional

from ub_prices.config import AppConfig
from ub_prices.storage.sqlite_builder import build_sqlite_db

# Match dashboard budget caps so email highlights focus on in-budget finds
RENT_BUDGET_MAX_MNT = 6_100_000
SALE_BUDGET_MAX_MNT = 1_200_000_000

# Mongolian published_label patterns indicating genuinely recent postings
_RECENT_SUBDAY_RE = re.compile(
    r"(?:өнөөдөр|өчигдөр|цагийн\s*өмнө|минутын\s*өмнө|саяхан)",
    flags=re.IGNORECASE | re.UNICODE,
)
_DAYS_AGO_RE = re.compile(
    r"^\s*(\d+)\s*өдрийн\s*өмнө",
    flags=re.IGNORECASE | re.UNICODE,
)
_OLD_POST_RE = re.compile(
    r"(?:долоо\s*хоногийн|сарын|жилийн)",
    flags=re.IGNORECASE | re.UNICODE,
)


def is_fresh_find(
    published_label: str,
    first_seen_at: str,
    lookback_days: int = 3,
    now_utc: Optional[datetime] = None,
) -> bool:
    """Return True if a listing is genuinely fresh (posted recently OR newly seen and not obviously old)."""
    pub = (published_label or "").strip()
    if pub:
        m_days = _DAYS_AGO_RE.search(pub)
        if m_days:
            return int(m_days.group(1)) <= lookback_days
        if _RECENT_SUBDAY_RE.search(pub):
            return True
        if _OLD_POST_RE.search(pub):
            return False

    if not first_seen_at:
        return False
    now = now_utc or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).replace(microsecond=0).isoformat()
    return first_seen_at >= cutoff


def _fmt_mnt(val: Optional[int]) -> str:
    if val is None:
        return "—"
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.2f}B ₮"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M ₮"
    return f"{val:,} ₮"


def _fmt_sqm(val: Optional[int]) -> str:
    if val is None:
        return "—"
    return f"{val / 1_000_000:.2f}M ₮/m²"


def _fmt_floor(row: dict[str, Any]) -> str:
    fl, tf = row.get("floor"), row.get("total_floors")
    if fl is None:
        return "—"
    return f"{fl}/{tf}F" if tf else f"{fl}F"


def _prefs_summary(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row.get("window_facing"):
        parts.append(f"☀️ {row['window_facing']}")
    ps = row.get("parking_status")
    if ps == "included":
        parts.append("🚗 Parking incl.")
    elif ps == "available":
        parts.append("🚗 Parking avail.")
    if row.get("floor_tier") == "high":
        parts.append("🏢 High floor")
    if row.get("effective_drop_pct") and row["effective_drop_pct"] > 0:
        parts.append(f"📉 -{row['effective_drop_pct']:.1f}%")
    if row.get("duplicate_listing_count", 1) > 1:
        parts.append(f"👥 {row['duplicate_listing_count']} ads")
    return " · ".join(parts)


def collect_digest_data(
    config: AppConfig,
    data_dir: Path,
    db_path: Optional[Path] = None,
    lookback_days: int = 3,
    top_n: int = 8,
    now_utc: Optional[datetime] = None,
) -> dict[str, Any]:
    """Query SQLite views for the best new in-budget Rent & Buy finds and price cuts."""
    target_db = db_path or (data_dir / "ub_prices.db")
    if not target_db.exists():
        build_sqlite_db(data_dir, target_db)

    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    try:
        all_rows = [dict(r) for r in conn.execute("SELECT * FROM v_family_shortlist").fetchall()]
        rvb_map = {
            r["complex_slug"]: dict(r)
            for r in conn.execute("SELECT * FROM v_rent_vs_buy").fetchall()
        }
    finally:
        conn.close()

    fresh_rents = [
        r
        for r in all_rows
        if r["deal_type"] == "rent"
        and r["total_price_mnt"] is not None
        and r["total_price_mnt"] <= RENT_BUDGET_MAX_MNT
        and is_fresh_find(r.get("published_label", ""), r.get("first_seen_at", ""), lookback_days, now_utc)
    ]
    fresh_rents.sort(key=lambda r: (-r["family_score"], r["total_price_mnt"] or 10**18))

    fresh_sales = [
        r
        for r in all_rows
        if r["deal_type"] == "sale"
        and r["total_price_mnt"] is not None
        and r["total_price_mnt"] < SALE_BUDGET_MAX_MNT
        and is_fresh_find(r.get("published_label", ""), r.get("first_seen_at", ""), lookback_days, now_utc)
    ]
    fresh_sales.sort(key=lambda r: (-r["family_score"], r["price_per_sqm_mnt"] or 10**18))

    cuts = [
        r
        for r in all_rows
        if r.get("effective_drop_pct")
        and r["effective_drop_pct"] > 0
        and (
            (r["deal_type"] == "rent" and (r["total_price_mnt"] or 0) <= RENT_BUDGET_MAX_MNT)
            or (r["deal_type"] == "sale" and (r["total_price_mnt"] or 0) < SALE_BUDGET_MAX_MNT)
        )
    ]
    cuts.sort(key=lambda r: (-r["effective_drop_pct"], -r["family_score"]))

    complexes = []
    for c in config.complexes:
        row = rvb_map.get(c.slug, {})
        complexes.append(
            {
                "name": c.name,
                "rent_units": row.get("rent_units", 0),
                "median_rent": row.get("median_monthly_rent_mnt"),
                "sale_units": row.get("sale_units", 0),
                "median_sale_sqm": row.get("sale_median_sqm_mnt"),
                "gross_yield": row.get("gross_yield_pct_sqm"),
            }
        )

    return {
        "generated_date": (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
        "lookback_days": lookback_days,
        "fresh_rents_total": len(fresh_rents),
        "fresh_sales_total": len(fresh_sales),
        "top_rents": fresh_rents[:top_n],
        "top_sales": fresh_sales[:top_n],
        "top_cuts": cuts[:6],
        "complexes": complexes,
    }


def _render_cards_html(rows: list[dict[str, Any]], is_rent: bool) -> str:
    if not rows:
        label = "rentals (≤ 6.1M ₮/mo)" if is_rent else "apartments for sale (< 1.2B ₮)"
        return f'<p style="color:#64748b;font-size:14px;margin:8px 0 18px;">No new {label} in the last 3 days.</p>'

    accent = "#4338ca" if is_rent else "#d97706"
    soft_bg = "#eef2ff" if is_rent else "#fffbeb"
    blocks: list[str] = []
    for r in rows:
        price_str = f"{_fmt_mnt(r['total_price_mnt'])}{'/mo' if is_rent else ''}"
        sub_price = (
            f"{round(r['price_per_sqm_mnt'] / 1000)}k ₮/m²"
            if is_rent and r.get("price_per_sqm_mnt")
            else _fmt_sqm(r.get("price_per_sqm_mnt"))
        )
        terms = f" · {escape(r['payment_terms'])}" if is_rent and r.get("payment_terms") else ""
        prefs = _prefs_summary(r)
        blocks.append(
            f"""
            <div style="border:1px solid #e2e8f0;border-left:4px solid {accent};border-radius:10px;padding:12px 14px;margin-bottom:10px;background:#ffffff;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="font-weight:700;font-size:15px;color:#1e293b;">
                    {escape(r['complex_name'])}
                    <span style="display:inline-block;margin-left:6px;padding:2px 8px;border-radius:999px;background:{soft_bg};color:{accent};font-size:11px;font-weight:700;">Match {r['family_score']}/6</span>
                  </td>
                  <td align="right" style="font-weight:800;font-size:16px;color:{accent};white-space:nowrap;">
                    {escape(price_str)}
                  </td>
                </tr>
                <tr>
                  <td style="padding-top:4px;font-size:13px;color:#334155;">
                    <strong>{r['rooms']} өрөө</strong> · <strong>{r['area_sqm']} m²</strong> · {_fmt_floor(r)}{terms}
                  </td>
                  <td align="right" style="padding-top:4px;font-size:12px;color:#64748b;white-space:nowrap;">
                    {escape(sub_price)}
                  </td>
                </tr>
              </table>
              {f'<div style="margin-top:6px;font-size:12px;color:#0f766e;font-weight:600;">{escape(prefs)}</div>' if prefs else ''}
              <div style="margin-top:6px;font-size:12px;color:#64748b;">{escape(r['title'][:110])}</div>
              <div style="margin-top:8px;">
                <a href="{escape(r['url'])}" style="display:inline-block;background:{accent};color:#ffffff;text-decoration:none;padding:6px 12px;border-radius:7px;font-size:12px;font-weight:700;">View Photos &amp; Listing on Unegui ↗</a>
                <span style="margin-left:8px;font-size:11px;color:#94a3b8;">{escape(r.get('published_label') or r['first_seen_at'][:10])}</span>
              </div>
            </div>
            """
        )
    return "\n".join(blocks)


def render_email_digest(
    data: dict[str, Any],
    dashboard_url: str = "https://arden3.github.io/property-prices/",
) -> tuple[str, str, str]:
    """Return (subject, plain_text_body, html_body) for the 3-day family digest."""
    dt = data["generated_date"]
    subject = (
        f"UB Apartments Digest ({dt}): "
        f"{data['fresh_rents_total']} new rentals · {data['fresh_sales_total']} new sales"
    )

    # Plain-text fallback
    txt: list[str] = [
        f"Ulaanbaatar Family Property Digest ({dt})",
        f"Interactive Dashboard: {dashboard_url}",
        "",
        f"=== BEST NEW RENTALS (<= 6.1M MNT/mo, last {data['lookback_days']} days) ===",
    ]
    for r in data["top_rents"]:
        txt.append(
            f"- [{r['family_score']}/6] {r['complex_name']} | {r['rooms']} өрөө, {r['area_sqm']}m², {_fmt_floor(r)} | "
            f"{_fmt_mnt(r['total_price_mnt'])}/mo | {_prefs_summary(r)} | {r['url']}"
        )
    if not data["top_rents"]:
        txt.append("- None in this window.")

    txt.append("")
    txt.append(f"=== BEST NEW FOR SALE (< 1.2B MNT, last {data['lookback_days']} days) ===")
    for r in data["top_sales"]:
        txt.append(
            f"- [{r['family_score']}/6] {r['complex_name']} | {r['rooms']} өрөө, {r['area_sqm']}m², {_fmt_floor(r)} | "
            f"{_fmt_mnt(r['total_price_mnt'])} ({_fmt_sqm(r['price_per_sqm_mnt'])}) | {_prefs_summary(r)} | {r['url']}"
        )
    if not data["top_sales"]:
        txt.append("- None in this window.")

    plain_body = "\n".join(txt) + "\n"

    # Cuts rows HTML
    cuts_html = ""
    if data["top_cuts"]:
        c_rows = []
        for c in data["top_cuts"]:
            deal_lbl = "RENT" if c["deal_type"] == "rent" else "BUY"
            c_rows.append(
                f"""
                <tr>
                  <td style="padding:7px 8px;border-bottom:1px solid #e2e8f0;font-size:12px;font-weight:700;color:{'#4338ca' if deal_lbl=='RENT' else '#d97706'};">{deal_lbl}</td>
                  <td style="padding:7px 8px;border-bottom:1px solid #e2e8f0;font-size:13px;font-weight:600;color:#1e293b;">{escape(c['complex_name'])}</td>
                  <td style="padding:7px 8px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#475569;">{c['rooms']} өрөө · {c['area_sqm']}m²</td>
                  <td align="right" style="padding:7px 8px;border-bottom:1px solid #e2e8f0;font-size:13px;font-weight:700;">{escape(_fmt_mnt(c['total_price_mnt']))}</td>
                  <td align="right" style="padding:7px 8px;border-bottom:1px solid #e2e8f0;font-size:12px;font-weight:700;color:#be123c;">-{c['effective_drop_pct']:.1f}%</td>
                  <td align="right" style="padding:7px 8px;border-bottom:1px solid #e2e8f0;font-size:12px;"><a href="{escape(c['url'])}" style="color:#4f46e5;font-weight:700;text-decoration:none;">Open ↗</a></td>
                </tr>
                """
            )
        cuts_html = f"""
        <h2 style="font-size:17px;color:#1e293b;margin:24px 0 10px;">📉 Top In-Budget Price Cuts</h2>
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e2e8f0;border-radius:10px;background:#ffffff;border-collapse:collapse;">
          {''.join(c_rows)}
        </table>
        """

    # Market snapshot rows
    snap_rows = []
    for c in data["complexes"]:
        yld = f"{c['gross_yield']:.1f}%" if c["gross_yield"] is not None else "—"
        snap_rows.append(
            f"""
            <tr>
              <td style="padding:6px 8px;border-bottom:1px solid #f1f5f9;font-size:13px;font-weight:600;color:#1e293b;">{escape(c['name'])}</td>
              <td align="right" style="padding:6px 8px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#4338ca;font-weight:600;">{escape(_fmt_mnt(c['median_rent']))} ({c['rent_units']})</td>
              <td align="right" style="padding:6px 8px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#b45309;font-weight:600;">{escape(_fmt_sqm(c['median_sale_sqm']))} ({c['sale_units']})</td>
              <td align="right" style="padding:6px 8px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#475569;">{yld}</td>
            </tr>
            """
        )

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /></head>
<body style="margin:0;padding:0;background:#f4f5fa;font-family:'Noto Sans','Segoe UI',Roboto,Arial,sans-serif;color:#1e293b;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5fa;padding:20px 10px;">
    <tr>
      <td align="center">
        <table width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;">
          <tr>
            <td style="background:linear-gradient(125deg,#312e81 0%,#4f46e5 60%,#7c3aed 100%);background-color:#4f46e5;border-radius:14px;padding:22px 24px;color:#ffffff;">
              <div style="font-size:20px;font-weight:800;letter-spacing:-0.01em;">Ulaanbaatar Family Apartment Digest</div>
              <div style="font-size:13px;color:#e0e7ff;margin-top:4px;">{escape(dt)} · Last {data['lookback_days']} days · 8 Khan-Uul &amp; Zaisan Complexes · South/West + Parking + High Floor</div>
              <div style="margin-top:14px;">
                <a href="{escape(dashboard_url)}" style="display:inline-block;background:#ffffff;color:#312e81;text-decoration:none;padding:9px 16px;border-radius:9px;font-size:13px;font-weight:800;">Open Full Interactive Dashboard ↗</a>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 4px 6px;">
              <h2 style="font-size:17px;color:#312e81;margin:0 0 10px;">🏠 Best New Rentals (≤ 6.1M ₮/mo · {data['fresh_rents_total']} found)</h2>
              {_render_cards_html(data['top_rents'], is_rent=True)}

              <h2 style="font-size:17px;color:#b45309;margin:22px 0 10px;">🔑 Best New Apartments to Buy (&lt; 1.2B ₮ · {data['fresh_sales_total']} found)</h2>
              {_render_cards_html(data['top_sales'], is_rent=False)}

              {cuts_html}

              <h2 style="font-size:17px;color:#1e293b;margin:24px 0 10px;">📊 8-Complex Median Snapshot</h2>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e2e8f0;border-radius:10px;background:#ffffff;border-collapse:collapse;">
                <tr style="background:#f8fafc;">
                  <th align="left" style="padding:8px;font-size:11px;color:#64748b;text-transform:uppercase;">Complex</th>
                  <th align="right" style="padding:8px;font-size:11px;color:#64748b;text-transform:uppercase;">Median Rent</th>
                  <th align="right" style="padding:8px;font-size:11px;color:#64748b;text-transform:uppercase;">Median Sale ₮/m²</th>
                  <th align="right" style="padding:8px;font-size:11px;color:#64748b;text-transform:uppercase;">Yield</th>
                </tr>
                {''.join(snap_rows)}
              </table>
              <p style="font-size:11px;color:#94a3b8;text-align:center;margin-top:20px;">
                Sent automatically every 3 days · Overpriced units (&gt;6.1M rent / ≥1.2B buy) are tucked away on the dashboard.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return subject, plain_body, html_body


def send_email_smtp(
    subject: str,
    plain_body: str,
    html_body: str,
    smtp_user: str,
    smtp_password: str,
    recipients: list[str],
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 465,
) -> None:
    """Send multipart HTML + plain-text digest via SSL SMTP (Gmail App Password)."""
    if not smtp_user or not smtp_password:
        raise ValueError("Missing SMTP_USER or SMTP_APP_PASSWORD")
    clean_rcpts = [r.strip() for r in recipients if r.strip()]
    if not clean_rcpts:
        raise ValueError("No recipients configured (set DIGEST_RECIPIENTS)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"UB Property Tracker <{smtp_user}>"
    msg["To"] = ", ".join(clean_rcpts)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, clean_rcpts, msg.as_string())


def run_email_digest(
    config: AppConfig,
    data_dir: Path,
    lookback_days: int = 3,
    top_n: int = 8,
    dashboard_url: str = "https://arden3.github.io/property-prices/",
    preview_html: Optional[Path] = None,
    dry_run: bool = False,
) -> tuple[str, int]:
    """Collect best new finds, optionally write preview HTML, and send via SMTP unless dry_run."""
    data = collect_digest_data(config, data_dir, lookback_days=lookback_days, top_n=top_n)
    subject, plain_body, html_body = render_email_digest(data, dashboard_url=dashboard_url)

    if preview_html is not None:
        preview_html.parent.mkdir(parents=True, exist_ok=True)
        preview_html.write_text(html_body, encoding="utf-8")

    if dry_run:
        return subject, 0

    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_APP_PASSWORD", "").strip()
    raw_rcpts = os.environ.get("DIGEST_RECIPIENTS", "")
    recipients = [r.strip() for r in re.split(r"[,;\s]+", raw_rcpts) if r.strip()]

    send_email_smtp(
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        smtp_user=smtp_user,
        smtp_password=smtp_pass,
        recipients=recipients,
    )
    return subject, len(recipients)
