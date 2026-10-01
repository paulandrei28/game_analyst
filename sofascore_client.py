from __future__ import annotations

import asyncio
import logging
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

LOGGER = logging.getLogger(__name__)

SOFASCORE_BASE_URL = "https://www.sofascore.com"
SOFASCORE_API_PREFIX = "/api/v1"
SOFASCORE_FOOTBALL_URL = f"{SOFASCORE_BASE_URL}/football"


@dataclass(frozen=True)
class ResourceSpec:
    """Reusable description of one SofaScore API resource."""

    name: str
    path: str


@dataclass(frozen=True)
class SofascoreSearchResult:
    """One result returned by SofaScore's global search modal."""

    kind: str
    text: str
    href: str | None
    data_id: str | None = None
    entity_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a backwards-friendly dictionary representation."""
        return {
            "kind": self.kind,
            "text": self.text,
            "href": self.href,
            "data_id": self.data_id,
            "entity_id": self.entity_id,
        }


class SofascoreSearcher:
    """UI-backed global search for SofaScore entities.

    Search is performed through the normal SofaScore web UI rather than
    calling a hidden search endpoint directly. The search sequence mirrors
    the working manual test: warm page -> open search -> human-paced typing
    -> wait for results -> read links from the visible modal.
    """

    _KIND_PATTERNS: dict[str, tuple[str, ...]] = {
        "game": ("/football/match/", "/football/match"),
        "referee": ("/referee/",),
        "team": ("/team/",),
        "player": ("/player/",),
        "tournament": ("/unique-tournament/",),
        "manager": ("/manager/",),
    }

    # Known consent-management-platform selectors, checked before falling back
    # to generic text matching.
    _COOKIE_CONSENT_SELECTORS: tuple[str, ...] = (
        "#onetrust-accept-btn-handler",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#didomi-notice-agree-button",
    )
    _COOKIE_CONSENT_TEXT_PATTERN = re.compile(
        r"\b(accept all|accept cookies|accept|i agree|i accept|allow all|agree to all|consent|i consent)\b",
        re.IGNORECASE,
    )
    _ROBOT_CHECK_FRAME_SELECTOR = 'iframe[title*="recaptcha" i], iframe[src*="recaptcha" i]'

    def __init__(
        self,
        client: "SofascoreClient",
        *,
        open_delay_min: float = 1.0,
        open_delay_max: float = 2.0,
        post_open_delay_min: float = 1.0,
        post_open_delay_max: float = 2.0,
        typing_delay_min_ms: int = 50,
        typing_delay_max_ms: int = 80,
        results_delay_min: float = 2.0,
        results_delay_max: float = 3.0,
        navigation_delay_min: float = 1.0,
        navigation_delay_max: float = 2.0,
        search_interval_min: float = 6.0,
        search_interval_jitter: float = 3.0,
    ) -> None:
        self.client = client
        self.open_delay_min = max(0.0, open_delay_min)
        self.open_delay_max = max(self.open_delay_min, open_delay_max)
        self.post_open_delay_min = max(0.0, post_open_delay_min)
        self.post_open_delay_max = max(self.post_open_delay_min, post_open_delay_max)
        self.typing_delay_min_ms = max(1, int(typing_delay_min_ms))
        self.typing_delay_max_ms = max(
            self.typing_delay_min_ms, int(typing_delay_max_ms)
        )
        self.results_delay_min = max(0.0, results_delay_min)
        self.results_delay_max = max(self.results_delay_min, results_delay_max)
        self.navigation_delay_min = max(0.0, navigation_delay_min)
        self.navigation_delay_max = max(self.navigation_delay_min, navigation_delay_max)
        self.search_interval_min = max(0.0, search_interval_min)
        self.search_interval_jitter = max(0.0, search_interval_jitter)
        self._last_search_at: float | None = None
        self._game_details_cache: dict[str, dict[str, Any] | None] = {}

    async def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        max_results: int | None = None,
        ensure_search_page: bool = True,
    ) -> list[SofascoreSearchResult]:
        """Search SofaScore and return typed results from the visible modal."""
        query = " ".join(str(query).split())
        if not query:
            return []
        if kind is not None and kind not in self._KIND_PATTERNS:
            raise ValueError(
                f"Unknown SofaScore search kind '{kind}'. "
                f"Available: {', '.join(sorted(self._KIND_PATTERNS))}"
            )

        async with self.client._browser_lock:
            await self._wait_for_search_slot()
            if ensure_search_page:
                await self._ensure_search_page()
            await self._open_search()
            search_input = await self._get_search_input()
            if search_input is None:
                # The search control can be hidden behind a cookie banner or a
                # bot-check prompt; dismiss those and retry once before giving up.
                if await self._dismiss_blocking_overlays():
                    await self._open_search()
                    search_input = await self._get_search_input()
            if search_input is None:
                LOGGER.warning("Could not find SofaScore search input")
                return []

            await self._sleep_random(
                "Before typing SofaScore search",
                self.post_open_delay_min,
                self.post_open_delay_max,
            )
            await search_input.click()
            await search_input.fill("")
            await search_input.type(
                query,
                delay=random.randint(
                    self.typing_delay_min_ms,
                    self.typing_delay_max_ms,
                ),
            )
            await self._sleep_random(
                "Waiting for SofaScore search results",
                self.results_delay_min,
                self.results_delay_max,
            )

            results = await self._read_modal_results()
            if kind is not None:
                results = [result for result in results if result.kind == kind]
            if max_results is not None:
                results = results[: max(0, max_results)]
            return results

    async def games(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SofascoreSearchResult]:
        """Search for football matches."""
        return await self.search(query, kind="game", max_results=max_results)

    async def referees(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SofascoreSearchResult]:
        """Search for referees."""
        return await self.search(query, kind="referee", max_results=max_results)

    async def teams(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SofascoreSearchResult]:
        """Search for teams."""
        return await self.search(query, kind="team", max_results=max_results)

    _GENERIC_TEAM_SUFFIXES = {
        "fc",
        "cf",
        "sc",
        "afc",
        "cfc",
        "sfc",
        "fcf",
        "fk",
    }
    _RESERVE_MARKERS = {"ii", "2", "reserve", "b"}

    async def find_game(
        self,
        home_team: str,
        away_team: str,
        *,
        target_date: str | date | None = None,
    ) -> SofascoreSearchResult | None:
        """Search progressively and return the game for ``target_date`` when supplied."""
        requested_date = self.client._coerce_date(target_date)
        queries = self._build_game_search_queries(home_team, away_team)
        fallback: SofascoreSearchResult | None = None

        for query in queries:
            LOGGER.info(
                "SofaScore game search query: %r%s",
                query,
                (
                    f" | target_date={requested_date.isoformat()}"
                    if requested_date
                    else ""
                ),
            )
            try:
                # Deliberately inspect only the first SofaScore result for each
                # query. If that result is not the requested fixture, relax the
                # query and try again instead of scanning a large result set.
                results = await self.games(query, max_results=1)
            except Exception:
                LOGGER.exception("SofaScore game search failed for query %r", query)
                continue

            if not results:
                LOGGER.info("SofaScore game search query %r returned no games", query)
                continue

            result = results[0]
            candidate_date = await self._game_result_date(
                result,
                reference_date=requested_date,
            )
            name_score = self._fixture_name_score(result, home_team, away_team)
            LOGGER.info(
                "SofaScore game candidate: query=%r event=%s date=%s name_score=%.2f text=%r",
                query,
                result.entity_id or "unknown",
                candidate_date.isoformat() if candidate_date else "unknown",
                name_score,
                result.text,
            )

            if requested_date is not None:
                if candidate_date == requested_date:
                    LOGGER.info(
                        "SofaScore game date match found: query=%r event=%s date=%s text=%r",
                        query,
                        result.entity_id or "unknown",
                        requested_date.isoformat(),
                        result.text,
                    )
                    # The requested date is the source of truth. Differences in
                    # SofaScore's displayed team names do not block a date match.
                    return result

                LOGGER.info(
                    "First SofaScore result did not match requested date %s for query %r; trying next variant",
                    requested_date.isoformat(),
                    query,
                )
                continue

            # Backwards-compatible path when no date is supplied: accept only
            # the first result when it actually resembles the requested fixture.
            if name_score >= 0.55:
                return result

            LOGGER.info(
                "First SofaScore result did not sufficiently match %s - %s for query %r; trying next variant",
                home_team,
                away_team,
                query,
            )

        if requested_date is not None:
            LOGGER.warning(
                "No SofaScore game found for %s - %s on %s after %d search variants",
                home_team,
                away_team,
                requested_date.isoformat(),
                len(queries),
            )
        return fallback

    @classmethod
    def _build_game_search_queries(cls, home_team: str, away_team: str) -> list[str]:
        home_variants = cls._team_search_variants(home_team)
        away_variants = cls._team_search_variants(away_team)

        queries: list[str] = []

        def add(home: str, away: str) -> None:
            query = " ".join(
                part for part in (home.strip(), away.strip()) if part
            ).strip()
            if query and query not in queries:
                queries.append(query)

        # Preserve the exact fixture names as the first query.
        add(str(home_team), str(away_team))

        # Normalized / suffix-stripped forms come next.
        for index in range(min(3, max(len(home_variants), len(away_variants)))):
            if not home_variants or not away_variants:
                break
            add(
                home_variants[min(index, len(home_variants) - 1)],
                away_variants[min(index, len(away_variants) - 1)],
            )

        # Pair meaningful single-token variants. This is what turns cases such
        # as ``Olympic Charleroi`` + ``Habay-la-Neuve`` into ``Charleroi Habay``
        # without hardcoding either team name.
        short_home = cls._meaningful_team_tokens(home_team)
        short_away = cls._meaningful_team_tokens(away_team)
        for home in short_home:
            for away in short_away:
                add(home, away)
                if len(queries) >= 10:
                    return queries

        # Relax one side at a time for asymmetric naming changes.
        for home in home_variants[1:4]:
            add(home, away_variants[0] if away_variants else away_team)
            if len(queries) >= 10:
                return queries
        for away in away_variants[1:4]:
            add(home_variants[0] if home_variants else home_team, away)
            if len(queries) >= 10:
                return queries

        return queries

    @classmethod
    def _meaningful_team_tokens(cls, value: str) -> list[str]:
        normalized = cls.client_normalize_team_name(value)
        tokens = [
            token
            for token in normalized.split()
            if len(token) >= 4
            and token not in cls._GENERIC_TEAM_SUFFIXES
            and token not in cls._RESERVE_MARKERS
        ]
        # Prefer longer tokens, preserving source order for ties.
        return sorted(tokens, key=len, reverse=True)[:3]

    @classmethod
    def _team_search_variants(cls, value: str) -> list[str]:
        normalized = cls.client_normalize_team_name(value)
        if not normalized:
            return []

        tokens = normalized.split()
        suffix_stripped = [
            token for token in tokens if token not in cls._GENERIC_TEAM_SUFFIXES
        ]
        base = " ".join(suffix_stripped).strip() or normalized
        base_tokens = base.split()

        variants: list[str] = []

        def add(candidate_tokens: list[str] | str) -> None:
            candidate = (
                " ".join(candidate_tokens)
                if isinstance(candidate_tokens, list)
                else candidate_tokens
            ).strip()
            if not candidate or candidate in variants:
                return
            if not any(len(token) >= 4 for token in candidate.split()):
                return
            variants.append(candidate)

        add(normalized)
        if base != normalized:
            add(base)

        # Treat reserve markers as a family rather than maintaining aliases.
        if any(token in cls._RESERVE_MARKERS for token in tokens):
            reserve_base = " ".join(
                token for token in tokens if token not in cls._RESERVE_MARKERS
            ).strip()
            if reserve_base:
                add(reserve_base)
                add(f"{reserve_base} B")
                add(f"{reserve_base} II")
                add(f"{reserve_base} 2")

        if len(base_tokens) > 1:
            # Prefer dropping leading descriptor tokens (Olympic -> Charleroi,
            # AD -> Ceuta) without blindly chopping characters.
            for start in range(1, len(base_tokens)):
                candidate = base_tokens[start:]
                if any(len(token) >= 4 for token in candidate):
                    add(candidate)

            # Then retain meaningful individual tokens as a last resort.
            for token in base_tokens:
                if len(token) >= 4:
                    add(token)

        return variants[:6]

    @staticmethod
    def client_normalize_team_name(value: Any) -> str:
        if not value:
            return ""
        text = unicodedata.normalize("NFKD", str(value))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.lower().replace("&", " and ")
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    async def _game_result_date(
        self,
        result: SofascoreSearchResult,
        *,
        reference_date: date | None = None,
    ) -> date | None:
        event_id = result.entity_id or self.client._extract_event_id(result.href or "")
        if not event_id:
            return self._extract_date_from_text(
                f"{result.text} {result.href or ''}",
                reference_date=reference_date,
            )

        if event_id not in self._game_details_cache:
            try:
                details = await self.client.fetch("event", event_id=event_id)
            except Exception as exc:
                LOGGER.warning(
                    "Could not inspect SofaScore event date for event %s: %s",
                    event_id,
                    exc,
                )
                details = None
            self._game_details_cache[event_id] = (
                details if isinstance(details, dict) else None
            )

        details = self._game_details_cache.get(event_id)
        if isinstance(details, dict):
            timestamp = details.get("startTimestamp")
            try:
                if timestamp is not None:
                    return datetime.fromtimestamp(
                        int(timestamp), tz=timezone.utc
                    ).date()
            except (TypeError, ValueError, OverflowError):
                pass

            for key in ("startTime", "startDate", "date"):
                parsed = self._extract_date_from_text(details.get(key))
                if parsed is not None:
                    return parsed

        return self._extract_date_from_text(
            f"{result.text} {result.href or ''}",
            reference_date=reference_date,
        )

    @staticmethod
    def _extract_date_from_text(
        value: Any,
        *,
        reference_date: date | None = None,
    ) -> date | None:
        if value is None:
            return None
        text = str(value)
        lower = text.lower()
        today = datetime.now().astimezone().date()
        base_date = reference_date or today

        # SofaScore often renders near-term matches as relative labels instead
        # of an explicit calendar date. Resolve those labels from the actual
        # current date, not from the requested fixture date.
        if re.search(r"\btomorrow\b", lower):
            return today + timedelta(days=1)
        if re.search(r"\btoday\b", lower):
            return today
        if re.search(r"\byesterday\b", lower):
            return today - timedelta(days=1)

        for pattern in (
            r"(?<!\d)(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])(?!\d)",
            r"(?<!\d)(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})(?!\d)",
        ):
            match = re.search(pattern, text)
            if not match:
                continue
            groups = match.groups()
            if len(groups[0]) == 4:
                year, month, day = map(int, groups)
            else:
                day, month, year = map(int, groups)
            try:
                return date(year, month, day)
            except ValueError:
                continue

        # SofaScore also uses compact dates such as ``10/4``. Prefer the
        # interpretation matching the requested date, then the nearest valid
        # interpretation around the current/reference date.
        partial = re.search(
            r"(?<!\d)(0?[1-9]|[12]\d|3[01])[/.-](0?[1-9]|1[0-2])(?!\d)", text
        )
        if partial:
            first, second = map(int, partial.groups())
            candidates: list[date] = []
            year_candidates = (base_date.year - 1, base_date.year, base_date.year + 1)
            for year in year_candidates:
                for month, day in ((first, second), (second, first)):
                    try:
                        candidates.append(date(year, month, day))
                    except ValueError:
                        continue

            if candidates:
                if reference_date is not None:
                    exact = [
                        candidate
                        for candidate in candidates
                        if candidate == reference_date
                    ]
                    if exact:
                        return exact[0]
                return min(
                    candidates,
                    key=lambda candidate: abs((candidate - base_date).days),
                )

        return None

    @classmethod
    def _fixture_name_score(
        cls,
        result: SofascoreSearchResult,
        home_team: str,
        away_team: str,
    ) -> float:
        text = cls.client_normalize_team_name(result.text)
        home = cls.client_normalize_team_name(home_team)
        away = cls.client_normalize_team_name(away_team)
        return (
            cls._text_team_score(home, text) + cls._text_team_score(away, text)
        ) / 2.0

    @classmethod
    def _select_date_match(
        cls,
        candidates: list[tuple[SofascoreSearchResult, date | None]],
        home_team: str,
        away_team: str,
    ) -> SofascoreSearchResult:
        if len(candidates) == 1:
            return candidates[0][0]
        return (
            cls._select_best_team_match(
                [result for result, _ in candidates],
                home_team,
                away_team,
            )
            or candidates[0][0]
        )

    @staticmethod
    def _text_team_score(requested: str, normalized_text: str) -> float:
        """Score whether a normalized team name appears in match-link text."""
        if requested in normalized_text:
            return 1.0

        tokens = set(requested.split())
        text_tokens = set(normalized_text.split())
        if not tokens or not text_tokens:
            return 0.0

        overlap = len(tokens & text_tokens) / len(tokens)
        sequence = SequenceMatcher(None, requested, normalized_text).ratio()
        return max(0.0, min(1.0, 0.7 * overlap + 0.3 * sequence))

    @classmethod
    def _select_best_team_match(
        cls,
        results: list[SofascoreSearchResult],
        home_team: str,
        away_team: str,
    ) -> SofascoreSearchResult | None:
        best: SofascoreSearchResult | None = None
        best_score = -1.0
        requested_home = cls.client_normalize_team_name(home_team)
        requested_away = cls.client_normalize_team_name(away_team)
        for result in results:
            text = cls.client_normalize_team_name(result.text)
            home_score = cls._text_team_score(requested_home, text)
            away_score = cls._text_team_score(requested_away, text)
            score = (home_score + away_score) / 2.0
            if score > best_score:
                best = result
                best_score = score
        return best

    async def open_result(self, result: SofascoreSearchResult) -> Any:
        """Navigate to a search result without clicking through the modal."""
        if not result.href:
            raise ValueError("SofaScore search result has no href")

        href = result.href
        url = f"{SOFASCORE_BASE_URL}{href}" if href.startswith("/") else href
        await self._sleep_random(
            "Before opening SofaScore search result",
            self.navigation_delay_min,
            self.navigation_delay_max,
        )

        async with self.client._browser_lock:
            response = await self.client.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.client.navigation_timeout_ms,
            )

        status = response.status if response is not None else None
        if status is None:
            raise RuntimeError(f"No response while opening SofaScore result: {url}")
        if status >= 400:
            raise SofascoreHTTPError(status, url)
        return response

    async def _ensure_search_page(self) -> None:
        current = self.client.page.url
        if current.startswith(SOFASCORE_FOOTBALL_URL):
            return

        await self._sleep_random("Before returning to SofaScore search page", 1.0, 2.0)
        response = await self.client.page.goto(
            SOFASCORE_FOOTBALL_URL,
            wait_until="domcontentloaded",
            timeout=self.client.navigation_timeout_ms,
        )
        status = response.status if response is not None else None
        if status is None or status >= 400:
            raise SofascoreHTTPError(status or 0, SOFASCORE_FOOTBALL_URL)
        await self._dismiss_blocking_overlays()
        await self._sleep_random("Waiting for SofaScore search surface", 2.0, 4.0)

    async def _open_search(self) -> None:
        # If a visible search modal already exists, reuse it instead of
        # clicking the search control twice.
        if await self._visible_modal() is not None:
            return

        await self._dismiss_blocking_overlays()
        if await self._click_search_button():
            return

        # Cookie/robot prompts can appear only once the page notices user
        # interaction, so check again before giving up.
        if await self._dismiss_blocking_overlays() and await self._click_search_button():
            return

        raise RuntimeError("Could not open SofaScore search")

    async def _click_search_button(self) -> bool:
        selectors = [
            self.client.page.get_by_role(
                "button", name=re.compile(r"search", re.IGNORECASE)
            ),
            self.client.page.get_by_label(re.compile(r"search", re.IGNORECASE)),
            self.client.page.locator('button[aria-label*="Search" i]'),
            self.client.page.locator('[role="button"][aria-label*="Search" i]'),
        ]

        for locator in selectors:
            try:
                count = await locator.count()
            except Exception:
                continue

            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    await self._sleep_random(
                        "Before opening SofaScore search",
                        self.open_delay_min,
                        self.open_delay_max,
                    )
                    await candidate.click(timeout=10_000)
                    await self._sleep_random(
                        "Waiting for SofaScore search",
                        self.post_open_delay_min,
                        self.post_open_delay_max,
                    )
                    if await self._visible_modal() is not None:
                        return True
                except Exception:
                    continue

        return False

    async def _dismiss_blocking_overlays(self) -> bool:
        """Best-effort dismissal of cookie banners and bot-check prompts."""
        cookie_dismissed = await self._dismiss_cookie_consent()
        robot_dismissed = await self._dismiss_robot_check()
        return cookie_dismissed or robot_dismissed

    async def _dismiss_cookie_consent(self) -> bool:
        # The consent dialog is sometimes rendered in the main document and
        # sometimes inside a third-party CMP iframe, so check every frame.
        for frame in self.client.page.frames:
            try:
                if await self._click_consent_button(frame):
                    return True
            except Exception:
                continue
        return False

    async def _click_consent_button(self, frame) -> bool:
        for selector in self._COOKIE_CONSENT_SELECTORS:
            locator = frame.locator(selector)
            try:
                if await locator.count() == 0 or not await locator.first.is_visible():
                    continue
                await locator.first.click(timeout=5_000)
                LOGGER.info("Dismissed SofaScore cookie consent via %r", selector)
                await self._sleep_random(
                    "After dismissing SofaScore cookie consent", 0.5, 1.5
                )
                return True
            except Exception:
                continue

        try:
            text_locator = frame.get_by_role(
                "button", name=self._COOKIE_CONSENT_TEXT_PATTERN
            )
            count = await text_locator.count()
        except Exception:
            count = 0
        for index in range(count):
            candidate = text_locator.nth(index)
            try:
                if not await candidate.is_visible():
                    continue
                await candidate.click(timeout=5_000)
                LOGGER.info("Dismissed SofaScore cookie consent via text match")
                await self._sleep_random(
                    "After dismissing SofaScore cookie consent", 0.5, 1.5
                )
                return True
            except Exception:
                continue

        return False

    async def _dismiss_robot_check(self) -> bool:
        frame_locator = self.client.page.frame_locator(
            self._ROBOT_CHECK_FRAME_SELECTOR
        )
        checkbox = frame_locator.locator('#recaptcha-anchor, [role="checkbox"]')
        try:
            if await checkbox.count() == 0 or not await checkbox.first.is_visible():
                return False
            await checkbox.first.click(timeout=5_000)
            LOGGER.info("Clicked SofaScore 'I'm not a robot' checkbox")
            await self._sleep_random("After SofaScore robot check", 2.0, 4.0)
            return True
        except Exception:
            return False

    async def _get_search_input(self):
        candidates = [
            self.client.page.locator('input[placeholder*="Search" i]'),
            self.client.page.locator('input[aria-label*="Search" i]'),
            self.client.page.locator('input[type="search"]'),
            self.client.page.get_by_role("textbox"),
        ]
        for locator in candidates:
            try:
                count = await locator.count()
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
        return None

    async def _visible_modal(self):
        modal = self.client.page.locator('[data-testid="modal"]')
        try:
            count = await modal.count()
        except Exception:
            return None
        for index in range(count - 1, -1, -1):
            candidate = modal.nth(index)
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    async def _read_modal_results(self) -> list[SofascoreSearchResult]:
        modal = await self._visible_modal()
        if modal is None:
            LOGGER.warning("SofaScore search modal not found")
            return []

        links = modal.locator("a[href]")
        try:
            count = await links.count()
        except Exception:
            return []

        results: list[SofascoreSearchResult] = []
        seen: set[tuple[str, str, str]] = set()
        for index in range(count):
            link = links.nth(index)
            try:
                href = await link.get_attribute("href")
                if not href or not href.startswith(("/", SOFASCORE_BASE_URL)):
                    continue
                text = " ".join((await link.inner_text()).split())
                if not text:
                    text = (
                        await link.get_attribute("aria-label")
                        or await link.get_attribute("title")
                        or ""
                    )
                data_id = await link.get_attribute("data-id")
                kind = self._classify_href(href)
                if kind == "unknown":
                    continue
                entity_id = data_id or self._extract_entity_id(href, kind)
                key = (kind, href, data_id or "")
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    SofascoreSearchResult(
                        kind=kind,
                        text=text,
                        href=href,
                        data_id=data_id,
                        entity_id=entity_id,
                    )
                )
            except Exception:
                continue

        search_value = " ".join((await self._get_search_value()).split())
        LOGGER.info(
            "SofaScore search returned %d typed results for %r",
            len(results),
            search_value,
        )
        return results

    async def _get_search_value(self) -> str:
        search_input = await self._get_search_input()
        if search_input is None:
            return ""
        try:
            return await search_input.input_value()
        except Exception:
            return ""

    @classmethod
    def _classify_href(cls, href: str) -> str:
        path = href.lower()
        for kind, patterns in cls._KIND_PATTERNS.items():
            if any(pattern in path for pattern in patterns):
                return kind
        return "unknown"

    @staticmethod
    def _extract_entity_id(href: str, kind: str) -> str | None:
        if kind == "game":
            match = re.search(r"#id:(\d+)", href)
            if match:
                return match.group(1)
            match = re.search(r"/event/(\d+)", href)
            return match.group(1) if match else None

        path = urlparse(href).path.rstrip("/")
        matches = re.findall(r"/(\d+)(?:/|$)", path)
        if matches:
            return matches[-1]
        return None

    async def _wait_for_search_slot(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._last_search_at is not None:
            elapsed = now - self._last_search_at
            target = self.search_interval_min + random.uniform(
                0.0, self.search_interval_jitter
            )
            sleep_for = max(0.0, target - elapsed)
            if sleep_for > 0:
                LOGGER.debug(
                    "Sleeping %.2fs before next SofaScore search",
                    sleep_for,
                )
                await asyncio.sleep(sleep_for)
        self._last_search_at = asyncio.get_running_loop().time()

    async def _sleep_random(self, label: str, minimum: float, maximum: float) -> None:
        delay = random.uniform(minimum, maximum)
        LOGGER.debug("%s: %.2fs", label, delay)
        await asyncio.sleep(delay)


RESOURCE_SPECS: dict[str, ResourceSpec] = {
    # Daily / sport-level resources.
    "scheduled_events": ResourceSpec(
        "scheduled_events",
        "/sport/{sport}/scheduled-events/{date}",
    ),
    "scheduled_events_inverse": ResourceSpec(
        "scheduled_events_inverse",
        "/sport/{sport}/scheduled-events/{date}/inverse",
    ),
    # Match / event resources.
    "event": ResourceSpec("event", "/event/{event_id}"),
    "team_streaks": ResourceSpec(
        "team_streaks",
        "/event/{event_id}/team-streaks",
    ),
    "statistics": ResourceSpec(
        "statistics",
        "/event/{event_id}/statistics",
    ),
    "incidents": ResourceSpec(
        "incidents",
        "/event/{event_id}/incidents",
    ),
    "lineups": ResourceSpec(
        "lineups",
        "/event/{event_id}/lineups",
    ),
    "pregame_form": ResourceSpec(
        "pregame_form",
        "/event/{event_id}/pregame-form",
    ),
    "h2h_events": ResourceSpec(
        "h2h_events",
        "/event/{event_id}/h2h/events",
    ),
    "managers": ResourceSpec(
        "managers",
        "/event/{event_id}/managers",
    ),
    "graph": ResourceSpec("graph", "/event/{event_id}/graph"),
    # Team resources.
    "team": ResourceSpec("team", "/team/{team_id}"),
    "team_players": ResourceSpec(
        "team_players",
        "/team/{team_id}/players",
    ),
    "team_performance": ResourceSpec(
        "team_performance",
        "/team/{team_id}/performance",
    ),
    "team_events_next": ResourceSpec(
        "team_events_next",
        "/team/{team_id}/events/next",
    ),
    "team_events_last": ResourceSpec(
        "team_events_last",
        "/team/{team_id}/events/last",
    ),
    # Referee resources.
    "referee": ResourceSpec("referee", "/referee/{referee_id}"),
    "referee_statistics": ResourceSpec(
        "referee_statistics",
        "/referee/{referee_id}/statistics",
    ),
    "referee_events": ResourceSpec(
        "referee_events",
        "/referee/{referee_id}/events",
    ),
}

# Friendly aliases. These are intentionally mapped to the canonical specs so
# adding a new resource only requires one ResourceSpec entry above.
RESOURCE_ALIASES: dict[str, str] = {
    "fixtures_by_date": "scheduled_events",
    "team_records": "team_performance",
    "referee_stats": "referee_statistics",
    "match_referee": "event",
}


class SofascoreHTTPError(RuntimeError):
    """Raised when a SofaScore browser-context API request is unsuccessful."""

    def __init__(self, status_code: int, path: str, body: Any = None) -> None:
        self.status_code = status_code
        self.path = path
        self.body = body
        super().__init__(f"SofaScore request failed ({status_code}): {path}")


class SofascoreClient:
    """Small browser-backed SofaScore client with a generic resource registry.

    The client deliberately keeps transport concerns separate from resource paths.
    New resources can be added to RESOURCE_SPECS without changing request logic.
    """

    def __init__(
        self,
        *,
        request_interval: float = 2.0,
        request_jitter: float = 1.5,
        request_burst_size: int = 5,
        request_burst_pause: float = 12.0,
        request_backoff_base: float = 3.0,
        request_max_retries: int = 4,
        browser_channel: str = "chrome",
        headless: bool = False,
        startup_delay_min: float = 3.0,
        startup_delay_max: float = 5.0,
        navigation_timeout_ms: int = 60_000,
    ) -> None:
        self.request_interval = max(0.0, request_interval)
        self.request_jitter = max(0.0, request_jitter)
        self.request_burst_size = max(1, request_burst_size)
        self.request_burst_pause = max(0.0, request_burst_pause)
        self.request_backoff_base = max(0.1, request_backoff_base)
        self.request_max_retries = max(0, request_max_retries)
        self.browser_channel = browser_channel
        self.headless = headless
        self.startup_delay_min = max(0.0, startup_delay_min)
        self.startup_delay_max = max(self.startup_delay_min, startup_delay_max)
        self.navigation_timeout_ms = navigation_timeout_ms

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_request_at: float | None = None
        self._request_count = 0
        self._browser_lock = asyncio.Lock()
        self._match_index_loaded = False
        self._match_index: list[dict[str, Any]] = []
        self.searcher = SofascoreSearcher(self)

    async def __aenter__(self) -> "SofascoreClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("SofascoreClient is not started")
        return self._page

    async def start(self) -> None:
        """Launch a real browser and warm up the SofaScore session."""
        if self._page is not None:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel=self.browser_channel,
            headless=self.headless,
        )
        self._context = await self._browser.new_context(locale="en-US")
        self._page = await self._context.new_page()

        LOGGER.info("Opening SofaScore warm-up page")
        response = await self.page.goto(
            SOFASCORE_FOOTBALL_URL,
            wait_until="domcontentloaded",
            timeout=self.navigation_timeout_ms,
        )
        if response is None or response.status >= 400:
            status = response.status if response else "NO RESPONSE"
            raise RuntimeError(f"SofaScore warm-up failed: {status}")

        await asyncio.sleep(
            random.uniform(self.startup_delay_min, self.startup_delay_max)
        )

    async def close(self) -> None:
        """Close browser resources in reverse order."""
        self._page = None
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

        self._context = None
        self._browser = None
        self._playwright = None
        self._last_request_at = None
        self._request_count = 0
        self._match_index_loaded = False
        self._match_index.clear()

    @staticmethod
    def normalize_resource_name(resource: str) -> str:
        canonical = RESOURCE_ALIASES.get(resource, resource)
        if canonical not in RESOURCE_SPECS:
            available = ", ".join(sorted(RESOURCE_SPECS))
            raise KeyError(
                f"Unknown SofaScore resource '{resource}'. Available: {available}"
            )
        return canonical

    @classmethod
    def build_resource_path(cls, resource: str, **params: Any) -> str:
        canonical = cls.normalize_resource_name(resource)
        spec = RESOURCE_SPECS[canonical]
        try:
            resource_path = spec.path.format(**params)
        except KeyError as exc:
            missing = str(exc.args[0])
            raise ValueError(
                f"Missing parameter '{missing}' for SofaScore resource '{resource}'"
            ) from exc
        return f"{SOFASCORE_API_PREFIX}{resource_path}"

    async def fetch(self, resource: str, **params: Any) -> Any:
        """Fetch one registered resource by name."""
        path = self.build_resource_path(resource, **params)
        return await self.fetch_path(path)

    async def fetch_path(self, path: str) -> Any:
        """Fetch an arbitrary relative SofaScore API path using the warm browser."""
        if not path.startswith("/"):
            path = f"/{path}"
        if not path.startswith(SOFASCORE_API_PREFIX + "/"):
            raise ValueError(f"Only SofaScore API v1 paths are supported: {path}")

        for attempt in range(self.request_max_retries + 1):
            self._request_count += 1
            await self._wait_for_slot()

            try:
                status_code, body = await self._browser_fetch(path)
                self._last_request_at = asyncio.get_running_loop().time()

                if status_code in {403, 429}:
                    if attempt >= self.request_max_retries:
                        raise SofascoreHTTPError(status_code, path, body)
                    await self._backoff(status_code, path, attempt)
                    continue

                if status_code < 200 or status_code >= 300:
                    raise SofascoreHTTPError(status_code, path, body)

                return body
            except SofascoreHTTPError:
                raise
            except Exception:
                self._last_request_at = asyncio.get_running_loop().time()
                raise

        raise RuntimeError(f"Request loop unexpectedly exhausted for {path}")

    async def _browser_fetch(self, path: str) -> tuple[int, Any]:
        result = await self.page.evaluate(
            """
            async (requestPath) => {
                const response = await fetch(requestPath, {
                    credentials: "include",
                    headers: {
                        "Accept": "application/json, text/plain, */*"
                    }
                });
                const text = await response.text();
                let body = text;
                try {
                    body = JSON.parse(text);
                } catch (_) {
                    // Preserve non-JSON bodies so the caller can diagnose 403/429 pages.
                }
                return {
                    status: response.status,
                    body,
                };
            }
            """,
            path,
        )
        return int(result["status"]), result.get("body")

    async def _wait_for_slot(self) -> None:
        if self._last_request_at is None:
            return

        loop = asyncio.get_running_loop()
        elapsed = loop.time() - self._last_request_at
        target_delay = self.request_interval + random.uniform(0.0, self.request_jitter)

        if (
            self._request_count > 0
            and self._request_count % self.request_burst_size == 0
        ):
            target_delay += random.uniform(
                self.request_burst_pause * 0.7,
                self.request_burst_pause * 1.5,
            )

        sleep_for = max(0.0, target_delay - elapsed)
        if sleep_for > 0:
            LOGGER.debug(
                "Sleeping %.2fs before SofaScore request (elapsed=%.2fs, target=%.2fs)",
                sleep_for,
                elapsed,
                target_delay,
            )
            await asyncio.sleep(sleep_for)

    async def _backoff(self, status_code: int, path: str, attempt: int) -> None:
        base = self.request_backoff_base * (2**attempt)
        jitter = random.uniform(0.0, max(self.request_jitter, 0.5))
        delay = base + jitter
        LOGGER.warning(
            "SofaScore returned %s for %s; retrying in %.2fs (attempt %d/%d)",
            status_code,
            path,
            delay,
            attempt + 1,
            self.request_max_retries,
        )
        await asyncio.sleep(delay)

    async def load_match_index(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Index the currently visible SofaScore football match links once.

        The index is intentionally independent from the date-based SofaScore
        schedule endpoints. Callers can resolve API-Football fixture names to
        SofaScore event IDs using only the already loaded football page.
        """
        if self._match_index_loaded and not force:
            return self._match_index

        links = self.page.locator('a[href*="/football/match/"]')
        count = await links.count()
        index: list[dict[str, Any]] = []
        seen_event_ids: set[str] = set()

        for position in range(count):
            link = links.nth(position)
            try:
                href = await link.get_attribute("href") or ""
                event_id = self._extract_event_id(href)
                if not event_id or event_id in seen_event_ids:
                    continue

                text = (await link.inner_text()).strip()
                normalized_text = self._normalize_team_name(f"{text} {href}")
                entry = {
                    "event_id": event_id,
                    "href": href,
                    "text": text,
                    "normalized_text": normalized_text,
                }
                index.append(entry)
                seen_event_ids.add(event_id)
            except Exception:
                continue

        self._match_index = index
        self._match_index_loaded = True
        LOGGER.info("Indexed %d SofaScore match links", len(index))
        return self._match_index

    async def resolve_event_by_match_name(
        self,
        home_team: str,
        away_team: str,
        *,
        target_date: str | date | None = None,
        force_reindex: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve fixture names through progressive SofaScore global search.

        ``target_date`` is optional for backwards compatibility. When supplied,
        it is the source of truth: only a SofaScore event whose actual event
        date matches it is accepted. When omitted, the existing name-based
        behaviour is preserved.
        """
        del force_reindex

        result = await self.searcher.find_game(
            home_team,
            away_team,
            target_date=target_date,
        )
        if result is None:
            return None

        event_id = result.entity_id or self._extract_event_id(result.href or "")
        if not event_id:
            return None

        return {
            "event_id": event_id,
            "href": result.href,
            "text": result.text,
            "data_id": result.data_id,
            "kind": result.kind,
            "match_date": (
                await self.searcher._game_result_date(result)
                if target_date is not None
                else None
            ),
        }

    async def fetch_match_resource_by_name(
        self,
        home_team: str,
        away_team: str,
        resource: str,
    ) -> Any | None:
        """Resolve a match by team names and fetch any registered event resource."""
        event = await self.resolve_event_by_match_name(home_team, away_team)
        if event is None:
            return None
        return await self.fetch(resource, event_id=event["event_id"])

    @classmethod
    def _text_team_score(cls, requested: str, normalized_text: str) -> float:
        """Score whether a normalized team name appears in match-link text."""
        if requested in normalized_text:
            return 1.0

        tokens = set(requested.split())
        text_tokens = set(normalized_text.split())
        if not tokens or not text_tokens:
            return 0.0

        overlap = len(tokens & text_tokens) / len(tokens)
        sequence = SequenceMatcher(None, requested, normalized_text).ratio()
        return max(0.0, min(1.0, 0.7 * overlap + 0.3 * sequence))

    @staticmethod
    def _extract_event_id(href: str) -> str | None:
        match = re.search(r"#id:(\d+)", href)
        if match:
            return match.group(1)

        match = re.search(r"/event/(\d+)", href)
        return match.group(1) if match else None

    async def fetch_scheduled_events(
        self,
        target_date: str | date,
        *,
        sport: str = "football",
        inverse: bool = False,
    ) -> dict[str, Any]:
        date_value = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else str(target_date)
        )
        resource = "scheduled_events_inverse" if inverse else "scheduled_events"
        payload = await self.fetch(resource, sport=sport, date=date_value)
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected scheduled-events response for {date_value}")
        return payload

    async def resolve_fixture_event(
        self,
        home_team: str,
        away_team: str,
        target_date: str | date,
        *,
        sport: str = "football",
        inverse: bool = False,
    ) -> dict[str, Any] | None:
        """Resolve an API-Football fixture to a SofaScore event object."""
        payload = await self.fetch_scheduled_events(
            target_date,
            sport=sport,
            inverse=inverse,
        )
        events = payload.get("events", [])
        if not isinstance(events, list):
            return None

        return self.select_scheduled_event(home_team, away_team, events)

    @classmethod
    def select_scheduled_event(
        cls,
        home_team: str,
        away_team: str,
        events: list[Any],
    ) -> dict[str, Any] | None:
        """Choose the best event from an already-fetched schedule response."""
        best_event: dict[str, Any] | None = None
        best_score = 0.0

        for event in events:
            if not isinstance(event, dict):
                continue
            home = event.get("homeTeam")
            away = event.get("awayTeam")
            if not isinstance(home, dict) or not isinstance(away, dict):
                continue

            home_score = cls._team_name_score(home_team, home)
            away_score = cls._team_name_score(away_team, away)
            score = (home_score + away_score) / 2.0

            if home_score >= 0.70 and away_score >= 0.70 and score > best_score:
                best_score = score
                best_event = event

        return best_event

    async def fetch_fixture_resource(
        self,
        home_team: str,
        away_team: str,
        target_date: str | date,
        resource: str,
        *,
        sport: str = "football",
        inverse: bool = False,
    ) -> dict[str, Any] | Any | None:
        """Resolve a fixture and fetch any registered event resource."""
        event = await self.resolve_fixture_event(
            home_team,
            away_team,
            target_date,
            sport=sport,
            inverse=inverse,
        )
        if event is None:
            return None

        event_id = event.get("id")
        if event_id is None:
            return None

        return await self.fetch(resource, event_id=event_id)

    async def fetch_event_bundle(
        self,
        event_id: int | str,
        resources: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        """Fetch multiple event resources while reusing one browser session."""
        result: dict[str, Any] = {}
        for resource in resources:
            result[self.normalize_resource_name(resource)] = await self.fetch(
                resource,
                event_id=event_id,
            )
        return result

    async def fetch_match_referee(
        self,
        event_id: int | str,
        *,
        include_statistics: bool = True,
    ) -> dict[str, Any]:
        """Return the referee attached to a match plus optional referee statistics."""
        event = await self.fetch("event", event_id=event_id)
        if not isinstance(event, dict):
            return {"event_id": event_id, "referee": None, "statistics": None}

        referee = event.get("referee")
        if not isinstance(referee, dict) or referee.get("id") is None:
            return {"event_id": event_id, "referee": None, "statistics": None}

        referee_id = referee["id"]
        details = await self.fetch("referee", referee_id=referee_id)
        statistics = None
        if include_statistics:
            try:
                statistics = await self.fetch(
                    "referee_statistics",
                    referee_id=referee_id,
                )
            except SofascoreHTTPError as exc:
                # Keep the basic referee data useful even if the separate stats
                # resource is temporarily unavailable.
                LOGGER.warning(
                    "Could not fetch referee statistics for %s (status=%s)",
                    referee_id,
                    exc.status_code,
                )

        return {
            "event_id": event_id,
            "referee": details,
            "statistics": statistics,
        }

    async def fetch_team_records(self, team_id: int | str) -> Any:
        """Return SofaScore's team performance/form resource."""
        return await self.fetch("team_performance", team_id=team_id)

    async def fetch_team_streaks(
        self,
        event_id: int | str,
        *,
        home_team: str | None = None,
        away_team: str | None = None,
    ) -> Any:
        """Return event team streaks, falling back to the match-page H2H flow."""
        try:
            return await self.fetch("team_streaks", event_id=event_id)
        except SofascoreHTTPError as exc:
            if exc.status_code not in {403, 429}:
                raise
            LOGGER.warning(
                "Direct team-streaks request failed with %s; trying the match-page flow",
                exc.status_code,
            )
            return await self._fetch_team_streaks_from_match_page(
                event_id,
                home_team=home_team,
                away_team=away_team,
            )

    async def _fetch_team_streaks_from_match_page(
        self,
        event_id: int | str,
        *,
        home_team: str | None,
        away_team: str | None,
    ) -> Any:
        """Capture /team-streaks from the match page, matching the working example."""
        target_path = self.build_resource_path("team_streaks", event_id=event_id)
        captured: dict[str, Any] = {}
        captured_event = asyncio.Event()

        async def handle_response(response) -> None:
            if urlparse(response.url).path != target_path:
                return
            if response.status != 200:
                return
            try:
                captured["data"] = await response.json()
            except Exception:
                return
            captured_event.set()

        self.page.on("response", handle_response)
        try:
            await self.page.goto(
                SOFASCORE_FOOTBALL_URL,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            await asyncio.sleep(
                random.uniform(self.startup_delay_min, self.startup_delay_max)
            )

            link = await self._find_match_link(
                event_id,
                home_team=home_team,
                away_team=away_team,
            )
            if link is None:
                raise RuntimeError(
                    f"SofaScore match link not found for event {event_id}"
                )

            await asyncio.sleep(random.uniform(2.0, 4.0))
            await link.click()

            try:
                await asyncio.wait_for(captured_event.wait(), timeout=8.0)
            except asyncio.TimeoutError:
                pass

            if "data" not in captured:
                h2h = self.page.get_by_text("H2H", exact=True)
                if await h2h.count() == 0:
                    h2h = self.page.get_by_text("Head to head", exact=True)
                if await h2h.count() > 0:
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                    await h2h.first.click()
                    try:
                        await asyncio.wait_for(captured_event.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass

            if "data" not in captured:
                raise RuntimeError(
                    f"No /team-streaks response captured for SofaScore event {event_id}"
                )
            return captured["data"]
        finally:
            self.page.remove_listener("response", handle_response)

    async def _find_match_link(
        self,
        event_id: int | str,
        *,
        home_team: str | None,
        away_team: str | None,
    ):
        event_marker = str(event_id)
        direct = self.page.locator(f'a[href*="#id:{event_marker}"]')
        if await direct.count() > 0:
            return direct.first

        await self.load_match_index()
        for entry in self._match_index:
            if entry.get("event_id") == event_marker:
                href = entry.get("href")
                if href:
                    locator = self.page.locator(f'a[href="{href}"]')
                    if await locator.count() > 0:
                        return locator.first

        if home_team and away_team:
            try:
                results = await self.searcher.games(f"{home_team} {away_team}")
            except Exception:
                results = []
            for result in results:
                result_event_id = result.entity_id or self._extract_event_id(
                    result.href or ""
                )
                if result_event_id != event_marker or not result.href:
                    continue

                href = result.href
                locator = self.page.locator(f'a[href="{href}"]')
                if await locator.count() > 0:
                    return locator.first

                # The searcher may have been called while on a different page.
                # Navigate directly to the exact result when the fallback needs it.
                return None

        return None

    @staticmethod
    def _coerce_date(value: str | date | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"Expected ISO date YYYY-MM-DD, got {value!r}") from exc

    @classmethod
    def _team_name_score(cls, requested: str, candidate: dict[str, Any]) -> float:
        requested_normalized = cls._normalize_team_name(requested)
        if not requested_normalized:
            return 0.0

        variants = {
            cls._normalize_team_name(candidate.get("name")),
            cls._normalize_team_name(candidate.get("shortName")),
            cls._normalize_team_name(candidate.get("slug")),
            cls._normalize_team_name(candidate.get("nameCode")),
        }
        variants.discard("")
        if not variants:
            return 0.0

        best = 0.0
        requested_tokens = set(requested_normalized.split())
        for variant in variants:
            if requested_normalized == variant:
                best = max(best, 1.0)
                continue
            if requested_normalized in variant or variant in requested_normalized:
                best = max(best, 0.92)
                continue

            variant_tokens = set(variant.split())
            union = requested_tokens | variant_tokens
            overlap = (
                len(requested_tokens & variant_tokens) / len(union) if union else 0.0
            )
            sequence = SequenceMatcher(None, requested_normalized, variant).ratio()
            best = max(best, 0.60 * sequence + 0.40 * overlap)

        return best

    @staticmethod
    def _normalize_team_name(value: Any) -> str:
        if not value:
            return ""
        text = unicodedata.normalize("NFKD", str(value))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.lower().replace("&", " and ")
        text = re.sub(r"[^a-z0-9]+", " ", text)
        text = " ".join(text.split())
        return text


__all__ = [
    "RESOURCE_ALIASES",
    "RESOURCE_SPECS",
    "ResourceSpec",
    "SofascoreClient",
    "SofascoreHTTPError",
    "SofascoreSearchResult",
    "SofascoreSearcher",
]
