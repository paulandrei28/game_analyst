from __future__ import annotations

from collections import defaultdict
from typing import Any


class HumanReadableReport:
    """Turn analyzer output into a concise, human-readable match report."""

    SECTION_LABELS = {
        "general": "Recent form",
        "head2head": "Head-to-head",
    }

    def render(
        self,
        predictions: list[dict[str, Any]],
        *,
        title: str = "Match Analysis Report",
    ) -> str:
        if not predictions:
            return f"# {title}\n\nNo predictions were generated.\n"

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for prediction in predictions:
            game = f"{prediction['home']} - {prediction['away']}"
            groups[game].append(prediction)

        lines = [
            f"# {title}",
            "",
            f"{len(predictions)} ranked predictions",
            "",
            "## Prediction overview",
            "",
            "| Rank | Match | Prediction | Details |",
            "| ---: | --- | --- | --- |",
        ]
        lines.extend(self._render_summary_row(prediction) for prediction in predictions)
        lines.extend(["", "## Evidence", ""])

        for game, game_predictions in groups.items():
            lines.extend(self._render_game(game, game_predictions))

        return "\n".join(lines).rstrip() + "\n"

    def _render_game(
        self,
        game: str,
        predictions: list[dict[str, Any]],
    ) -> list[str]:
        home = predictions[0]["home"]
        away = predictions[0]["away"]
        lines = [f"## {home} - {away}", ""]

        for prediction in predictions:
            lines.extend(self._render_prediction(prediction))
            lines.append("")

        return lines

    def _render_prediction(self, prediction: dict[str, Any]) -> list[str]:
        rank = prediction.get("rank", "-")
        market = prediction["market"]
        confidence = prediction["confidence"]
        score = prediction["score"]
        value = prediction["prediction"]

        lines = [
            f'<a id="prediction-{rank}"></a>',
            "<details>",
            f"<summary>#{rank} — {market}</summary>",
            "",
            f"### #{rank} — {market}",
            f"**Prediction strength:** {value:.2f}  |  **Score:** {score:.2f}  |  **Confidence:** {confidence:.2f}%",
            "",
            "**Direct evidence**",
        ]

        evidence = prediction.get("evidence", [])
        lines.extend(
            self._render_evidence(
                evidence,
                home=prediction["home"],
                away=prediction["away"],
            )
        )

        supporting = prediction.get("supporting_evidence", [])
        if supporting:
            lines.extend(["", "**Supporting evidence**"] )
            lines.extend(
                self._render_evidence(
                    supporting,
                    home=prediction["home"],
                    away=prediction["away"],
                )
            )

        bonuses = prediction.get("bonuses", [])
        if bonuses:
            lines.extend(["", "**Why the prediction is strengthened**"] )
            lines.extend(f"- {self._clean_bonus(item)}" for item in bonuses)

        penalties = prediction.get("penalties", [])
        if penalties:
            lines.extend(["", "**Conflicting evidence**"] )
            lines.extend(f"- {item}" for item in penalties)

        lines.extend(["", "</details>"])
        return lines

    @staticmethod
    def _render_summary_row(prediction: dict[str, Any]) -> str:
        """Render one scan-friendly row for the report's prediction index."""
        rank = prediction.get("rank", "-")
        game = f"{prediction['home']} - {prediction['away']}"
        market = prediction["market"]
        return (
            f"| {rank} | {game} | {market} | "
            f"[View details](#prediction-{rank}) |"
        )

    def _render_evidence(
        self,
        evidence: list[dict[str, Any]],
        *,
        home: str,
        away: str,
    ) -> list[str]:
        if not evidence:
            return ["- None"]

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence:
            grouped[item.get("section", "unknown")].append(item)

        lines: list[str] = []
        for section in ("general", "head2head"):
            items = grouped.get(section, [])
            if not items:
                continue

            label = self.SECTION_LABELS.get(section, section.replace("_", " ").title())
            lines.append(f"- **{label}:**")
            for item in items:
                team = self._team_label(item.get("team"), home=home, away=away)
                name = item.get("name", "Evidence")
                value = item.get("value", "?")
                lines.append(f"  - {team}{name}: **{value}**")

        # Preserve any future/unknown evidence sections instead of dropping them.
        for section, items in grouped.items():
            if section in {"general", "head2head"}:
                continue
            label = self.SECTION_LABELS.get(section, section.replace("_", " ").title())
            lines.append(f"- **{label}:**")
            for item in items:
                team = self._team_label(item.get("team"), home=home, away=away)
                lines.append(
                    f"  - {team}{item.get('name', 'Evidence')}: **{item.get('value', '?')}**"
                )

        return lines

    @staticmethod
    def _team_label(team: str | None, *, home: str, away: str) -> str:
        if team == "home":
            return f"{home} — "
        if team == "away":
            return f"{away} — "
        if team == "both":
            return "Both — "
        return ""

    @staticmethod
    def _clean_bonus(text: str) -> str:
        # The analyzer already produces useful relationship descriptions;
        # only normalize the arrow glyph for plain-text/Markdown portability.
        return text.replace("→", "→")

    def save(
        self,
        predictions: list[dict[str, Any]],
        output_path: str,
        *,
        title: str = "Match Analysis Report",
    ) -> str:
        report = self.render(predictions, title=title)
        with open(output_path, "w", encoding="utf-8") as report_file:
            report_file.write(report)
        return output_path
