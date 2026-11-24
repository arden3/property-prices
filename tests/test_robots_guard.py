"""Tests enforcing unegui.mn robots.txt disallow rules before any network call."""

import pytest

from ub_prices.http_client import DisallowedPathError, check_url_allowed, is_challenge_page


@pytest.mark.parametrize(
    "url",
    [
        "https://www.unegui.mn/l-hdlh/l-hdlh-zarna/oron-suuts-zarna/hanuul/?q=river+garden",
        "https://www.unegui.mn/l-hdlh/l-hdlh-treesllne/oron-suuts/hanuul/?q=king+tower&page=2",
        "https://www.unegui.mn/adv/10910594_river-garden-1-khotkhond-117-5-mkv-3-oroo/",
    ],
)
def test_allowed_urls_pass(url: str):
    check_url_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.unegui.mn/api/adverts/",
        "https://www.unegui.mn/meta/info",
        "https://www.unegui.mn/admin/",
        "https://www.unegui.mn/moderator/",
        "https://www.unegui.mn/profile/123/",
        "https://www.unegui.mn/adv/123/edit",
        "https://www.unegui.mn/adv/123/hide",
        "https://www.unegui.mn/adv/123/author",
        "https://www.unegui.mn/foo/change_version/v2",
        "https://www.unegui.mn/ru/l-hdlh/",
        "https://www.unegui.mn/en/l-hdlh/",
        "https://www.unegui.mn/l-hdlh/l-hdlh-zarna/oron-suuts-zarna/3-r/talbai_min---117/",
        "https://www.unegui.mn/map/l-hdlh/",
        "https://www.unegui.mn/static/js/app.js",
        "https://www.unegui.mn/get_banner/1",
    ],
)
def test_disallowed_urls_raise(url: str):
    with pytest.raises(DisallowedPathError):
        check_url_allowed(url)


def test_challenge_page_detection():
    assert is_challenge_page(403, "Forbidden") is True
    assert is_challenge_page(200, "<html><title>Just a moment...</title>cf-turnstile</html>") is True
    assert is_challenge_page(200, "<html><title>Unegui.mn</title><body>Normal</body></html>") is False
