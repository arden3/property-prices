"""Mongolian real estate field normalization, price disambiguation, and filter enforcement."""

from __future__ import annotations

import re
from typing import Optional

from ub_prices.config import FilterConfig
from ub_prices.models import DealType, ListingDetail, ListingRecord, MatchResult, PriceBasis

# Approximate MNT/USD fallback rate only if a listing is priced in USD (flagged in currency field)
DEFAULT_USD_TO_MNT = 3450.0

# Reasonable Ulaanbaatar bounds for anomaly flagging (stored, not silently deleted)
SALE_SQM_MIN_MNT = 2_000_000
SALE_SQM_MAX_MNT = 25_000_000
RENT_MONTHLY_MIN_MNT = 1_000_000
RENT_MONTHLY_MAX_MNT = 35_000_000

_ROOM_SLUG_MAP = {
    "1-r": 1,
    "2-r": 2,
    "3-r": 3,
    "4-r": 4,
    # "5-r" on Unegui means "5+ өрөө" — exact count should be parsed from title/desc when present
    "5-r": 5,
}


def parse_rooms(
    rubric_slug: str = "", title: str = "", description: str = ""
) -> Optional[int]:
    """Extract room count (Mongolian convention: includes living room).

    Note: Unegui's '5-r' rubric means '5+ өрөө', so we inspect title/description
    first for 5+ rooms to catch 6-room or 7-room apartments accurately.
    """
    text_sources = [title or "", description or ""]
    # Explicit room patterns in title/description
    for src in text_sources:
        m = re.search(
            r"(?<!\d)(\d{1,2})\s*(?:-?\s*н)?\s*(?:өрөө|өрөөн|өрөөтэй|oroo|uruu|room)",
            src,
            flags=re.IGNORECASE | re.UNICODE,
        )
        if m:
            val = int(m.group(1))
            if 1 <= val <= 12:
                # If rubric is 3-r or 4-r and title matches, trust it; for 5-r this catches 6+ өрөө
                return val

    slug = (rubric_slug or "").strip().lower()
    if slug in _ROOM_SLUG_MAP:
        return _ROOM_SLUG_MAP[slug]
    return None


def parse_area_sqm(
    features: Optional[dict[str, str]] = None, title: str = "", description: str = ""
) -> Optional[float]:
    """Extract apartment area in square meters (prefers structured 'Талбай' feature)."""
    features = features or {}
    for key in ("Талбай", "талбай", "Area", "Талбайн хэмжээ"):
        if key in features and features[key]:
            m = re.search(r"(\d+(?:[.,]\d+)?)", features[key])
            if m:
                val = float(m.group(1).replace(",", "."))
                if 15.0 <= val <= 1500.0:
                    return val

    # Fall back to title then description
    for src in (title or "", description or ""):
        m = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:м2|м²|мкв|м\.кв|mkv|m2|sqm)",
            src,
            flags=re.IGNORECASE | re.UNICODE,
        )
        if m:
            val = float(m.group(1).replace(",", "."))
            if 15.0 <= val <= 1500.0:
                return val
    return None


def parse_int_feature(features: dict[str, str], keys: tuple[str, ...]) -> Optional[int]:
    for k in keys:
        v = features.get(k)
        if v:
            m = re.search(r"(\d+)", v)
            if m:
                return int(m.group(1))
    return None


def parse_floor_from_text(text: str) -> tuple[Optional[int], Optional[int]]:
    """Fallback parser for '5/16 давхар' or '3 давхарт' patterns."""
    if not text:
        return None, None
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*(?:давхар|давхарт|floor)", text, flags=re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"(\d{1,2})\s*(?:-?р)?\s*давхар(?:т)?", text, flags=re.I)
    if m2:
        return int(m2.group(1)), None
    return None, None


