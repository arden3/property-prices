"""Latin/Cyrillic complex matcher with proximity & commercial/short-term rental guards."""

from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from ub_prices.config import AppConfig, ComplexConfig
from ub_prices.models import MatchResult

# Cyrillic-to-Latin rough phonetic transliteration for cross-script matching
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "ө": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ү": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Fold Mongolian Cyrillic spelling confusables for normalized matching
_CYR_FOLD = str.maketrans({
    "ү": "у",
    "ө": "о",
    "ё": "е",
    "э": "е",
    "й": "и",
    "ь": "",
    "ъ": "",
})

# Mongolian possessive / genitive / locative suffixes AFTER normalize_text()
# (hyphen becomes space and 'й' folds to 'и', so "River Garden-ийн" -> "river garden иин",
#  and "Vega city-н урд" -> "vega city н урд")
_MN_SUFFIX_RE = r"(?:\s*(?:иин|ын|нии|ны|ин|н|д|т|нд|руу|таи|теи|тои|аас|еес|оос|ииг|ыг))?"


def normalize_text(text: str) -> str:
    """Lowercase, fold Cyrillic confusables, and normalize whitespace/punctuation."""
    if not text:
        return ""
    s = text.lower().translate(_CYR_FOLD)
    # Replace punctuation/separators with spaces (preserve digits & letters)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"_+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def transliterate_to_latin(text: str) -> str:
    """Transliterate Cyrillic characters into normalized Latin tokens."""
    norm = normalize_text(text)
    out = []
    for ch in norm:
        out.append(_CYR_TO_LAT.get(ch, ch))
    s = "".join(out)
    # Fold common Latin vowel/consonant variants (w->v, ph->f, double letters)
    s = s.replace("w", "v").replace("y", "i").replace("kh", "h")
    s = re.sub(r"(.)\1+", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class ComplexMatcher:
    """Matches listing text (title, location, description) to one of the configured complexes."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._exclude_keywords_norm = [
            normalize_text(kw) for kw in config.filters.exclude_keywords if kw.strip()
        ]
        self._parking_regexes = [
            re.compile(pat, flags=re.IGNORECASE | re.UNICODE)
            for pat in config.filters.standalone_parking_patterns
        ]
        # Build proximity regex tail (normalized)
        prox_terms = sorted(
            {normalize_text(w) for w in config.filters.proximity_words if w.strip()},
            key=len,
            reverse=True,
        )
        self._proximity_tail = "|".join(re.escape(w) for w in prox_terms) if prox_terms else ""

        # Precompile per-complex alias tables
        self._compiled: list[tuple[ComplexConfig, list[str], list[str], list[str]]] = []
        for comp in config.complexes:
            norm_aliases = sorted(
                {normalize_text(a) for a in ([comp.name] + comp.aliases) if a.strip()},
                key=len,
                reverse=True,
            )
            lat_aliases = sorted(
                {transliterate_to_latin(a) for a in ([comp.name] + comp.aliases) if a.strip()},
                key=len,
                reverse=True,
            )
            norm_excludes = [normalize_text(e) for e in comp.exclude if e.strip()]
            self._compiled.append((comp, norm_aliases, lat_aliases, norm_excludes))

    def check_global_exclusion(self, title: str, description: str = "") -> Optional[str]:
        """Return rejection reason if the listing is commercial, standalone garage, or short-term rental."""
        title_norm = normalize_text(title)
        for kw in self._exclude_keywords_norm:
            if kw and kw in title_norm:
                return f"excluded_keyword_title:{kw}"

        for rx in self._parking_regexes:
            if rx.search(title or ""):
                return "excluded_standalone_parking"

        # Short-term / daily rentals sometimes only appear in the description
        if description:
            desc_norm = normalize_text(description)
            for daily_kw in ("хоногиин туреес", "хоногоор", "одроор турееслуулне", "цагаар турееслуулне"):
                if daily_kw in desc_norm:
                    return f"excluded_short_term_desc:{daily_kw}"

        return None

    def _is_proximity_reference(self, raw_text: str, alias_norm: str) -> bool:
        """Detect if alias_norm in raw_text is followed by a proximity word (e.g. 'хажууд', 'урд')."""
        if not self._proximity_tail or not raw_text:
            return False
        text_norm = normalize_text(raw_text)
        # Allow optional phase/block token (1, 2, хотхон, residence) + Mongolian genitive suffix before proximity word
        pattern = (
            rf"(?<![\w]){re.escape(alias_norm)}"
            rf"(?:\s+(?:1|2|3|i|ii|хотхон|резиденс|residence|town|таун|villa|вилла))?"
            rf"{_MN_SUFFIX_RE}\s+(?:{self._proximity_tail})(?![\w])"
        )
        return bool(re.search(pattern, text_norm, flags=re.UNICODE))

    def _contains_whole_phrase(self, haystack: str, needle: str) -> bool:
        if not haystack or not needle:
            return False
        return bool(re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack, flags=re.UNICODE))

    def _match_in_field(
        self, raw_text: str, field_name: str, allow_fuzzy: bool = True
    ) -> Optional[MatchResult]:
        if not raw_text or not raw_text.strip():
            return None

        text_norm = normalize_text(raw_text)
        text_lat = transliterate_to_latin(raw_text)
        tokens_lat = text_lat.split()

        for comp, norm_aliases, lat_aliases, norm_excludes in self._compiled:
            # Skip if an explicit complex-level exclusion phrase appears (e.g., "river stone" for river-garden)
            if any(self._contains_whole_phrase(text_norm, ex) for ex in norm_excludes):
                # Only skip if a true positive alias does NOT also appear separately
                has_exact_pos = any(
                    self._contains_whole_phrase(text_norm, a)
                    and not any(a in ex for ex in norm_excludes)
                    for a in norm_aliases
                )
                if not has_exact_pos:
                    continue

            # 1. Exact normalized phrase match
            for alias in norm_aliases:
                if self._contains_whole_phrase(text_norm, alias):
                    if self._is_proximity_reference(raw_text, alias):
                        return MatchResult(
                            matched=False,
                            rejected_reason=f"proximity_reference:{comp.slug}:{alias}",
                        )
                    conf = "high" if field_name in ("title", "location") else "medium"
                    return MatchResult(
                        matched=True,
                        complex_slug=comp.slug,
                        complex_name=comp.name,
                        confidence=conf,
                        reason=f"exact_{field_name}:{alias}",
                    )

            # 2. Cross-script transliterated match (e.g., mixed script or unusual Cyrillic spelling)
            for lat_alias in lat_aliases:
                if len(lat_alias) < 5:
                    continue
                if self._contains_whole_phrase(text_lat, lat_alias):
                    # Also verify no proximity word in original text after approximate position
                    if self._proximity_tail and self._check_latin_proximity(raw_text, lat_alias):
                        return MatchResult(
                            matched=False,
                            rejected_reason=f"proximity_reference_lat:{comp.slug}:{lat_alias}",
                        )
                    conf = "high" if field_name in ("title", "location") else "medium"
                    return MatchResult(
                        matched=True,
                        complex_slug=comp.slug,
                        complex_name=comp.name,
                        confidence=conf,
                        reason=f"translit_{field_name}:{lat_alias}",
                    )

            # 3. Fuzzy token-window match (handles minor typos like "riwer garden", "king tover")
            if allow_fuzzy and len(tokens_lat) >= 1:
                best_score = 0.0
                best_alias = ""
                for lat_alias in lat_aliases:
                    alias_tokens = lat_alias.split()
                    n = len(alias_tokens)
                    if n == 0 or len(lat_alias) < 6 or len(tokens_lat) < n:
                        continue
                    for i in range(len(tokens_lat) - n + 1):
                        window = " ".join(tokens_lat[i : i + n])
                        score = fuzz.ratio(window, lat_alias)
                        if score > best_score:
                            best_score = score
                            best_alias = lat_alias
                # Strict threshold (>= 88) prevents false positives like "central garden" -> "river garden"
                if best_score >= 88.0:
                    if self._proximity_tail and self._check_latin_proximity(raw_text, best_alias):
                        return MatchResult(
                            matched=False,
                            rejected_reason=f"proximity_reference_fuzzy:{comp.slug}:{best_alias}",
                        )
                    return MatchResult(
                        matched=True,
                        complex_slug=comp.slug,
                        complex_name=comp.name,
                        confidence="fuzzy",
                        reason=f"fuzzy_{field_name}:{best_alias}({best_score:.0f})",
                    )

        return None

    def _check_latin_proximity(self, raw_text: str, lat_alias: str) -> bool:
        """Check if a proximity word appears shortly after the transliterated alias."""
        if not self._proximity_tail:
            return False
        text_lat = transliterate_to_latin(raw_text)
        prox_lat = "|".join(
            re.escape(transliterate_to_latin(w))
            for w in self.config.filters.proximity_words
            if w.strip()
        )
        if not prox_lat:
            return False
        # Within 1–2 short tokens (e.g. phase number + genitive suffix)
        pattern = rf"(?<![\w]){re.escape(lat_alias)}[a-z0-9]{{0,5}}(?:\s+[a-z0-9]{{1,8}}){{0,2}}\s+(?:{prox_lat})(?![\w])"
        return bool(re.search(pattern, text_lat))

    def match(
        self, title: str, location_text: str = "", description: str = ""
    ) -> MatchResult:
        """Match a listing to one of the 12 complexes or return rejected/unmatched."""
        excl = self.check_global_exclusion(title, description)
        if excl:
            return MatchResult(matched=False, rejected_reason=excl)

        # Priority: 1) Title, 2) Structured Unegui Location (e.g. "Улаанбаатар — Хан-Уул — River Garden"), 3) Description
        for field_name, text, allow_fuzzy in (
            ("title", title, True),
            ("location", location_text, False),
            ("description", description, False),
        ):
            res = self._match_in_field(text, field_name, allow_fuzzy=allow_fuzzy)
            if res is not None:
                return res

        return MatchResult(matched=False, reason="no_complex_match")
