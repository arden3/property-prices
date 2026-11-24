"""Unegui.mn source adapter parsing Next.js App Router SSR payloads cleanly."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ub_prices.http_client import PoliteHttpClient
from ub_prices.models import DealType, IndexCard, ListingDetail
from ub_prices.sources.base import SourceAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.unegui.mn"

# Scoped to Khan-Uul District apartments (all 12 target complexes are in Khan-Uul).
# Uses clean single-category slugs + ?q= query params (never uses disallowed '*---*' filter URLs).
CATEGORY_PATHS = {
    DealType.SALE: "/l-hdlh/l-hdlh-zarna/oron-suuts-zarna/hanuul/",
    DealType.RENT: "/l-hdlh/l-hdlh-treesllne/oron-suuts/hanuul/",
}


def extract_rsc_stream(html: str) -> str:
    """Concatenate decoded Next.js App Router self.__next_f.push([1, "..."]) chunks."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
    decoded: list[str] = []
    for chunk in chunks:
        try:
            decoded.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    return "".join(decoded)


def _decode_json_containing(stream: str, anchor_pattern: str, start_key: str) -> Optional[dict[str, Any]]:
    m = re.search(anchor_pattern, stream)
    if not m:
        return None
    # Search backward for `{"<start_key>"`
    idx = m.start()
    start = stream.rfind(f'{{"{start_key}"', 0, idx)
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stream, start)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        return None
    return None


def _abs_url(path_or_url: str) -> str:
    if not path_or_url:
        return BASE_URL
    if path_or_url.startswith("http"):
        return path_or_url
    if not path_or_url.startswith("/"):
        return f"{BASE_URL}/{path_or_url}"
    return f"{BASE_URL}{path_or_url}"


def parse_search_page(html: str, deal_type: DealType) -> tuple[list[IndexCard], bool]:
    """Parse an unegui.mn search page HTML into (IndexCard list, has_next_page)."""
    stream = extract_rsc_stream(html)
    data = _decode_json_containing(stream, r'"adverts"\s*:\s*\[', "next_page")
    if data is None:
        # Secondary anchor in case key ordering changes
        data = _decode_json_containing(stream, r'"adverts"\s*:\s*\[', "listing_type")

    cards: list[IndexCard] = []
    has_next = False

    if data and isinstance(data.get("adverts"), list):
        has_next = bool(data.get("next_page"))
        for ad in data["adverts"]:
            if not isinstance(ad, dict) or not ad.get("id"):
                continue
            rubric = ad.get("rubric") if isinstance(ad.get("rubric"), dict) else {}
            user = ad.get("user") if isinstance(ad.get("user"), dict) else {}
            raw_imgs = ad.get("images") if isinstance(ad.get("images"), list) else []
            img_urls = [str(u).strip() for u in raw_imgs if isinstance(u, str) and u.startswith("http")][:5]
            if not img_urls and isinstance(ad.get("first_thumb"), str) and ad["first_thumb"].startswith("http"):
                img_urls = [ad["first_thumb"].strip()]
            cards.append(
                IndexCard(
                    source="unegui",
                    external_id=str(ad["id"]),
                    url=_abs_url(str(ad.get("url") or f"/adv/{ad['id']}/")),
                    deal_type=deal_type,
                    title=str(ad.get("title") or "").strip(),
                    location_text=str(ad.get("location") or "").strip(),
                    rubric_slug=str(rubric.get("slug") or "").strip(),
                    raw_price_text=str(ad.get("price") or "").strip(),
                    start_price_text=str(ad.get("start_price") or "").strip(),
                    currency=str(ad.get("currency") or "MNT").strip(),
                    price_without_currency=(
                        str(ad["price_without_currency"])
                        if ad.get("price_without_currency") is not None
                        else None
                    ),
                    published_label=str(ad.get("published") or "").strip(),
                    seller_id=str(user["id"]) if user.get("id") is not None else None,
                    seller_name=str(user.get("name") or "").strip(),
                    is_business=user.get("is_bussiness")
                    if isinstance(user.get("is_bussiness"), bool)
                    else None,
                    img_count=int(ad.get("img_count") or 0),
                    image_urls=img_urls,
                )
            )
        return cards, has_next

    # Fallback HTML anchor parsing if RSC structure ever shifts
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    for a in soup.select('a[href*="/adv/"]'):
        href = str(a.get("href") or "")
        m = re.search(r"/adv/(\d+)", href)
        if not m:
            continue
        ext_id = m.group(1)
        if ext_id in seen:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        seen.add(ext_id)
        cards.append(
            IndexCard(
                source="unegui",
                external_id=ext_id,
                url=_abs_url(href),
                deal_type=deal_type,
                title=title,
            )
        )
    has_next = bool(soup.select_one('a[href*="page="]'))
    return cards, has_next