def parse_rent_terms(features: dict[str, str], title: str = "", description: str = "") -> str:
    """Extract rental payment terms (e.g. '3+1', '6+1', '12+1') or sale payment condition."""
    structured = features.get("Төлбөрийн нөхцөл", "").strip()
    combined = f"{structured} {title} {description}"
    m = re.search(r"\b(\d{1,2}\s*\+\s*\d{1,2})\b", combined)
    if m:
        term = re.sub(r"\s+", "", m.group(1))
        return term
    return structured


def parse_furnished(title: str = "", description: str = "") -> str:
    combined = f"{title} {description}".lower()
    if any(k in combined for k in ("бүрэн тавилгатай", "бүрэн тавилга", "fully furnished")):
        return "fully_furnished"
    if any(k in combined for k in ("хагас тавилгатай", "semi furnished", "partially furnished")):
        return "semi_furnished"
    if any(k in combined for k in ("тавилгагүй", "unfurnished")):
        return "unfurnished"
    if "тавилгатай" in combined or "furnished" in combined:
        return "furnished"
    return ""


def parse_numeric_price(
    raw_price_text: str,
    price_without_currency: Optional[str] = None,
    currency: str = "MNT",
) -> tuple[Optional[float], str]:
    """Parse raw Mongolian price string or numeric string into (numeric_value, currency)."""
    curr = (currency or "MNT").upper().strip()
    txt = (raw_price_text or "").strip()
    txt_lower = txt.lower()

    if "$" in txt or "usd" in txt_lower or "доллар" in txt_lower:
        curr = "USD"

    # Prefer explicit numeric field from Unegui JSON when available & valid
    if price_without_currency:
        cleaned = re.sub(r"[^\d.]", "", str(price_without_currency))
        if cleaned:
            try:
                val = float(cleaned)
                if val > 0:
                    return val, curr
            except ValueError:
                pass

    if not txt:
        return None, curr

    # Handle "1 тэрбум 200 сая" or "1.2 тэрбум" or "830 сая" or "830,000,000"
    billion_m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:тэрбум|terbum)", txt_lower)
    million_m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:сая|say|saya)", txt_lower)
    thousand_m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:мянга|myanga)", txt_lower)

    total = 0.0
    matched_unit = False
    if billion_m:
        total += float(billion_m.group(1).replace(",", ".")) * 1_000_000_000
        matched_unit = True
    if million_m:
        total += float(million_m.group(1).replace(",", ".")) * 1_000_000
        matched_unit = True
    if thousand_m and not billion_m:
        total += float(thousand_m.group(1).replace(",", ".")) * 1_000
        matched_unit = True

    if matched_unit and total > 0:
        return total, curr

    # Plain number with commas/spaces (e.g. "830,000,000 ₮" or "4 500 000")
    num_m = re.search(r"(\d[\d\s,]{2,}(?:\.\d+)?)", txt)
    if num_m:
        cleaned = re.sub(r"[\s,]", "", num_m.group(1))
        try:
            val = float(cleaned)
            if val > 0:
                return val, curr
        except ValueError:
            pass

    return None, curr


