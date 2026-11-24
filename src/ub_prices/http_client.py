"""Polite HTTP client with strict robots.txt enforcement and clean block detection."""

from __future__ import annotations

import logging
import random
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Explicit rules from https://www.unegui.mn/robots.txt (User-agent: *)
UNEGUI_DISALLOWED_PREFIXES = (
    "/api",
    "/meta",
    "/admin",
    "/moderator",
    "/profile",
    "/ru/",
    "/en/",
    "/map/",
    "/static/",
    "/get_banner/",
)

UNEGUI_DISALLOWED_SUBSTRINGS = (
    "---",  # Disallow: *---*
    "/edit",
    "/hide",
    "/author",
    "/change_version/",
)

# Indicators of a Cloudflare / Turnstile / CAPTCHA challenge page
_CHALLENGE_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "just a moment...",
    "attention required! | cloudflare",
    "verify you are human",
    "cf-turnstile",
)


class DisallowedPathError(ValueError):
    """Raised when a URL violates robots.txt disallow rules."""


class BlockedError(RuntimeError):
    """Raised when the target site returns a 403 or anti-bot challenge."""


def check_url_allowed(url: str) -> None:
    """Validate URL path against unegui.mn robots.txt rules before any HTTP request."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    path_lower = path.lower()

    if parsed.hostname and parsed.hostname.endswith("unegui.mn"):
        for prefix in UNEGUI_DISALLOWED_PREFIXES:
            if path_lower == prefix.rstrip("/") or path_lower.startswith(prefix):
                raise DisallowedPathError(
                    f"URL path '{path}' violates robots.txt prefix rule '{prefix}'"
                )
        for sub in UNEGUI_DISALLOWED_SUBSTRINGS:
            if sub in path_lower:
                raise DisallowedPathError(
                    f"URL path '{path}' violates robots.txt pattern rule '*{sub}*'"
                )


def is_challenge_page(status_code: int, body: str) -> bool:
    if status_code == 403:
        return True
    head = (body or "")[:8192].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


class PoliteHttpClient:
    """Single-threaded HTTP client with polite jitter delays and robots.txt guards."""

    def __init__(
        self,
        min_delay: float = 3.0,
        max_delay: float = 6.0,
        timeout: float = 25.0,
        max_retries: int = 2,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.requests_made = 0
        self._last_request_ts: Optional[float] = None
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "mn,en-US;q=0.9,en;q=0.8",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteHttpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _sleep_politely(self) -> None:
        if self._last_request_ts is None or self.max_delay <= 0:
            return
        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.monotonic() - self._last_request_ts
        remaining = delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get_text(self, url: str) -> str:
        """Fetch URL HTML text after robots.txt validation and polite pacing."""
        check_url_allowed(url)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._sleep_politely()
            try:
                resp = self._client.get(url)
                self._last_request_ts = time.monotonic()
                self.requests_made += 1

                # Also verify final redirected URL stays robots.txt compliant
                check_url_allowed(str(resp.url))

                text = resp.text
                if is_challenge_page(resp.status_code, text):
                    raise BlockedError(
                        f"Blocked or challenge page received (HTTP {resp.status_code}) for {url}"
                    )

                if resp.status_code == 200:
                    return text

                if resp.status_code == 404:
                    raise httpx.HTTPStatusError(
                        f"404 Not Found: {url}", request=resp.request, response=resp
                    )

                if resp.status_code in (429, 500, 502, 503, 504):
                    backoff = (2 ** attempt) * max(self.min_delay, 2.0)
                    logger.warning(
                        "Transient HTTP %s for %s; backing off %.1fs (attempt %d/%d)",
                        resp.status_code,
                        url,
                        backoff,
                        attempt + 1,
                        self.max_retries + 1,
                    )
                    if attempt < self.max_retries:
                        time.sleep(backoff)
                        continue
                resp.raise_for_status()
            except (BlockedError, DisallowedPathError):
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise
                last_err = e
            except httpx.RequestError as e:
                last_err = e
                if attempt < self.max_retries:
                    backoff = (2 ** attempt) * max(self.min_delay, 2.0)
                    logger.warning("Network error for %s: %s; retrying in %.1fs", url, e, backoff)
                    time.sleep(backoff)
                    continue
                raise

        if last_err is not None:
            raise last_err
        raise RuntimeError(f"Failed to fetch {url}")
