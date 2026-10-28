---
name: football-top-10
description: Select and present a concise, balanced Top 10 from the football analyser's prediction candidates.
---

# Football Top 10

Select up to 10 of the strongest defensible predictions, using score, prediction strength, confidence, evidence, and football judgment. Never rank by score alone. Exclude any candidate with a conflict noted for it. If fewer than 10 defensible predictions remain, return only those. Never add an eleventh pick or force a weak or conflicted prediction to fill the list.

For a given match, do not select both `both teams scoring` and either team's `no clean sheet` market. BTTS already entails that neither team keeps a clean sheet, so the no-clean-sheet pick adds no new outcome information. This rule applies regardless of which pick would rank higher.

Build a genuinely varied list across matches and market types. Compare all available options, such as goals, both teams to score, team totals, match result or double chance, corners, and cards. Avoid relying on one market type (especially cards) just because its candidates rank highly; use at most three picks from the same market type unless the candidate pool clearly leaves no sound alternatives. Include both Over and Under picks when supported. Look beyond the very top-ranked candidates for credible alternatives, but never force a weak or conflicted pick.

Judge each pick in its match context: use the supplied form, home/away performance, scoring and conceding patterns, head-to-heads, season stats, playing styles, and match circumstances where available. Prefer markets that fit that evidence. Do not invent context or statistics, and do not present speculation as certainty.

Give every pick one unique global rank, numbered consecutively from 1 through the final pick count. Each numbered line is exactly one pick, so the total number of numbered lines must equal the number of ranks and must never exceed 10. Group picks for the same match under one `### Home - Away` heading. Under each heading, put every pick on its own numbered line, with a blank line between game groups. Keep this format consistent:

### NEC Nijmegen - Groningen

1. Less than 4.5 cards

### Barcelona - Real Madrid

2. Over 2.5 goals

Before responding, count the numbered pick lines and verify there are no more than 10 and the ranks are exactly `1, 2, ..., N` with no gaps or duplicates. Output only the ranked Top 10 grouped by game. Keep it concise, with no introduction or conclusion.
