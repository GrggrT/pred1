"""
scripts/fetch_odds_footballdata.py
==================================
Fetch historical odds from football-data.co.uk CSV files
and insert into hist_odds table matched by date + team names.

football-data.co.uk provides free CSVs with Bet365 and Pinnacle odds
for all top European leagues back to 1993.

Usage:
    python scripts/fetch_odds_footballdata.py
    python scripts/fetch_odds_footballdata.py --dry-run
    python scripts/fetch_odds_footballdata.py --seasons 2324,2425
    python scripts/fetch_odds_footballdata.py --bookmaker pinnacle
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("fetch_odds_footballdata")

# ---------------------------------------------------------------------------
# League mapping: API-Football league_id -> football-data.co.uk division code
# ---------------------------------------------------------------------------

LEAGUE_TO_DIV = {
    39: "E0",    # EPL
    61: "F1",    # Ligue 1
    78: "D1",    # Bundesliga
    94: "P1",    # Primeira Liga
    135: "I1",   # Serie A
    140: "SP1",  # La Liga
}

# API-Football season year -> football-data.co.uk season code
# API-Football uses start year (2022 = 2022-23 season)
# football-data uses YYMM format (2223 = 2022-23 season)
def _season_code(api_season: int) -> str:
    y1 = api_season % 100
    y2 = (api_season + 1) % 100
    return f"{y1:02d}{y2:02d}"

# ---------------------------------------------------------------------------
# Team name mapping: football-data.co.uk -> API-Football names
# ---------------------------------------------------------------------------

TEAM_NAME_MAP = {
    # EPL (39)
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Nott'm Forest": "Nottingham Forest",
    "Sheffield United": "Sheffield United",
    "Luton": "Luton",
    "Leeds": "Leeds",
    "Leicester": "Leicester",

    # Ligue 1 (61)
    "Paris SG": "Paris Saint Germain",
    "St Etienne": "Saint Etienne",
    "Brest": "Stade Brestois 29",
    "Ajaccio": "Ajaccio",
    "Clermont": "Clermont",
    "Troyes": "Troyes",

    # Bundesliga (78)
    "Bayern Munich": "Bayern München",
    "Dortmund": "Borussia Dortmund",
    "M'gladbach": "Borussia Mönchengladbach",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "Leverkusen": "Bayer Leverkusen",
    "Hoffenheim": "1899 Hoffenheim",
    "Augsburg": "FC Augsburg",
    "Freiburg": "SC Freiburg",
    "Mainz": "FSV Mainz 05",
    "Wolfsburg": "VfL Wolfsburg",
    "Bochum": "VfL Bochum",
    "Stuttgart": "VfB Stuttgart",
    "Heidenheim": "1. FC Heidenheim",
    "FC Koln": "1. FC Köln",
    "St Pauli": "FC St. Pauli",
    "Holstein Kiel": "Holstein Kiel",
    "Hertha": "Hertha",
    "Schalke 04": "Schalke 04",
    "Darmstadt": "Darmstadt",

    # Primeira Liga (94)
    "Porto": "FC Porto",
    "Sp Lisbon": "Sporting CP",
    "Sp Braga": "SC Braga",
    "Gil Vicente": "GIL Vicente",
    "Chaves": "Chaves",
    "Pacos Ferreira": "Pacos Ferreira",
    "Portimonense": "Portimonense",
    "Maritimo": "Maritimo",

    # Serie A (135)
    "Milan": "AC Milan",
    "Roma": "AS Roma",
    "Spezia": "Spezia",
    "Sampdoria": "Sampdoria",
    "Salernitana": "Salernitana",
    "Frosinone": "Frosinone",

    # La Liga (140)
    "Ath Madrid": "Atletico Madrid",
    "Ath Bilbao": "Athletic Club",
    "Betis": "Real Betis",
    "Sociedad": "Real Sociedad",
    "Celta": "Celta Vigo",
    "Espanol": "Espanyol",
    "Vallecano": "Rayo Vallecano",
    "Almeria": "Almeria",
    "Cadiz": "Cadiz",
    "Granada": "Granada",
}


def _normalize_team(name: str) -> str:
    """Map football-data team name to API-Football name."""
    return TEAM_NAME_MAP.get(name, name)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn(dsn: str):
    import psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


def _build_fixture_index(conn, league_id: int, season: int) -> dict:
    """
    Build lookup: (match_date, home_team_name, away_team_name) -> fixture_id.
    Uses a date window of +-1 day to handle timezone differences.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT hf.fixture_id, hf.match_date::date, th.name, ta.name
        FROM hist_fixtures hf
        JOIN teams th ON th.id = hf.home_team_id
        JOIN teams ta ON ta.id = hf.away_team_id
        WHERE hf.league_id = %s AND hf.season = %s
    """, (league_id, season))

    index = {}
    for fid, mdate, home, away in cur.fetchall():
        # Index with exact date and +-1 day for timezone flexibility
        for delta in [0, -1, 1]:
            d = mdate + timedelta(days=delta)
            key = (d, home.lower(), away.lower())
            if key not in index:
                index[key] = fid
    cur.close()
    return index


# ---------------------------------------------------------------------------
# CSV download & parse
# ---------------------------------------------------------------------------

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"


def _download_csv(div: str, season_code: str) -> list[dict]:
    """Download and parse CSV from football-data.co.uk."""
    url = BASE_URL.format(season=season_code, div=div)
    log.info("Downloading %s", url)

    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Failed to download %s: %s", url, e)
        return []

    text = resp.text
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        if not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        rows.append(row)

    log.info("  Parsed %d matches from %s", len(rows), url)
    return rows


def _parse_date(date_str: str) -> datetime | None:
    """Parse date from football-data CSV (DD/MM/YYYY or DD/MM/YY)."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _safe_float(val: str | None) -> float | None:
    """Parse float or return None."""
    if not val or val.strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def process_league_season(
    conn,
    league_id: int,
    api_season: int,
    bookmaker: str = "bet365",
    dry_run: bool = False,
) -> dict:
    """Process one league+season: download CSV, match fixtures, upsert odds."""
    div = LEAGUE_TO_DIV.get(league_id)
    if not div:
        log.warning("No division mapping for league %d", league_id)
        return {"matched": 0, "unmatched": 0, "inserted": 0}

    season_code = _season_code(api_season)
    rows = _download_csv(div, season_code)
    if not rows:
        return {"matched": 0, "unmatched": 0, "inserted": 0}

    # Select odds columns based on bookmaker
    if bookmaker == "pinnacle":
        h_col, d_col, a_col = "PSH", "PSD", "PSA"
        over_col, under_col = "P>2.5", "P<2.5"
        bk_id = 5  # Pinnacle
    else:  # bet365
        h_col, d_col, a_col = "B365H", "B365D", "B365A"
        over_col, under_col = "B365>2.5", "B365<2.5"
        bk_id = 1  # Bet365

    fixture_index = _build_fixture_index(conn, league_id, api_season)
    log.info("  Fixture index: %d entries for league=%d season=%d",
             len(fixture_index) // 3, league_id, api_season)

    matched = 0
    unmatched = 0
    inserted = 0
    unmatched_examples = []

    for row in rows:
        date = _parse_date(row.get("Date", ""))
        if not date:
            continue

        home_raw = row.get("HomeTeam", "")
        away_raw = row.get("AwayTeam", "")
        home = _normalize_team(home_raw)
        away = _normalize_team(away_raw)

        # Try to find fixture
        key = (date.date(), home.lower(), away.lower())
        fid = fixture_index.get(key)

        if not fid:
            unmatched += 1
            if len(unmatched_examples) < 5:
                unmatched_examples.append(f"{date.date()} {home_raw}({home}) vs {away_raw}({away})")
            continue

        matched += 1

        # Parse odds
        odd_h = _safe_float(row.get(h_col))
        odd_d = _safe_float(row.get(d_col))
        odd_a = _safe_float(row.get(a_col))
        odd_over = _safe_float(row.get(over_col))
        odd_under = _safe_float(row.get(under_col))

        if dry_run:
            continue

        # Upsert 1X2 odds
        if odd_h and odd_d and odd_a:
            _upsert_odds(conn, fid, bk_id, "1X2", "",
                         odd_h, odd_d, odd_a, None, None)
            inserted += 1

        # Upsert Over/Under 2.5
        if odd_over and odd_under:
            _upsert_odds(conn, fid, bk_id, "Over/Under", "2.5",
                         None, None, None, odd_over, odd_under)
            inserted += 1

    if unmatched_examples:
        log.info("  Unmatched examples: %s", unmatched_examples)

    if not dry_run:
        conn.commit()

    return {"matched": matched, "unmatched": unmatched, "inserted": inserted}


def _upsert_odds(conn, fixture_id, bookmaker_id, market, line,
                 odd_home, odd_draw, odd_away, odd_over, odd_under):
    """Insert or update odds row."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO hist_odds (fixture_id, bookmaker_id, market, line,
                               odd_home, odd_draw, odd_away, odd_over, odd_under, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (fixture_id, bookmaker_id, market, line)
        DO UPDATE SET
            odd_home = COALESCE(EXCLUDED.odd_home, hist_odds.odd_home),
            odd_draw = COALESCE(EXCLUDED.odd_draw, hist_odds.odd_draw),
            odd_away = COALESCE(EXCLUDED.odd_away, hist_odds.odd_away),
            odd_over = COALESCE(EXCLUDED.odd_over, hist_odds.odd_over),
            odd_under = COALESCE(EXCLUDED.odd_under, hist_odds.odd_under),
            fetched_at = NOW()
    """, (fixture_id, bookmaker_id, market, line or "",
          odd_home, odd_draw, odd_away, odd_over, odd_under))
    cur.close()


