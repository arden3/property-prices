# Ulaanbaatar Family Property Price Database (`ub-prices`)

A personal, slow-and-steady tracker of **sale and long-term rental prices** across **12 Khan-Uul / Zaisan residential complexes** in Ulaanbaatar, built to support a "rent first, then buy" decision.

## Watchlist (12 Complexes)
- **Marshall / Tuul River:** River Garden, Time Square, Khan Hills, King Tower, Marshall Town
- **Zaisan Foothills:** Bella Vista, Buddha Vista, Jardin Residence, Seoul Royal County
- **120 Myangat / National Park:** Japan Town, Vega City, Gerlug Vista

### Listing Criteria
- **Deal types:** Both Sale (`зарна`) and Long-Term Rent (`түрээслүүлнэ`)
- **Rooms:** `3–5 өрөө` (Mongolian convention = 2–4 bedrooms + living room)
- **Area:** `≥ 60 m²` (no upper limit)
- **Exclusions:** Commercial/office spaces, standalone garages, short-term/daily rentals (`хоногийн түрээс`), and neighboring buildings advertised as "near X" (`хажууд`, `ойролцоо`, etc.)

## Quick Start (Local)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Test matching rules on any title
ub-prices inspect-match "Ривер гарден 2 хотхонд 120мкв 4 өрөө байр зарна"

# Run a small polite pilot scan (1 complex, 1 page)
ub-prices scan --complex river-garden --max-pages 1 --max-details 15

# Compile SQLite database & generate Markdown digest
ub-prices build-db
ub-prices report
```

## Analyzing the Database

Canonical records are stored in Git-friendly text files (`data/listings.jsonl`, `data/price_history.csv`, `data/scrape_runs.csv`). Running `ub-prices build-db` compiles `data/ub_prices.db` with ready-to-query views:

- `v_active_deduped` — active non-anomaly listings deduplicated across multiple brokers by `(complex, deal_type, rooms, area, floor/total_floors)`
- `v_sale_medians` & `v_rent_medians` — exact median asking prices per complex
- `v_rent_vs_buy` — sale median ₮/m², monthly rent median, estimated gross yield %, and price-to-rent ratio (years)
- `v_sale_by_rooms` & `v_rent_by_rooms` — room-count breakdown (`3`, `4`, `5 өрөө`)
- `v_price_drops` — active listings whose asking price dropped since first tracked

Example SQL query:
```sql
SELECT complex_name, sale_units, sale_median_sqm_mnt, rent_units,
       median_monthly_rent_mnt, gross_yield_pct_sqm, price_to_rent_years
FROM v_rent_vs_buy
ORDER BY sale_median_sqm_mnt ASC;
```

## GitHub Actions Automation

Push this repository to a **private GitHub repo** and enable Actions:
- `.github/workflows/daily_scan.yml` runs once daily at **09:00 AM UB time** (`01:00 UTC`), ~3–5 min/day (~100–150 min/month, well under the 2,000-minute free tier).
- Each run posts the daily digest in the **GitHub Actions Job Summary**, updates `reports/latest.md`, commits `data/`, and uploads `ub_prices.db` as a 30-day downloadable artifact.
- Because initial backfill caps new detail fetches at `120/run`, the first 2–3 runs gently backfill existing listings; subsequent daily runs only fetch brand-new or price-changed ads.
- If `unegui.mn` ever blocks GitHub Actions datacenter IPs, the workflow saves partial progress, exits cleanly without bypassing challenges, and you can run the exact same `ub-prices scan` command on Windows Task Scheduler.

## Etiquette & `robots.txt` Compliance
- Enforces every `Disallow` rule from `https://www.unegui.mn/robots.txt` (including `/api`, `/map/`, `/ru/`, `/en/`, and `*---*` compound filter URLs) before making any HTTP call.
- Single-threaded, randomized 3–6s delays between requests, and incremental detail fetching.
