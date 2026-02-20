from app.jobs.sync_data import _extract_total_25


def test_extract_total_25_prefers_full_match_market() -> None:
    bookmaker = {
        "id": 1,
        "name": "10Bet",
        "bets": [
            {
                "id": 5,
                "name": "Goals Over/Under",
                "values": [
                    {"value": "Over 2.5", "odd": "1.42"},
                    {"value": "Under 2.5", "odd": "2.80"},
                ],
            },
            {
                "id": 26,
                "name": "Goals Over/Under - Second Half",
                "values": [
                    {"value": "Over 2.5", "odd": "2.75"},
                    {"value": "Under 2.5", "odd": "1.40"},
                ],
            },
        ],
    }

    over, under = _extract_total_25(bookmaker)

    assert over == 1.42
    assert under == 2.80
