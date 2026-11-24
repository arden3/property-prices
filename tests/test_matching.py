"""Tests for Latin/Cyrillic complex matching, precedence, proximity & exclusion guards."""

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
        ("Bella Vista хотхонд 140 мкв 4 өрөө түрээслүүлнэ", "bella-vista"),
        ("Белла Виста 3 өрөө бүрэн тавилгатай", "bella-vista"),
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
        ("Marshall Town хотхонд 150мкв 4 өрөө", "marshall-town"),
        ("Делюкс Маршалл хотхонд 125мкв 3 өрөө", "marshall-town"),
        ("Japan Town C3 108мкв 3 өрөө байр зарна", "japan-town"),
        ("Жапан таун хотхонд 4 өрөө түрээслүүлнэ", "japan-town"),
        ("Япон таун 98 мкв 3 өрөө", "japan-town"),
        ("Vega City хотхонд 88мкв 3 өрөө зарна", "vega-city"),
        ("Вега сити 112м2 4 өрөө орон сууц", "vega-city"),
        ("Gerlug Vista хотхонд 85мкв 3 өрөө", "gerlug-vista"),
        ("Гэрлүг виста 104мкв 4 өрөө", "gerlug-vista"),
        ("Jardin Residence 160мкв 4 өрөө зарна", "jardin"),
        ("Жардин хотхонд 145мкв 3 өрөө түрээслүүлнэ", "jardin"),
        ("Seoul Royal County 190мкв 4 өрөө", "seoul-royal-county"),
        ("Сөүл рояал каунти хотхонд 210мкв 5 өрөө", "seoul-royal-county"),
        ("Сөүл роял каунти 4 өрөө байр зарна", "seoul-royal-county"),
    ],
)
def test_matches_all_12_complexes_latin_and_cyrillic(matcher: ComplexMatcher, title: str, expected_slug: str):
    res = matcher.match(title)
    assert res.matched is True, f"Failed to match: {title} ({res})"
    assert res.complex_slug == expected_slug


def test_king_tower_takes_precedence_over_marshall_town(matcher: ComplexMatcher):
    res = matcher.match("Маршалл таун Кинг тауэр хотхонд 120мкв 4 өрөө")
    assert res.matched is True
    assert res.complex_slug == "king-tower"


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
        "Vega city-н урд байрлах Orchid Park хотхонд 4 өрөө орон сууц",
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
    ],
)
def test_rejects_similar_named_other_complexes(matcher: ComplexMatcher, title: str):
    res = matcher.match(title)
    assert res.matched is False, f"Should not match non-watchlist complex: {title} ({res})"


@pytest.mark.parametrize(
    "title,desc",
    [
        ("King Tower-т 140мкв үйлчилгээний талбай зарна", ""),
        ("River Garden 2 дулаан зогсоол зарна", ""),
        ("Жардин хотхонд хоногийн түрээс 3 өрөө", ""),
        ("Jardin Residence 3 өрөө түрээслүүлнэ", "Цэвэрхэн 3 өрөө хоногоор түрээслүүлнэ"),
        ("Bella Vista оффисын талбай түрээслүүлнэ", ""),
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
