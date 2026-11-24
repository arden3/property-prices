"""Tests for parsing unegui.mn Next.js App Router RSC search & detail payloads."""

import json

from ub_prices.models import DealType
from ub_prices.sources.unegui import parse_detail_page, parse_search_page


def _wrap_rsc(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False)
    escaped = json.dumps(raw, ensure_ascii=False)  # produces valid JS double-quoted string
    return f"<html><body><script>self.__next_f.push([1,{escaped}])</script></body></html>"


def test_parse_search_page_rsc():
    payload = {
        "next_page": "?q=river+garden&page=2",
        "count": 2,
        "adverts": [
            {
                "id": 10910594,
                "title": "Ривер гарден 1 хотхонд 117.5 мкв 3 өрөө",
                "price": "830 сая ₮",
                "start_price": "880 сая ₮",
                "currency": "MNT",
                "price_without_currency": "830000000",
                "published": "Өчигдөр",
                "url": "/adv/10910594_river-garden-1/",
                "location": "Улаанбаатар — Хан-Уул — River Garden",
                "rubric": {"id": 1879, "slug": "3-r"},
                "user": {"id": 629770, "name": "Отгонтуяа", "is_bussiness": True},
                "img_count": 14,
            }
        ],
    }
    html = _wrap_rsc(payload)
    cards, has_next = parse_search_page(html, DealType.SALE)

    assert has_next is True
    assert len(cards) == 1
    c = cards[0]
    assert c.external_id == "10910594"
    assert c.url == "https://www.unegui.mn/adv/10910594_river-garden-1/"
    assert c.rubric_slug == "3-r"
    assert c.price_without_currency == "830000000"
    assert c.is_business is True


def test_parse_detail_page_rsc():
    payload = {
        "id": 10910594,
        "name": "Ривер гарден 1 хотхонд 117.5 мкв 3 өрөө",
        "description": "3 давхарт, гэрчилгээ бэлэн",
        "is_closed": False,
        "counters": {"favorites": 4, "advert_view": 29, "published": "Өчигдөр"},
        "price": {
            "price": "830 сая ₮",
            "start_price": "880,000,000 ₮",
            "square_meter_price": None,
            "price_without_currency": "830000000",
        },
        "location": {
            "name": "Улаанбаатар — Хан-Уул — River Garden",
            "coordinates": {"lat": 47.88927, "lng": 106.93287},
        },
        "features": [
            {"name": "Талбай", "value": "117.5 м²"},
            {"name": "Хэдэн давхарт", "value": "3"},
            {"name": "Барилгын давхар", "value": "9"},
            {"name": "Ашиглалтанд орсон он", "value": "2017"},
            {"name": "Тагт", "value": "1 тагттай"},
            {"name": "Гараж", "value": "Байхгүй"},
        ],
        "categories": [
            {"slug": "l-hdlh"},
            {"slug": "oron-suuts-zarna"},
            {"slug": "3-r"},
        ],
        "user": {"id": 629770, "name": "Отгонтуяа", "is_bussiness": True},
    }
    html = _wrap_rsc(payload)
    detail = parse_detail_page(
        html=html,
        external_id="10910594",
        url="https://www.unegui.mn/adv/10910594_river-garden-1/",
        deal_type=DealType.SALE,
    )
    assert detail.external_id == "10910594"
    assert detail.rubric_slug == "3-r"
    assert detail.features["Талбай"] == "117.5 м²"
    assert detail.features["Хэдэн давхарт"] == "3"
    assert detail.lat == 47.88927
    assert detail.is_closed is False