def normalize_price(
    deal_type: DealType,
    raw_price_text: str,
    price_without_currency: Optional[str] = None,
    currency: str = "MNT",
    area_sqm: Optional[float] = None,
    square_meter_price_text: str = "",
) -> tuple[PriceBasis, Optional[int], Optional[int], bool, str]:
    """Normalize price into (basis, total_price_mnt, price_per_sqm_mnt, anomaly, anomaly_reason).

    For SALE:
      - Distinguishes total asking price vs per-m² price (both explicit and heuristic).
      - Computes both total_price_mnt and price_per_sqm_mnt whenever area_sqm is known.
    For RENT:
      - Treats price as monthly rent in total_price_mnt and computes monthly rent/m².
    """
    val, curr = parse_numeric_price(raw_price_text, price_without_currency, currency)
    if val is None or val <= 0:
        return PriceBasis.UNKNOWN, None, None, True, "missing_price"

    val_mnt = val * DEFAULT_USD_TO_MNT if curr == "USD" else val
    txt_lower = (raw_price_text or "").lower()

    if deal_type == DealType.RENT:
        monthly_mnt = int(round(val_mnt))
        # Common seller typo: entering "4500" meaning 4.5M or "4.5" meaning 4.5M
        if monthly_mnt < 100_000:
            return PriceBasis.MONTHLY, monthly_mnt, None, True, "rent_value_too_low"
        per_sqm = int(round(monthly_mnt / area_sqm)) if area_sqm and area_sqm > 0 else None
        if monthly_mnt < RENT_MONTHLY_MIN_MNT or monthly_mnt > RENT_MONTHLY_MAX_MNT:
            return PriceBasis.MONTHLY, monthly_mnt, per_sqm, True, "rent_out_of_bounds"
        return PriceBasis.MONTHLY, monthly_mnt, per_sqm, False, ""

    # SALE price disambiguation
    explicit_per_sqm = any(
        tok in txt_lower for tok in ("/м2", "/м²", "м2 үнэ", "мкв", "per sqm", "/m2")
    )

    if explicit_per_sqm or (
        area_sqm is not None
        and area_sqm >= 30.0
        and SALE_SQM_MIN_MNT <= val_mnt <= SALE_SQM_MAX_MNT
    ):
        # Seller entered per-m² price in the price field
        per_sqm_mnt = int(round(val_mnt))
        total_mnt = int(round(per_sqm_mnt * area_sqm)) if area_sqm and area_sqm > 0 else None
        anomaly = not (SALE_SQM_MIN_MNT <= per_sqm_mnt <= SALE_SQM_MAX_MNT)
        reason = "sale_sqm_out_of_bounds" if anomaly else ""
        return PriceBasis.PER_SQM, total_mnt, per_sqm_mnt, anomaly, reason

    # Otherwise treat as total sale price
    total_mnt = int(round(val_mnt))
    per_sqm_mnt = int(round(total_mnt / area_sqm)) if area_sqm and area_sqm > 0 else None

    # Also check explicit square_meter_price_text if area was missing
    if per_sqm_mnt is None and square_meter_price_text:
        sqm_val, sqm_curr = parse_numeric_price(square_meter_price_text, None, curr)
        if sqm_val:
            per_sqm_mnt = int(
                round(sqm_val * DEFAULT_USD_TO_MNT if sqm_curr == "USD" else sqm_val)
            )

    if per_sqm_mnt is not None:
        if per_sqm_mnt < SALE_SQM_MIN_MNT or per_sqm_mnt > SALE_SQM_MAX_MNT:
            return PriceBasis.TOTAL, total_mnt, per_sqm_mnt, True, "sale_sqm_out_of_bounds"
    elif total_mnt < 100_000_000:
        return PriceBasis.TOTAL, total_mnt, None, True, "sale_total_too_low"

    return PriceBasis.TOTAL, total_mnt, per_sqm_mnt, False, ""


def compute_unit_fingerprint(
    complex_slug: str,
    deal_type: DealType,
    rooms: Optional[int],
    area_sqm: Optional[float],
    floor: Optional[int],
    total_floors: Optional[int],
) -> str:
    """Soft deduplication key to cluster multi-broker listings of the same physical unit."""
    r = str(rooms) if rooms is not None else "?"
    a = str(int(round(area_sqm))) if area_sqm is not None else "?"
    f = str(floor) if floor is not None else "?"
    tf = str(total_floors) if total_floors is not None else "?"
    return f"{complex_slug}|{deal_type.value}|{r}r|{a}sqm|{f}/{tf}"


def extract_district(location_text: str) -> str:
    if not location_text:
        return ""
    parts = [p.strip() for p in re.split(r"[—\-–,/]", location_text) if p.strip()]
    for part in parts:
        if any(
            d in part.lower()
            for d in ("хан-уул", "баянзүрх", "сүхбаатар", "баянгол", "чингэлтэй", "сонгинохайрхан")
        ):
            return part
    return parts[1] if len(parts) >= 2 else parts[0]


