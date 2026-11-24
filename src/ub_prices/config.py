"""Configuration loader for complexes and filter criteria."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class FilterConfig(BaseModel):
    rooms_min: int = 3
    rooms_max: int = 5
    min_area_sqm: float = 60.0
    max_area_sqm: Optional[float] = None
    allowed_room_slugs: list[str] = Field(default_factory=lambda: ["3-r", "4-r", "5-r"])
    exclude_keywords: list[str] = Field(default_factory=list)
    standalone_parking_patterns: list[str] = Field(default_factory=list)
    proximity_words: list[str] = Field(default_factory=list)


class ComplexConfig(BaseModel):
    slug: str
    name: str
    zone: str
    search_queries: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    filters: FilterConfig = Field(default_factory=FilterConfig)
    complexes: list[ComplexConfig] = Field(default_factory=list)

    def get_complex(self, slug: str) -> Optional[ComplexConfig]:
        for c in self.complexes:
            if c.slug == slug:
                return c
        return None


def default_config_path() -> Path:
    """Resolve config/complexes.yaml relative to project root or current working dir."""
    cwd_candidate = Path.cwd() / "config" / "complexes.yaml"
    if cwd_candidate.exists():
        return cwd_candidate
    pkg_root = Path(__file__).resolve().parents[2]
    root_candidate = pkg_root / "config" / "complexes.yaml"
    if root_candidate.exists():
        return root_candidate
    return cwd_candidate


def load_config(path: Optional[Path] = None) -> AppConfig:
    cfg_path = path or default_config_path()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return AppConfig.model_validate(data)