def main():
    parser = argparse.ArgumentParser(description="Fetch historical odds from football-data.co.uk")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without writing to DB")
    parser.add_argument("--seasons", help="Comma-separated season codes (e.g. 2223,2324)")
    parser.add_argument("--leagues", help="Comma-separated API-Football league IDs")
    parser.add_argument("--bookmaker", default="bet365", choices=["bet365", "pinnacle"])
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    conn = _get_conn(database_url)

    if args.leagues:
        league_ids = [int(x.strip()) for x in args.leagues.split(",")]
    else:
        league_ids = list(LEAGUE_TO_DIV.keys())

    if args.seasons:
        # User provides API-Football season years (e.g. 2022,2023,2024)
        api_seasons = [int(x.strip()) for x in args.seasons.split(",")]
    else:
        api_seasons = [2022, 2023, 2024, 2025]

    total_matched = 0
    total_unmatched = 0
    total_inserted = 0

    for league_id in league_ids:
        for season in api_seasons:
            log.info("Processing league=%d season=%d", league_id, season)
            result = process_league_season(
                conn, league_id, season,
                bookmaker=args.bookmaker,
                dry_run=args.dry_run,
            )
            total_matched += result["matched"]
            total_unmatched += result["unmatched"]
            total_inserted += result["inserted"]
            log.info("  Result: matched=%d, unmatched=%d, inserted=%d",
                     result["matched"], result["unmatched"], result["inserted"])

    log.info("")
    log.info("=" * 50)
    log.info("TOTAL: matched=%d, unmatched=%d, inserted=%d",
             total_matched, total_unmatched, total_inserted)
    if total_unmatched > 0:
        log.info("  Match rate: %.1f%%",
                 total_matched / (total_matched + total_unmatched) * 100)
    log.info("=" * 50)

    conn.close()


if __name__ == "__main__":
    main()
