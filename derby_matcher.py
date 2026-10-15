"""Fast, local derby lookup using the curated pair list."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_SUFFIXES = {"fc", "cf", "afc", "sc", "ac", "fk", "sk", "club", "football", "ii", "b"}
_ABBREVIATIONS = {
    "manchester": "man", "united": "utd", "saint": "st",
    "internazionale": "inter", "juventus": "juve", "athletic": "ath",
    "atletico": "atleti", "munchen": "munich",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_team_name(name: str) -> str:
    """Normalize case, accents, punctuation and common club/reserve suffixes."""
    if any(marker in name for marker in ("Ã", "Â", "â")):
        try:
            name = name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    ascii_name = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = _WORD_RE.findall(ascii_name)
    while words and words[0] in _SUFFIXES - {"club"}:
        words.pop(0)
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


class DerbyMatcher:
    """Resolve names through indexed aliases, then bounded fuzzy candidates."""

    def __init__(self, pairs: Iterable[Iterable[str]], *, fuzzy_candidate_limit: int = 40):
        self.fuzzy_candidate_limit = max(1, fuzzy_candidate_limit)
        self._aliases: dict[str, set[str]] = defaultdict(set)
        normalized_pairs: set[frozenset[str]] = set()
        for pair in pairs:
            names = list(pair)
            if len(names) != 2 or not all(isinstance(name, str) and name.strip() for name in names):
                continue
            left, right = (normalize_team_name(name) for name in names)
            if not left or not right or left == right:
                continue
            normalized_pairs.add(frozenset((left, right)))
            for canonical, source in zip((left, right), names):
                self._add_aliases(canonical, source)
        self._pairs = normalized_pairs
        token_counts = Counter(token for alias in self._aliases for token in alias.split())
        token_index: dict[str, set[str]] = defaultdict(set)
        prefix_index: dict[str, set[str]] = defaultdict(set)
        self._canonical_aliases: dict[str, set[str]] = defaultdict(set)
        for alias, canonicals in self._aliases.items():
            for canonical in canonicals:
                self._canonical_aliases[canonical].add(alias)
            for token in set(alias.split()):
                token_index[token].update(canonicals)
                if len(token) >= 3:
                    prefix_index[token[:3]].update(canonicals)
        # Freeze ordered buckets once at startup so each lookup can take a
        # fixed-size slice without iterating a broad bucket.
        self._token_index = {token: tuple(sorted(names)) for token, names in token_index.items()}
        self._prefix_index = {prefix: tuple(sorted(names)) for prefix, names in prefix_index.items()}
        self._fuzzy_aliases = {
            canonical: tuple(sorted(aliases)[:8])
            for canonical, aliases in self._canonical_aliases.items()
        }
        self._token_counts = token_counts
        self._resolve_cached = lru_cache(maxsize=4096)(self._resolve_uncached)

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "DerbyMatcher":
        source = Path(path) if path is not None else Path(__file__).with_name("derby_games_scoped.json")
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError(f"Derby list must contain a JSON array: {source}")
        return cls(data)

    def _add_aliases(self, canonical: str, source: str) -> None:
        base = normalize_team_name(source)
        aliases = {canonical, base}
        words = base.split()
        if words:
            core_words = [word for word in words if word not in _SUFFIXES]
            if core_words:
                aliases.add(" ".join(core_words))
                if len(core_words) > 1:
                    aliases.add("".join(word[0] for word in core_words))
                    aliases.add(f"{core_words[0]} {''.join(word[0] for word in core_words[1:])}")
                    aliases.add(f"{core_words[0][0]} {' '.join(core_words[1:])}")
                    aliases.add(" ".join(_ABBREVIATIONS.get(word, word) for word in core_words))
                    for index, word in enumerate(core_words):
                        short = _ABBREVIATIONS.get(word)
                        if short:
                            variant = core_words.copy()
                            variant[index] = short
                            aliases.add(" ".join(variant))
        for alias in aliases:
            if alias:
                self._aliases[alias].add(canonical)

    def _resolve_uncached(self, name: str) -> str | None:
        normalized = normalize_team_name(name)
        exact = self._aliases.get(normalized, set())
        if len(exact) == 1:
            return next(iter(exact))
        if len(exact) > 1 or not normalized:
            return None

        tokens = normalized.split()
        candidates: set[str] = set()
        # Rarest-token-first limits work to related indexed names.
        for token in sorted(set(tokens), key=lambda item: self._token_counts.get(item, 0)):
            candidates.update(self._token_index.get(token, ())[: self.fuzzy_candidate_limit])
            if len(candidates) >= self.fuzzy_candidate_limit:
                break
        if len(candidates) < self.fuzzy_candidate_limit:
            for token in tokens:
                if len(token) >= 3:
                    candidates.update(self._prefix_index.get(token[:3], ())[: self.fuzzy_candidate_limit - len(candidates)])
                    if len(candidates) >= self.fuzzy_candidate_limit:
                        break
        if len(candidates) > self.fuzzy_candidate_limit:
            candidates = set(sorted(candidates)[: self.fuzzy_candidate_limit])
        ranked = []
        for canonical in candidates:
            for alias in self._fuzzy_aliases[canonical]:
                if not _shares_token_prefix(tokens, alias.split()):
                    continue
                ranked.append((SequenceMatcher(None, normalized, alias).ratio(), canonical))
        ranked.sort(reverse=True)
        if not ranked:
            return None
        best_score, best_canonical = ranked[0]
        second_score = next((score for score, canonical in ranked[1:] if canonical != best_canonical), 0.0)
        if best_score < 0.88 or best_score - second_score < 0.08:
            return None
        return best_canonical

    def resolve(self, name: str) -> str | None:
        return self._resolve_cached(name.strip()) if isinstance(name, str) else None

    def is_derby(self, home: str, away: str) -> bool:
        left, right = self.resolve(home), self.resolve(away)
        return bool(left and right and left != right and frozenset((left, right)) in self._pairs)

    @property
    def alias_count(self) -> int:
        return len(self._aliases)

    @property
    def pair_count(self) -> int:
        return len(self._pairs)


def _shares_token_prefix(left: list[str], right: list[str]) -> bool:
    return any(
        first == second
        or (len(first) >= 3 and len(second) >= 3 and first[:3] == second[:3])
        for first in left
        for second in right
    )
