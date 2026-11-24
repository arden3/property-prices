"""Abstract base class for property portal source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ub_prices.models import DealType, IndexCard, ListingDetail


class SourceAdapter(ABC):
    name: str

    @abstractmethod
    def build_search_url(self, deal_type: DealType, query: str, page: int = 1) -> str:
        """Build a robots.txt-compliant search URL for a deal type, query, and page."""

    @abstractmethod
    def search_page(
        self, deal_type: DealType, query: str, page: int = 1
    ) -> tuple[list[IndexCard], bool]:
        """Fetch and parse one search results page, returning (cards, has_next_page)."""

    @abstractmethod
    def fetch_detail(self, card: IndexCard) -> ListingDetail:
        """Fetch and parse the full detail page for an IndexCard."""
