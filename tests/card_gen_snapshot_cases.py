"""Card Gen v2 — Snapshot test cases.

Provides 10 test cases:
  - 8 prediction cards (ported from html_snapshot_cases.py)
  - 2 result cards (WIN + LOSS)

Each case is a dict with:
  - ``id``: unique case identifier
  - ``card_data``: PredictionCardData or ResultCardData instance
"""

from __future__ import annotations

from app.services.card_gen.models import PredictionCardData, ResultCardData, TeamInfo

CASES = [
    # ── Prediction cards (ported from legacy cases) ──────────
    {
        "id": "pred_en_hot_basic",
        "card_data": PredictionCardData(
            title="HOT PREDICTION",
            theme="pro",
            home=TeamInfo(
                name="Manchester City",
                rank=2, points=53, played=26, goal_diff=30, form="WWDWL",
            ),
            away=TeamInfo(
                name="Newcastle",
                rank=10, points=36, played=26, goal_diff=0, form="WLLLD",
            ),
            league="Premier League",
            league_country="England",
            league_round="Regular Season - 27",
            date_line="21 February 2026, 12:30 UTC",
            venue_name="Etihad Stadium",
            venue_city="Manchester",
            pick_display="Total Under 2.5",
            odd=2.75,
            signal_title="VALUE CHECK",
            signal_lines=[
                "Bookmakers give: 36.4%",
                "Our model: 46.2% (+9.9% edge)",
            ],
            home_win_prob=0.41,
            draw_prob=0.27,
            away_win_prob=0.32,
        ),
    },
    {
        "id": "pred_en_top_long_names",
        "card_data": PredictionCardData(
            title="TOP PREDICTION",
            theme="pro",
            home=TeamInfo(
                name="Nottingham Forest",
                rank=17, points=27, played=26, goal_diff=-13, form="DLDWD",
            ),
            away=TeamInfo(
                name="Borussia Monchengladbach",
                rank=6, points=42, played=26, goal_diff=6, form="DLWLW",
            ),
            league="Premier League",
            league_country="England",
            league_round="Regular Season - 27",
            date_line="21 February 2026, 15:00 UTC",
            pick_display="Total Over 2.5",
            odd=1.91,
            signal_title="VALUE OVERVIEW",
            signal_lines=[
                "Bookmakers give: 47.6%",
                "Our model: 69.3% (+21.7% edge)",
            ],
            home_win_prob=0.27,
            draw_prob=0.32,
            away_win_prob=0.41,
        ),
    },
    {
        "id": "pred_ru_top_layout",
        "card_data": PredictionCardData(
            title="\u0422\u041e\u041f \u041f\u0420\u041e\u0413\u041d\u041e\u0417",
            theme="pro",
            home=TeamInfo(
                name="\u0410\u0441\u0442\u043e\u043d \u0412\u0438\u043b\u043b\u0430",
                rank=3, points=50, played=26, goal_diff=10, form="WDLWL",
            ),
            away=TeamInfo(
                name="\u041b\u0438\u0434\u0441",
                rank=15, points=30, played=26, goal_diff=-9, form="WDLWD",
            ),
            league="Premier League",
            league_country="England",
            league_round="Regular Season - 27",
            date_line="21 \u0444\u0435\u0432\u0440\u0430\u043b\u044f 2026, 15:00 UTC",
            pick_display="\u0422\u043e\u0442\u0430\u043b \u0411\u043e\u043b\u044c\u0448\u0435 2.5",
            odd=1.73,
            signal_title="VALUE-\u0411\u0415\u0422 \u0418\u041d\u0414\u0418\u041a\u0410\u0422\u041e\u0420\u042b",
            signal_lines=[
                "\u0411\u0443\u043a\u043c\u0435\u043a\u0435\u0440\u044b \u0434\u0430\u044e\u0442: 57.8%",
                "\u041d\u0430\u0448\u0430 \u043c\u043e\u0434\u0435\u043b\u044c: 64.9% (+7.1% \u043f\u0435\u0440\u0435\u0432\u0435\u0441)",
            ],
            home_win_prob=0.47,
            draw_prob=0.26,
            away_win_prob=0.27,
        ),
    },
    {
        "id": "pred_en_extreme_odds",
        "card_data": PredictionCardData(
            title="HOT PREDICTION",
            theme="pro",
            home=TeamInfo(
                name="Manchester City",
                rank=2, points=53, played=26, goal_diff=30, form="WWDWL",
            ),
            away=TeamInfo(
                name="Newcastle",
                rank=10, points=36, played=26, goal_diff=0, form="WLLLD",
            ),
            league="Premier League",
            date_line="21 February 2026, 12:30 UTC",
            pick_display="Total Under 2.5",
            odd=12.38,
            signal_title="VALUE CHECK",
            signal_lines=[
                "Bookmakers give: 8.4%",
                "Our model: 12.2% (+3.8% edge)",
            ],
            home_win_prob=0.12,
            draw_prob=0.28,
            away_win_prob=0.60,
        ),
    },
    {
        "id": "pred_en_no_form",
        "card_data": PredictionCardData(
            title="STANDARD PREDICTION",
            theme="pro",
            home=TeamInfo(
                name="Lecce",
                rank=17, points=24, played=25, goal_diff=-14, form="",
            ),
            away=TeamInfo(
                name="Inter",
                rank=1, points=61, played=25, goal_diff=39, form="",
            ),
            league="Serie A",
            league_country="Italy",
            league_round="Regular Season - 26",
            date_line="22 February 2026, 14:00 UTC",
            pick_display="Total Over 2.5",
            odd=2.10,
            signal_title="VALUE CHECK",
            signal_lines=[
                "Bookmakers give: 52.4%",
                "Our model: 62.7% (+10.4% edge)",
            ],
            home_win_prob=0.15,
            draw_prob=0.27,
            away_win_prob=0.58,
        ),
    },
    {
        "id": "pred_en_viral_theme",
        "card_data": PredictionCardData(
            title="HIGH-CONFIDENCE PICK",
            theme="viral",
            home=TeamInfo(
                name="Atalanta",
                rank=3, points=57, played=25, goal_diff=21, form="WDWWW",
            ),
            away=TeamInfo(
                name="Napoli",
                rank=2, points=60, played=25, goal_diff=28, form="WWDWW",
            ),
            league="Serie A",
            league_country="Italy",
            league_round="Regular Season - 26",
            date_line="22 February 2026, 14:00 UTC",
            pick_display="Total Under 2.5",
            odd=2.10,
            signal_title="VALUE SIGNALS",
            signal_lines=[
                "Bookmakers give: 47.6%",
                "Our model: 69.3% (+21.7% edge)",
            ],
            home_win_prob=0.33,
            draw_prob=0.29,
            away_win_prob=0.38,
        ),
    },
    {
        "id": "pred_de_long_venue",
        "card_data": PredictionCardData(
            title="TOP PREDICTION",
            theme="pro",
            home=TeamInfo(
                name="Union Berlin",
                rank=10, points=25, played=22, goal_diff=-9, form="LDLLD",
            ),
            away=TeamInfo(
                name="Bayer Leverkusen",
                rank=6, points=39, played=21, goal_diff=16, form="LWWDW",
            ),
            league="Bundesliga",
            league_country="Germany",
            league_round="Regular Season - 23",
            date_line="21 February 2026, 14:30 UTC",
            venue_name="Stadion An Der Alten Forsterei",
            venue_city="Berlin",
            pick_display="Total Under 2.5",
            odd=1.91,
            signal_title="VALUE BET INDICATORS",
            signal_lines=[
                "Bookmakers give: 36.4%",
                "Our model: 53.6% (+17.2% edge)",
            ],
            home_win_prob=0.27,
            draw_prob=0.30,
            away_win_prob=0.43,
        ),
    },
    {
        "id": "pred_sparse_inputs",
        "card_data": PredictionCardData(
            title="HOT PREDICTION",
            theme="pro",
            home=TeamInfo(name="Team Alpha"),
            away=TeamInfo(name="Team Beta"),
            league="League",
            date_line="21 February 2026, 12:30 UTC",
            pick_display="Total Under 2.5",
            odd=2.30,
            signal_title="VALUE CHECK",
            signal_lines=[
                "Bookmakers give: 50.0%",
                "Our model: 50.0% (+0.0% edge)",
            ],
            home_win_prob=0.33,
            draw_prob=0.34,
            away_win_prob=0.33,
        ),
    },

    # ── Result cards ─────────────────────────────────────────
    {
        "id": "result_win",
        "card_data": ResultCardData(
            theme="pro",
            home=TeamInfo(
                name="Arsenal",
                rank=1, points=60, played=25, goal_diff=36, form="WWWDW",
            ),
            away=TeamInfo(
                name="Chelsea",
                rank=4, points=45, played=25, goal_diff=12, form="WLDWL",
            ),
            league="Premier League",
            league_country="England",
            league_round="Round 26",
            date_line="26 Feb 2026, 15:00 UTC",
            venue_name="Emirates Stadium",
            venue_city="London",
            home_goals=2,
            away_goals=1,
            status="WIN",
            profit=0.85,
            pick_display="Home Win",
            odd=1.85,
        ),
    },
    {
        "id": "result_loss",
        "card_data": ResultCardData(
            theme="pro",
            home=TeamInfo(
                name="Barcelona",
                rank=3, points=52, played=25, goal_diff=25, form="WDWLW",
            ),
            away=TeamInfo(
                name="Real Madrid",
                rank=1, points=62, played=25, goal_diff=38, form="WWWWW",
            ),
            league="La Liga",
            league_country="Spain",
            league_round="Jornada 26",
            date_line="26 Feb 2026, 21:00 UTC",
            venue_name="Spotify Camp Nou",
            venue_city="Barcelona",
            home_goals=0,
            away_goals=3,
            status="LOSS",
            profit=-1.0,
            pick_display="Total Under 2.5",
            odd=2.10,
        ),
    },
]