def _extract_detail_json(stream: str, external_id: str) -> Optional[dict[str, Any]]:
    """Locate the advert detail JSON object in the Next.js RSC stream."""
    # Search all occurrences of `"id": <external_id>` and pick the one containing `features` or `description`
    pattern = re.compile(rf'"id"\s*:\s*{re.escape(str(external_id))}\b')
    dec = json.JSONDecoder()
    best: Optional[dict[str, Any]] = None
    for m in pattern.finditer(stream):
        start = stream.rfind("{", 0, m.start())
        if start != -1:
            try:
                obj, _ = dec.raw_decode(stream, start)
                if isinstance(obj, dict) and str(obj.get("id")) == str(external_id):
                    if "features" in obj or "description" in obj:
                        return obj
                    best = obj
            except json.JSONDecodeError:
                pass
    return best


def parse_detail_page(
    html: str,
    external_id: str,
    url: str,
    deal_type: DealType,
    fallback_card: Optional[IndexCard] = None,
) -> ListingDetail:
    """Parse an unegui.mn /adv/<id>/ detail page into a structured ListingDetail."""
    stream = extract_rsc_stream(html)
    obj = _extract_detail_json(stream, external_id)

    if obj is not None:
        price_obj = obj.get("price") if isinstance(obj.get("price"), dict) else {}
        loc_obj = obj.get("location") if isinstance(obj.get("location"), dict) else {}
        coords = loc_obj.get("coordinates") if isinstance(loc_obj.get("coordinates"), dict) else {}
        counters = obj.get("counters") if isinstance(obj.get("counters"), dict) else {}
        user = obj.get("user") if isinstance(obj.get("user"), dict) else {}

        features: dict[str, str] = {}
        for feat in obj.get("features") or []:
            if isinstance(feat, dict) and feat.get("name"):
                val = feat.get("value")
                if val is not None and str(val).strip():
                    features[str(feat["name"]).strip()] = str(val).strip()

        # Extract room rubric slug from categories list (last category is typically '3-r', '4-r', '5-r')
        rubric_slug = fallback_card.rubric_slug if fallback_card else ""
        categories = obj.get("categories") or []
        if isinstance(categories, list):
            for cat in reversed(categories):
                if isinstance(cat, dict) and re.match(r"^\d+-r$", str(cat.get("slug") or "")):
                    rubric_slug = str(cat["slug"])
                    break

        lat = coords.get("lat")
        lng = coords.get("lng")

        raw_price_text = str(
            price_obj.get("price") or (fallback_card.raw_price_text if fallback_card else "")
        ).strip()
        start_price_text = str(
            price_obj.get("start_price")
            or (fallback_card.start_price_text if fallback_card else "")
        ).strip()
        price_wo_curr = price_obj.get("price_without_currency")
        if price_wo_curr is None and fallback_card:
            price_wo_curr = fallback_card.price_without_currency

        # Extract up to 5 crisp photo URLs from gallery (full -> small -> preview -> index card fallback)
        gallery = obj.get("gallery") if isinstance(obj.get("gallery"), dict) else {}
        img_urls: list[str] = []
        for tier in ("full", "small", "preview"):
            cand = gallery.get(tier)
            if isinstance(cand, list) and cand:
                img_urls = [str(u).strip() for u in cand if isinstance(u, str) and u.startswith("http")][:5]
                if img_urls:
                    break
        if not img_urls and fallback_card and fallback_card.image_urls:
            img_urls = list(fallback_card.image_urls[:5])

        return ListingDetail(
            source="unegui",
            external_id=str(external_id),
            url=url,
            deal_type=deal_type,
            title=str(obj.get("name") or (fallback_card.title if fallback_card else "")).strip(),
            description=str(obj.get("description") or "").strip(),
            location_text=str(
                loc_obj.get("name") or (fallback_card.location_text if fallback_card else "")
            ).strip(),
            lat=float(lat) if isinstance(lat, (int, float)) else None,
            lng=float(lng) if isinstance(lng, (int, float)) else None,
            rubric_slug=rubric_slug,
            raw_price_text=raw_price_text,
            start_price_text=start_price_text,
            square_meter_price_text=str(price_obj.get("square_meter_price") or "").strip(),
            currency=str(
                fallback_card.currency if fallback_card else "MNT"
            ).strip(),
            price_without_currency=str(price_wo_curr) if price_wo_curr is not None else None,
            published_label=str(
                counters.get("published")
                or (fallback_card.published_label if fallback_card else "")
            ).strip(),
            is_closed=bool(obj.get("is_closed", False)),
            favorites=int(counters["favorites"])
            if isinstance(counters.get("favorites"), int)
            else None,
            views=int(counters["advert_view"])
            if isinstance(counters.get("advert_view"), int)
            else None,
            seller_id=str(user["id"])
            if user.get("id") is not None
            else (fallback_card.seller_id if fallback_card else None),
            seller_name=str(
                user.get("name") or (fallback_card.seller_name if fallback_card else "")
            ).strip(),
            is_business=user.get("is_bussiness")
            if isinstance(user.get("is_bussiness"), bool)
            else (fallback_card.is_business if fallback_card else None),
            features=features,
            image_urls=img_urls,
        )

    # Graceful fallback to BeautifulSoup HTML parsing
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (fallback_card.title if fallback_card else "")
    desc_meta = soup.find("meta", attrs={"name": "description"})
    description = str(desc_meta.get("content") or "") if desc_meta else ""

    return ListingDetail(
        source="unegui",
        external_id=str(external_id),
        url=url,
        deal_type=deal_type,
        title=title,
        description=description,
        location_text=fallback_card.location_text if fallback_card else "",
        rubric_slug=fallback_card.rubric_slug if fallback_card else "",
        raw_price_text=fallback_card.raw_price_text if fallback_card else "",
        start_price_text=fallback_card.start_price_text if fallback_card else "",
        currency=fallback_card.currency if fallback_card else "MNT",
        price_without_currency=fallback_card.price_without_currency if fallback_card else None,
        published_label=fallback_card.published_label if fallback_card else "",
    )


class UneguiAdapter(SourceAdapter):
    """Polite unegui.mn crawler scoped to Khan-Uul District apartments."""

    name = "unegui"

    def __init__(self, http_client: PoliteHttpClient) -> None:
        self.http = http_client

    def build_search_url(self, deal_type: DealType, query: str, page: int = 1) -> str:
        path = CATEGORY_PATHS[deal_type]
        params: dict[str, Any] = {"q": query}
        if page > 1:
            params["page"] = page
        return f"{BASE_URL}{path}?{urlencode(params)}"

    def search_page(
        self, deal_type: DealType, query: str, page: int = 1
    ) -> tuple[list[IndexCard], bool]:
        url = self.build_search_url(deal_type, query, page=page)
        html = self.http.get_text(url)
        return parse_search_page(html, deal_type)

    def fetch_detail(self, card: IndexCard) -> ListingDetail:
        html = self.http.get_text(card.url)
        return parse_detail_page(
            html=html,
            external_id=card.external_id,
            url=card.url,
            deal_type=card.deal_type,
            fallback_card=card,
        )
