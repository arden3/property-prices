"""Tests for Latin/Cyrillic complex matching, proximity & exclusion guards (8-complex watchlist)."""

import pytest

from ub_prices.config import load_config
from ub_prices.matching import ComplexMatcher


@pytest.fixture(scope="module")
def matcher() -> ComplexMatcher:
    return ComplexMatcher(load_config())


@pytest.mark.parametrize(
    "title,expected_slug",
    [
        ("Ривер гарден 1 хотхонд 117.5 мкв 3 өрөө", "river-garden"),
        ("River Garden 2 хотхонд 120мкв 4 өрөө байр зарна", "river-garden"),
        ("Ривэр гардэн 2-р ээлж 5 өрөө", "river-garden"),
        ("Buddha Vista 4 өрөө пентхаус зарна", "buddha-vista"),
        ("Будда виста хотхонд 130м2 3 өрөө", "buddha-vista"),
        ("Time Square хотхонд 110мкв 3 өрөө", "time-square"),
        ("Тайм скүэр хотхонд 4 өрөө байр худалдана", "time-square"),
        ("Таймс сквер 95мкв 3 өрөө", "time-square"),
        ("Khan Hills хотхонд 3 өрөө орон сууц", "khan-hills"),
        ("Хан хиллз хотхонд 105мкв 3 өрөө", "khan-hills"),
        ("King Tower 135мкв 4 өрөө байр зарна", "king-tower"),
        ("Кинг тауэр 5-р ээлж 118мкв 3 өрөө", "king-tower"),
        ("Кинг товер хотхонд 4 өрөө түрээслүүлнэ", "king-tower"),
        ("Japan Town C3 108мкв 3 өрөө байр зарна", "japan-town"),
        ("Жапан таун хотхонд 4 өрөө түрээслүүлнэ", "japan-town"),
        ("Япон таун 98 мкв 3 өрөө", "japan-town"),
        ("Jardin Residence 160мкв 4 өрөө зарна", "jardin"),
        ("Жардин хотхонд 145мкв 3 өрөө түрээслүүлнэ", "jardin"),
        ("Seoul Royal County 190мкв 4 өрөө", "seoul-royal-county"),
        ("Сөүл рояал каунти хотхонд 210мкв 5 өрөө", "seoul-royal-county"),
        ("Сөүл роял каунти 4 өрөө байр зарна", "seoul-royal-county"),
    ],
)
def test_matches_all_8_complexes_latin_and_cyrillic(matcher: ComplexMatcher, title: str, expected_slug: str):
    res = matcher.match(title)
    assert res.matched is True, f"Failed to match: {title} ({res})"
    assert res.complex_slug == expected_slug


def test_location_fallback_matches_when_title_omits_complex(matcher: ComplexMatcher):
    res = matcher.match(
        title="120мкв 4 өрөө байр зарна",
        location_text="Улаанбаатар — Хан-Уул — River Garden",
    )
    assert res.matched is True
    assert res.complex_slug == "river-garden"
    assert res.confidence == "high"


@pytest.mark.parametrize(
    "title",
    [
        "Ривер гардений хажууд 90мкв 3 өрөө байр зарна",
        "River Garden-ийн урд 85мкв 3 өрөө орон сууц",
        "Кинг тауэрын ойролцоо 3 өрөө байр түрээслүүлнэ",
        "Жапан тауны дэргэд 100мкв 3 өрөө",
        "Хан хиллсийн баруун талд шинэ 3 өрөө",
        "River Garden-н урд байрлах шинэ хотхонд 4 өрөө",
    ],
)
def test_rejects_proximity_references(matcher: ComplexMatcher, title: str):
    res = matcher.match(title)
    assert res.matched is False, f"Should reject proximity ad: {title} ({res})"
    assert res.rejected_reason and "proximity" in res.rejected_reason


@pytest.mark.parametrize(
    "title",
    [
        "River Stone хотхонд 85мкв 3 өрөө зарна",
        "Ривер стоун 2 хотхонд 3 өрөө",
        "River Plaza 115мкв 3 өрөө",
        "Royal Green Villa 180мкв 4 өрөө",
        "Роял кроун вилла 200мкв 5 өрөө",
        "Сансар Central Garden хотхонд 75мкв 3 өрөө",
        "Garden City Яармагт 90мкв 3 өрөө",
        "Хан-уул, токио таун1 т 3 өрөө байр 103.5m2",
        "Japan town баруун royal garden 5 өрөө граштай 220мкв",
        # Removed complexes must no longer match
        "Bella Vista хотхонд 140 мкв 4 өрөө түрээслүүлнэ",
        "Vega City хотхонд 88мкв 3 өрөө зарна",
        "Gerlug Vista хотхонд 85мкв 3 өрөө",
        "Marshall Town хотхонд 150мкв 4 өрөө",
    ],
)
def test_rejects_non_watchlist_and_removed_complexes(matcher: ComplexMatcher, title: str):
    res = matcher.match(title)
    assert res.matched is False, f"Should not match excluded complex: {title} ({res})"


@pytest.mark.parametrize(
    "title,desc",
    [
        ("King Tower-т 140мкв үйлчилгээний талбай зарна", ""),
        ("River Garden 2 дулаан зогсоол зарна", ""),
        ("Жардин хотхонд хоногийн түрээс 3 өрөө", ""),
        ("Jardin Residence 3 өрөө түрээслүүлнэ", "Цэвэрхэн 3 өрөө хоногоор түрээслүүлнэ"),
        ("Buddha Vista оффисын талбай түрээслүүлнэ", ""),
    ],
)
def test_rejects_commercial_parking_and_short_term_rentals(matcher: ComplexMatcher, title: str, desc: str):
    res = matcher.match(title, description=desc)
    assert res.matched is False, f"Should reject excluded listing: {title} ({res})"
    assert res.rejected_reason is not None


def test_allows_apartment_with_parking_included(matcher: ComplexMatcher):
    res = matcher.match("River Garden 2 дээр 73мкв 3 өрөө зогсоолтой зарна")
    assert res.matched is True
    assert res.complex_slug == "river-garden"