def passes_listing_filters(
    rooms: Optional[int],
    area_sqm: Optional[float],
    filters: FilterConfig,
) -> tuple[bool, str]:
    """Enforce 3–5 rooms (2–4 bedrooms) and >= 60 m² (no max area limit)."""
    if rooms is None:
        return False, "missing_rooms"
    if rooms < filters.rooms_min or rooms > filters.rooms_max:
        return False, f"rooms_out_of_range:{rooms}"

    if area_sqm is None:
        return False, "missing_area"
    if area_sqm < filters.min_area_sqm:
        return False, f"area_below_min:{area_sqm}"
    if filters.max_area_sqm is not None and area_sqm > filters.max_area_sqm:
        return False, f"area_above_max:{area_sqm}"

    return True, ""


def build_listing_record(
    detail: ListingDetail,
    match: MatchResult,
    zone: str,
    filters: FilterConfig,
) -> tuple[Optional[ListingRecord], str]:
    """Convert a ListingDetail + MatchResult into a validated ListingRecord if it passes filters."""
    rooms = parse_rooms(detail.rubric_slug, detail.title, detail.description)
    area_sqm = parse_area_sqm(detail.features, detail.title, detail.description)

    ok, reject_reason = passes_listing_filters(rooms, area_sqm, filters)
    if not ok:
        return None, reject_reason

    floor = parse_int_feature(detail.features, ("Хэдэн давхарт", "Давхар"))
    total_floors = parse_int_feature(detail.features, ("Барилгын давхар",))
    if floor is None:
        f_txt, tf_txt = parse_floor_from_text(f"{detail.title} {detail.description}")
        floor = f_txt
        if total_floors is None:
            total_floors = tf_txt

    year_built = parse_int_feature(detail.features, ("Ашиглалтанд орсон он", "Ашиглалтад орсон он"))
    window_count = parse_int_feature(detail.features, ("Цонхны тоо",))
    balcony = detail.features.get("Тагт", "").strip()
    garage = detail.features.get("Гараж", "").strip()
    payment_terms = parse_rent_terms(detail.features, detail.title, detail.description)
    furnished = parse_furnished(detail.title, detail.description)

    basis, total_mnt, per_sqm_mnt, anomaly, anomaly_reason = normalize_price(
        deal_type=detail.deal_type,
        raw_price_text=detail.raw_price_text,
        price_without_currency=detail.price_without_currency,
        currency=detail.currency,
        area_sqm=area_sqm,
        square_meter_price_text=detail.square_meter_price_text,
    )

    fingerprint = compute_unit_fingerprint(
        complex_slug=match.complex_slug or "",
        deal_type=detail.deal_type,
        rooms=rooms,
        area_sqm=area_sqm,
        floor=floor,
        total_floors=total_floors,
    )

    record = ListingRecord(
        source=detail.source,
        external_id=detail.external_id,
        url=detail.url,
        deal_type=detail.deal_type,
        complex_slug=match.complex_slug or "",
        complex_name=match.complex_name or "",
        zone=zone,
        match_confidence=match.confidence,
        match_reason=match.reason,
        title=detail.title.strip(),
        district=extract_district(detail.location_text),
        location_text=detail.location_text.strip(),
        lat=detail.lat,
        lng=detail.lng,
        rooms=rooms,
        area_sqm=area_sqm,
        floor=floor,
        total_floors=total_floors,
        year_built=year_built,
        balcony=balcony,
        garage=garage,
        window_count=window_count,
        payment_terms=payment_terms,
        furnished=furnished,
        raw_price_text=detail.raw_price_text.strip(),
        start_price_text=detail.start_price_text.strip(),
        currency=detail.currency or "MNT",
        price_basis=basis,
        total_price_mnt=total_mnt,
        price_per_sqm_mnt=per_sqm_mnt,
        price_anomaly=anomaly,
        price_anomaly_reason=anomaly_reason,
        unit_fingerprint=fingerprint,
        seller_id=detail.seller_id,
        seller_name=detail.seller_name,
        is_business=detail.is_business,
        published_label=detail.published_label,
        description=detail.description.strip(),
        is_active=not detail.is_closed,
    )
    return record, ""
