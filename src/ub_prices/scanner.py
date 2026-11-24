"""Two-stage incremental scanner orchestrating polite index scans and detail fetches."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

import httpx

from ub_prices.config import AppConfig, ComplexConfig
from ub_prices.http_client import BlockedError, DisallowedPathError, PoliteHttpClient
from ub_prices.matching import ComplexMatcher
from ub_prices.models import (
    DealType,
    IndexCard,
    ListingRecord,
    MatchResult,
    PriceObservation,
    ScrapeRun,
    utc_now_iso,
)
from ub_prices.normalize import build_listing_record, parse_numeric_price
from ub_prices.sources.base import SourceAdapter
from ub_prices.sources.unegui import UneguiAdapter
from ub_prices.storage.store import CanonicalStore

logger = logging.getLogger(__name__)


def _card_room_allowed(card: IndexCard, allowed_slugs: set[str]) -> bool:
    """Quick index-level room filter: skip 1-r and 2-r immediately before fetching details."""
    slug = (card.rubric_slug or "").strip().lower()
    if not slug:
        return True
    return slug in allowed_slugs


def _card_price_changed(existing: ListingRecord, card: IndexCard) -> bool:
    """Detect whether the search-card price differs from our stored listing price."""
    card_raw = (card.raw_price_text or "").strip()
    if card_raw and existing.raw_price_text and card_raw != existing.raw_price_text:
        val_new, _ = parse_numeric_price(
            card.raw_price_text, card.price_without_currency, card.currency
        )
        val_old, _ = parse_numeric_price(existing.raw_price_text, None, existing.currency)
        if val_new is not None and val_old is not None and abs(val_new - val_old) >= 1.0:
            return True
    return False


def run_scan(
    config: AppConfig,
    data_dir: Path,
    source_name: str = "unegui",
    deal_types: Optional[list[DealType]] = None,
    complex_slugs: Optional[list[str]] = None,
    max_pages: int = 3,
    max_details: int = 120,
    min_delay: float = 3.0,
    max_delay: float = 6.0,
    dry_run: bool = False,
    adapter: Optional[SourceAdapter] = None,
) -> ScrapeRun:
    """Execute a polite two-stage scan and update canonical data files."""
    started_at = utc_now_iso()
    run_id = f"{started_at[:10]}_{uuid.uuid4().hex[:8]}"

    target_deals = deal_types or [DealType.SALE, DealType.RENT]
    if complex_slugs:
        slug_set = set(complex_slugs)
        target_complexes: list[ComplexConfig] = [c for c in config.complexes if c.slug in slug_set]
        missing = slug_set - {c.slug for c in target_complexes}
        if missing:
            raise ValueError(f"Unknown complex slug(s): {', '.join(sorted(missing))}")
    else:
        target_complexes = list(config.complexes)

    is_full_scan = (
        len(target_complexes) == len(config.complexes)
        and set(target_deals) == {DealType.SALE, DealType.RENT}
        and max_pages >= 2
    )

    run = ScrapeRun(
        run_id=run_id,
        source=source_name,
        started_at=started_at,
        status="dry_run" if dry_run else "running",
        is_full_scan=is_full_scan,
    )

    store = CanonicalStore(data_dir)
    listings = store.load_listings()
    matcher = ComplexMatcher(config)
    allowed_slugs = {s.lower() for s in config.filters.allowed_room_slugs}
    complex_by_slug = {c.slug: c for c in config.complexes}

    http_client: Optional[PoliteHttpClient] = None
    if adapter is None:
        http_client = PoliteHttpClient(min_delay=min_delay, max_delay=max_delay)
        if source_name == "unegui":
            adapter = UneguiAdapter(http_client)
        else:
            http_client.close()
            raise ValueError(f"Unsupported source: {source_name}")

    # Collect unique matched index candidates keyed by (source, external_id)
    matched_candidates: dict[tuple[str, str], tuple[IndexCard, MatchResult]] = {}
    new_observations: list[PriceObservation] = []
    notes: list[str] = []
    blocked = False
    detail_cap_hit = False

    try:
        # --- STAGE 1: Polite index crawl across configured complex queries ---
        for comp in target_complexes:
            for deal in target_deals:
                for query in comp.search_queries:
                    for page in range(1, max_pages + 1):
                        logger.info(
                            "Index scan: source=%s deal=%s complex=%s q=%r page=%d",
                            source_name,
                            deal.value,
                            comp.slug,
                            query,
                            page,
                        )
                        try:
                            cards, has_next = adapter.search_page(deal, query, page=page)
                        except BlockedError as e:
                            logger.error("Blocked during index scan: %s", e)
                            notes.append(f"blocked_index:{e}")
                            blocked = True
                            break
                        except DisallowedPathError as e:
                            logger.error("robots.txt violation prevented: %s", e)
                            notes.append(f"disallowed:{e}")
                            break
                        except httpx.HTTPError as e:
                            logger.warning("HTTP error on index page %s q=%r p=%d: %s", deal.value, query, page, e)
                            notes.append(f"index_http_err:{comp.slug}:{deal.value}:p{page}")
                            break

                        run.index_cards_seen += len(cards)
                        if not cards:
                            break

                        page_matches = 0
                        for card in cards:
                            if not _card_room_allowed(card, allowed_slugs):
                                continue
                            res = matcher.match(card.title, card.location_text)
                            if not res.matched:
                                continue
                            # Respect scoped complex filter if user ran --complex <slug>
                            if complex_slugs and res.complex_slug not in set(complex_slugs):
                                continue
                            page_matches += 1
                            key = (card.source, card.external_id)
                            if key not in matched_candidates:
                                matched_candidates[key] = (card, res)

                        logger.info(
                            "  -> %d cards, %d matched watchlist (cumulative unique=%d)",
                            len(cards),
                            page_matches,
                            len(matched_candidates),
                        )

                        # Stop paginating once relevance drops off completely
                        if not has_next or page_matches == 0:
                            break
                    if blocked:
                        break
                if blocked:
                    break
            if blocked:
                break

        run.matched_candidates = len(matched_candidates)
        now_iso = utc_now_iso()

        # Round-robin candidates across (complex_slug, deal_type) so capped backfill runs
        # sample both sale and rent evenly across all target complexes.
        buckets: dict[tuple[str, DealType], list[tuple[tuple[str, str], tuple[IndexCard, MatchResult]]]] = {}
        for key, pair in matched_candidates.items():
            card, index_match = pair
            bkey = (index_match.complex_slug or "", card.deal_type)
            buckets.setdefault(bkey, []).append((key, pair))

        ordered_candidates: list[tuple[tuple[str, str], tuple[IndexCard, MatchResult]]] = []
        bucket_keys = list(buckets.keys())
        idx = 0
        while True:
            added = False
            for bk in bucket_keys:
                items = buckets[bk]
                if idx < len(items):
                    ordered_candidates.append(items[idx])
                    added = True
            if not added:
                break
            idx += 1

        # --- STAGE 2: Incremental detail fetch (only new, price-changed, or reactivated IDs) ---
        for key, (card, index_match) in ordered_candidates:
            existing = listings.get(key)
            needs_detail = (
                existing is None
                or not existing.is_active
                or _card_price_changed(existing, card)
            )

            if not needs_detail and existing is not None:
                # Unchanged active listing: just touch last_seen_at without hitting /adv/
                existing.last_seen_at = now_iso
                existing.missed_scans = 0
                existing.is_active = True
                if card.published_label:
                    existing.published_label = card.published_label
                continue

            if blocked:
                break

            if max_details > 0 and run.details_fetched >= max_details:
                detail_cap_hit = True
                continue

            logger.info("Fetching detail: %s (%s)", card.external_id, card.title[:60])
            try:
                detail = adapter.fetch_detail(card)
                run.details_fetched += 1
            except BlockedError as e:
                logger.error("Blocked during detail fetch: %s", e)
                notes.append(f"blocked_detail:{e}")
                blocked = True
                break
            except DisallowedPathError as e:
                logger.error("robots.txt violation on detail URL: %s", e)
                notes.append(f"disallowed_detail:{card.external_id}")
                continue
            except httpx.HTTPError as e:
                logger.warning("Detail HTTP error for %s: %s", card.external_id, e)
                notes.append(f"detail_http_err:{card.external_id}")
                continue

            # Re-verify match using full title + location + description (catches description-level daily rentals)
            full_match = matcher.match(detail.title, detail.location_text, detail.description)
            if not full_match.matched:
                logger.info(
                    "Rejected after detail fetch (%s): %s",
                    full_match.rejected_reason or full_match.reason,
                    card.external_id,
                )
                continue

            comp_cfg = complex_by_slug.get(full_match.complex_slug or "")
            zone = comp_cfg.zone if comp_cfg else ""

            record, filter_reject = build_listing_record(
                detail=detail,
                match=full_match,
                zone=zone,
                filters=config.filters,
            )
            if record is None:
                logger.info("Filtered out %s (%s)", card.external_id, filter_reject)
                continue

            if existing is None:
                record.first_seen_at = now_iso
                record.last_seen_at = now_iso
                listings[key] = record
                run.new_listings += 1
                new_observations.append(
                    PriceObservation(
                        source=record.source,
                        external_id=record.external_id,
                        deal_type=record.deal_type,
                        complex_slug=record.complex_slug,
                        observed_at=now_iso,
                        raw_price_text=record.raw_price_text,
                        currency=record.currency,
                        price_basis=record.price_basis,
                        total_price_mnt=record.total_price_mnt,
                        price_per_sqm_mnt=record.price_per_sqm_mnt,
                        price_anomaly=record.price_anomaly,
                    )
                )
            else:
                price_changed = (
                    record.total_price_mnt != existing.total_price_mnt
                    or record.price_per_sqm_mnt != existing.price_per_sqm_mnt
                )
                record.first_seen_at = existing.first_seen_at
                record.last_seen_at = now_iso
                record.missed_scans = 0
                listings[key] = record
                if price_changed:
                    run.price_changes += 1
                    new_observations.append(
                        PriceObservation(
                            source=record.source,
                            external_id=record.external_id,
                            deal_type=record.deal_type,
                            complex_slug=record.complex_slug,
                            observed_at=now_iso,
                            raw_price_text=record.raw_price_text,
                            currency=record.currency,
                            price_basis=record.price_basis,
                            total_price_mnt=record.total_price_mnt,
                            price_per_sqm_mnt=record.price_per_sqm_mnt,
                            price_anomaly=record.price_anomaly,
                        )
                    )

        # --- STAGE 3: Delisting detection (only on full, unblocked, non-capped scans) ---
        if is_full_scan and not blocked and not detail_cap_hit:
            seen_keys = set(matched_candidates.keys())
            for key, rec in listings.items():
                if rec.source != source_name or not rec.is_active:
                    continue
                if key not in seen_keys:
                    rec.missed_scans += 1
                    if rec.missed_scans >= 2:
                        rec.is_active = False
                        run.delisted_count += 1
        elif detail_cap_hit:
            notes.append(f"detail_cap_reached:{max_details}")

        if dry_run:
            run.status = "dry_run"
        elif blocked:
            run.status = "blocked"
        elif detail_cap_hit:
            run.status = "partial"
        else:
            run.status = "ok"

    finally:
        if http_client is not None:
            run.requests_made = http_client.requests_made
            http_client.close()

    run.finished_at = utc_now_iso()
    run.notes = "; ".join(notes)[:500]

    if not dry_run:
        store.save_listings(listings)
        store.append_price_observations(new_observations)
        store.append_scrape_run(run)

    return run
