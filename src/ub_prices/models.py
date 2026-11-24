"""Core Pydantic data models for property listings, price history, and scrape runs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DealType(str, Enum):
    SALE = "sale"
    RENT = "rent"


class PriceBasis(str, Enum):
    TOTAL = "total"
    PER_SQM = "per_sqm"
    MONTHLY = "monthly"
    UNKNOWN = "unknown"


class MatchResult(BaseModel):
    matched: bool = False
    complex_slug: Optional[str] = None
    complex_name: Optional[str] = None
    confidence: str = "none"  # high | medium | fuzzy | none
    reason: str = ""
    rejected_reason: Optional[str] = None


class IndexCard(BaseModel):
    """Lightweight representation extracted from a search results page."""

    source: str
    external_id: str
    url: str
    deal_type: DealType
    title: str
    location_text: str = ""
    rubric_slug: str = ""
    raw_price_text: str = ""
    start_price_text: str = ""
    currency: str = "MNT"
    price_without_currency: Optional[str] = None
    published_label: str = ""
    seller_id: Optional[str] = None
    seller_name: str = ""
    is_business: Optional[bool] = None
    img_count: int = 0


class ListingDetail(BaseModel):
    """Structured detail payload fetched from an individual listing page."""

    source: str
    external_id: str
    url: str
    deal_type: DealType
    title: str
    description: str = ""
    location_text: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    rubric_slug: str = ""
    raw_price_text: str = ""
    start_price_text: str = ""
    square_meter_price_text: str = ""
    currency: str = "MNT"
    price_without_currency: Optional[str] = None
    published_label: str = ""
    is_closed: bool = False
    favorites: Optional[int] = None
    views: Optional[int] = None
    seller_id: Optional[str] = None
    seller_name: str = ""
    is_business: Optional[bool] = None
    features: dict[str, str] = Field(default_factory=dict)


class ListingRecord(BaseModel):
    """Canonical persisted listing record stored in data/listings.jsonl."""

    source: str
    external_id: str
    url: str
    deal_type: DealType
    complex_slug: str
    complex_name: str
    zone: str = ""
    match_confidence: str = "high"
    match_reason: str = ""

    title: str
    district: str = ""
    location_text: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None

    rooms: Optional[int] = None
    area_sqm: Optional[float] = None
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    floor_tier: str = "unknown"  # high | mid | low | unknown
    year_built: Optional[int] = None
    balcony: str = ""
    garage: str = ""
    parking_status: str = "unknown"  # included | available | none | unknown
    window_count: Optional[int] = None
    window_facing: str = ""  # e.g., "South, West"
    faces_south: bool = False
    faces_west: bool = False
    faces_east: bool = False
    faces_north: bool = False
    payment_terms: str = ""
    furnished: str = ""

    raw_price_text: str = ""
    start_price_text: str = ""
    start_price_mnt: Optional[int] = None
    currency: str = "MNT"
    price_basis: PriceBasis = PriceBasis.UNKNOWN
    total_price_mnt: Optional[int] = None
    price_per_sqm_mnt: Optional[int] = None
    price_anomaly: bool = False
    price_anomaly_reason: str = ""

    unit_fingerprint: str = ""
    seller_id: Optional[str] = None
    seller_name: str = ""
    is_business: Optional[bool] = None
    published_label: str = ""
    description: str = ""

    first_seen_at: str = Field(default_factory=utc_now_iso)
    last_seen_at: str = Field(default_factory=utc_now_iso)
    missed_scans: int = 0
    is_active: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.external_id)


class PriceObservation(BaseModel):
    """Append-only price history entry stored in data/price_history.csv."""

    source: str
    external_id: str
    deal_type: DealType
    complex_slug: str
    observed_at: str = Field(default_factory=utc_now_iso)
    raw_price_text: str = ""
    currency: str = "MNT"
    price_basis: PriceBasis = PriceBasis.UNKNOWN
    total_price_mnt: Optional[int] = None
    price_per_sqm_mnt: Optional[int] = None
    price_anomaly: bool = False


class ScrapeRun(BaseModel):
    """Telemetry record for a single scan execution stored in data/scrape_runs.csv."""

    run_id: str
    source: str
    started_at: str
    finished_at: str = ""
    status: str = "running"  # ok | partial | blocked | error | dry_run
    is_full_scan: bool = False
    requests_made: int = 0
    index_cards_seen: int = 0
    matched_candidates: int = 0
    details_fetched: int = 0
    new_listings: int = 0
    price_changes: int = 0
    delisted_count: int = 0
    notes: str = ""
