"""Git-friendly canonical persistence (JSONL + CSV) for listings, price history, and runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from ub_prices.models import DealType, ListingRecord, PriceBasis, PriceObservation, ScrapeRun

PRICE_HISTORY_FIELDS = [
    "source",
    "external_id",
    "deal_type",
    "complex_slug",
    "observed_at",
    "raw_price_text",
    "currency",
    "price_basis",
    "total_price_mnt",
    "price_per_sqm_mnt",
    "price_anomaly",
]

SCRAPE_RUN_FIELDS = [
    "run_id",
    "source",
    "started_at",
    "finished_at",
    "status",
    "is_full_scan",
    "requests_made",
    "index_cards_seen",
    "matched_candidates",
    "details_fetched",
    "new_listings",
    "price_changes",
    "delisted_count",
    "notes",
]


class CanonicalStore:
    """Manages deterministic Git-tracked data files under data/."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.listings_path = data_dir / "listings.jsonl"
        self.price_history_path = data_dir / "price_history.csv"
        self.scrape_runs_path = data_dir / "scrape_runs.csv"

    def ensure_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_listings(self) -> dict[tuple[str, str], ListingRecord]:
        records: dict[tuple[str, str], ListingRecord] = {}
        if not self.listings_path.exists():
            return records
        with self.listings_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = ListingRecord.model_validate_json(line)
                records[rec.key] = rec
        return records

    def save_listings(self, records: dict[tuple[str, str], ListingRecord]) -> None:
        """Write listings sorted deterministically by (source, external_id) for clean Git diffs."""
        self.ensure_dir()
        ordered = sorted(records.values(), key=lambda r: (r.source, int(r.external_id) if r.external_id.isdigit() else 0, r.external_id))
        tmp_path = self.listings_path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            for rec in ordered:
                fh.write(json.dumps(rec.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")
        tmp_path.replace(self.listings_path)

    def load_price_history(self) -> list[PriceObservation]:
        obs: list[PriceObservation] = []
        if not self.price_history_path.exists():
            return obs
        with self.price_history_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                obs.append(
                    PriceObservation(
                        source=row["source"],
                        external_id=row["external_id"],
                        deal_type=DealType(row["deal_type"]),
                        complex_slug=row["complex_slug"],
                        observed_at=row["observed_at"],
                        raw_price_text=row["raw_price_text"],
                        currency=row["currency"] or "MNT",
                        price_basis=PriceBasis(row["price_basis"] or "unknown"),
                        total_price_mnt=int(row["total_price_mnt"]) if row["total_price_mnt"] else None,
                        price_per_sqm_mnt=int(row["price_per_sqm_mnt"]) if row["price_per_sqm_mnt"] else None,
                        price_anomaly=row["price_anomaly"].lower() == "true",
                    )
                )
        return obs

    def append_price_observations(self, observations: Iterable[PriceObservation]) -> None:
        items = list(observations)
        if not items:
            return
        self.ensure_dir()
        write_header = not self.price_history_path.exists() or self.price_history_path.stat().st_size == 0
        with self.price_history_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=PRICE_HISTORY_FIELDS, lineterminator="\n")
            if write_header:
                writer.writeheader()
            for item in items:
                writer.writerow(
                    {
                        "source": item.source,
                        "external_id": item.external_id,
                        "deal_type": item.deal_type.value,
                        "complex_slug": item.complex_slug,
                        "observed_at": item.observed_at,
                        "raw_price_text": item.raw_price_text,
                        "currency": item.currency,
                        "price_basis": item.price_basis.value,
                        "total_price_mnt": "" if item.total_price_mnt is None else item.total_price_mnt,
                        "price_per_sqm_mnt": "" if item.price_per_sqm_mnt is None else item.price_per_sqm_mnt,
                        "price_anomaly": str(item.price_anomaly).lower(),
                    }
                )

    def load_scrape_runs(self) -> list[ScrapeRun]:
        runs: list[ScrapeRun] = []
        if not self.scrape_runs_path.exists():
            return runs
        with self.scrape_runs_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                runs.append(
                    ScrapeRun(
                        run_id=row["run_id"],
                        source=row["source"],
                        started_at=row["started_at"],
                        finished_at=row["finished_at"],
                        status=row["status"],
                        is_full_scan=row["is_full_scan"].lower() == "true",
                        requests_made=int(row["requests_made"] or 0),
                        index_cards_seen=int(row["index_cards_seen"] or 0),
                        matched_candidates=int(row["matched_candidates"] or 0),
                        details_fetched=int(row["details_fetched"] or 0),
                        new_listings=int(row["new_listings"] or 0),
                        price_changes=int(row["price_changes"] or 0),
                        delisted_count=int(row["delisted_count"] or 0),
                        notes=row["notes"],
                    )
                )
        return runs

    def append_scrape_run(self, run: ScrapeRun) -> None:
        self.ensure_dir()
        write_header = not self.scrape_runs_path.exists() or self.scrape_runs_path.stat().st_size == 0
        with self.scrape_runs_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=SCRAPE_RUN_FIELDS, lineterminator="\n")
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "run_id": run.run_id,
                    "source": run.source,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "status": run.status,
                    "is_full_scan": str(run.is_full_scan).lower(),
                    "requests_made": run.requests_made,
                    "index_cards_seen": run.index_cards_seen,
                    "matched_candidates": run.matched_candidates,
                    "details_fetched": run.details_fetched,
                    "new_listings": run.new_listings,
                    "price_changes": run.price_changes,
                    "delisted_count": run.delisted_count,
                    "notes": run.notes,
                }
            )
