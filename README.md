# Ulaanbaatar Family Property Price Database (`ub-prices`)

A personal, slow-and-steady tracker of **sale and long-term rental prices** across **8 Khan-Uul / Zaisan residential complexes** in Ulaanbaatar, built to support a "rent first, then buy" decision.

## Watchlist (8 Complexes)
- **Marshall / Tuul River:** River Garden, Time Square, Khan Hills, King Tower
- **Zaisan Foothills:** Buddha Vista, Jardin Residence, Seoul Royal County
- **120 Myangat / Ikh Mongol:** Japan Town

### Listing Criteria
- **Deal types:** Both Sale (`зарна`) and Long-Term Rent (`түрээслүүлнэ`)
- **Rooms:** `3–5 өрөө` (Mongolian convention = 2–4 bedrooms + living room)
- **Area:** `≥ 60 m²` (no upper limit)
- **Exclusions:** Commercial/office spaces, standalone garages, short-term/daily rentals (`хоногийн түрээс`), and neighboring buildings advertised as "near X" (`хажууд`, `ойролцоо`, `-н урд`, etc.)

## Quick Start (Local)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Test matching rules on any title
ub-prices inspect-match "Ривер гарден 2 хотхонд 120мкв 4 өрөө байр зарна"

# Run the daily runner manually (syncs Git, skips if already ran today, scans, pushes)
.\scripts\run_daily.ps1 -Force
```

## Automated Daily Scanning (Home PC + Optional Work Laptop)

Because `unegui.mn` sits behind Cloudflare WAF and returns `403` to cloud datacenter IPs (such as GitHub Actions' US Azure runners), daily scans run politely from your Mongolian residential/local connection and sync automatically via GitHub:

### 1. Home PC (Wake from S3 Sleep → Scan → Back to Sleep)
Install the daily Windows Scheduled Task (default **09:00 AM**):
```powershell
.\scripts\register_task.ps1 -RunAt "09:00"
```
- Before leaving for 7 days, put your PC to **Sleep** (not full Shutdown).
- At 09:00 AM, Windows wakes the PC from S3 Standby, waits for network, pulls from GitHub, scans politely, pushes updated `data/` + `reports/latest.md`, and puts the PC back to S3 Sleep **only if you are idle / locked** (never sleeps while you're actively using it).
- Logs are written to `logs/daily_scan.log` (git-ignored).
- To change time or remove the task: `.\scripts\register_task.ps1 -RunAt "08:30"` or `.\scripts\register_task.ps1 -Unregister`.

### 2. Optional Work Laptop Backup (Zero Conflicts)
If you want to run from your work laptop while on site:
```powershell
git clone https://github.com/<you>/property-prices.git
cd property-prices
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\scripts\run_daily.ps1
```
You can also register it on your laptop without sleep (`.\scripts\register_task.ps1 -RunAt "09:30" -NoSleepAfter`). Because `scripts/run_daily.ps1` pulls via `git pull --rebase` and checks `data/scrape_runs.csv` for today's UTC date, whichever machine runs first does the scan and the second machine skips cleanly with zero duplicate scans or Git conflicts.

### 3. GitHub Actions CI & Downloadable SQLite Artifact
Every push to `main` automatically runs `pytest`, prints `reports/latest.md` to the GitHub Actions Job Summary, and uploads a fresh `ub_prices.db` artifact (30-day retention).

## Analyzing the Database

Canonical records are stored in Git-friendly text files (`data/listings.jsonl`, `data/price_history.csv`, `data/scrape_runs.csv`). Running `ub-prices build-db` compiles `data/ub_prices.db` with ready-to-query SQL views:

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

## Etiquette & `robots.txt` Compliance
- Enforces every `Disallow` rule from `https://www.unegui.mn/robots.txt` (including `/api`, `/map/`, `/ru/`, `/en/`, and `*---*` compound filter URLs) before making any HTTP call.
- Single-threaded, randomized 3.5–6.5s delays between requests, and incremental detail fetching.
