"""
scripts/fetch_historical.py
============================
Сбор исторических данных с api-football.com для проекта pred1.

Собирает за каждый матч:
  - Результат + счёт + xG          (/fixtures)
  - Статистику матча                (/fixtures/statistics)
  - Исторические odds 1X2 + O/U2.5 (/odds)
  - События (голы/карточки/замены)  (/fixtures/events)

Сезоны: 2022 → текущий (2025/2026)
Лиги:   берутся из LEAGUE_IDS в .env (через запятую, напр. 39,78,140,135,61)

Запуск:
    python scripts/fetch_historical.py
    python scripts/fetch_historical.py --dry-run        # показать план без закачки
    python scripts/fetch_historical.py --resume         # продолжить с места остановки
    python scripts/fetch_historical.py --leagues 39,78  # переопределить лиги
    python scripts/fetch_historical.py --seasons 2022,2023  # конкретные сезоны

Переменные окружения (.env):
    API_FOOTBALL_KEY   — ключ api-football
    DATABASE_URL       — postgres DSN (postgresql://user:pass@host/db)
    LEAGUE_IDS         — лиги через запятую (39,78,140,135,61,94,253,71)
    BOOKMAKER_ID       — ID букмекера для odds (default: 1 = Bet365)

Таблицы создаются автоматически при первом запуске:
    hist_fixtures      — матчи + xG
    hist_statistics    — статистика матча
    hist_odds          — коэффициенты до матча
    hist_events        — события матча
    hist_fetch_log     — прогресс/лог закачки (для --resume)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# ─── Логгер ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("fetch_historical.log"),
    ],
)
log = logging.getLogger("fetch_historical")

# ─── Конфиг ──────────────────────────────────────────────────────────────────

API_KEY      = os.environ.get("API_FOOTBALL_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BOOKMAKER_ID = int(os.environ.get("BOOKMAKER_ID", "1"))  # 1 = Bet365

# API-Football: при ULTRA 75k/day безопасный темп ~0.8 req/сек
# Ставим 0.15 сек паузы = ~6 req/сек = ~518 400 req/day (с запасом)
# На практике используем 0.3 сек чтобы не словить 429
REQUEST_DELAY  = 0.30   # сек между запросами
RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5.0    # сек при ретрае

def _current_season() -> int:
    """
    Определяет текущий сезон.
    Большинство европейских лиг: сезон 2025 = 2025/2026 (старт авг 2025).
    MLS/Brazil: сезон 2025 = календарный 2025.
    Логика: если сейчас Jan–Jun → сезон прошлого года, Jul–Dec → текущий год.
    Но мы всегда добавляем оба варианта чтобы не промахнуться.
    """
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1

SEASONS_DEFAULT = [2022, 2023, 2024, 2025]  # 2025 = сезон 2025/2026
API_BASE        = "https://v3.football.api-sports.io"

# ─── DDL — создаём таблицы если нет ──────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS hist_fixtures (
    fixture_id      INTEGER PRIMARY KEY,
    league_id       INTEGER NOT NULL,
    season          INTEGER NOT NULL,
    home_team_id    INTEGER,
    away_team_id    INTEGER,
    home_team_name  TEXT,
    away_team_name  TEXT,
    match_date      TIMESTAMPTZ,
    status          TEXT,
    goals_home      INTEGER,
    goals_away      INTEGER,
    xg_home         NUMERIC(6,3),
    xg_away         NUMERIC(6,3),
    raw_json        JSONB,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hist_statistics (
    fixture_id      INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    team_name       TEXT,
    stats_json      JSONB,
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fixture_id, team_id)
);

CREATE TABLE IF NOT EXISTS hist_odds (
    fixture_id      INTEGER NOT NULL,
    bookmaker_id    INTEGER NOT NULL,
    market          TEXT NOT NULL,   -- '1X2' | 'Over/Under'
    line            TEXT NOT NULL DEFAULT '',  -- '' для 1X2, '2.5' для O/U
    odd_home        NUMERIC(8,3),
    odd_draw        NUMERIC(8,3),
    odd_away        NUMERIC(8,3),
    odd_over        NUMERIC(8,3),
    odd_under       NUMERIC(8,3),
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fixture_id, bookmaker_id, market, line)
);

CREATE TABLE IF NOT EXISTS hist_events (
    id              SERIAL PRIMARY KEY,
    fixture_id      INTEGER NOT NULL,
    team_id         INTEGER,
    player_id       INTEGER,
    player_name     TEXT,
    event_type      TEXT,   -- Goal / Card / subst
    event_detail    TEXT,
    elapsed         INTEGER,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hist_fetch_log (
    league_id   INTEGER NOT NULL,
    season      INTEGER NOT NULL,
    fixture_id  INTEGER NOT NULL,
    step        TEXT NOT NULL,       -- 'stats' | 'odds' | 'events'
    status      TEXT NOT NULL,       -- 'ok' | 'error' | 'no_data'
    error_msg   TEXT,
    done_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fixture_id, step)
);

CREATE INDEX IF NOT EXISTS hist_fixtures_league_season ON hist_fixtures(league_id, season);
CREATE INDEX IF NOT EXISTS hist_odds_fixture ON hist_odds(fixture_id);
CREATE INDEX IF NOT EXISTS hist_events_fixture ON hist_events(fixture_id);
"""

# ─── DB helpers ──────────────────────────────────────────────────────────────

def get_conn(dsn: str):
    """Синхронное подключение через psycopg2 (достаточно для скрипта)."""
    # asyncpg DSN → psycopg2 DSN
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


def init_db(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()
    log.info("DB tables ready")


def already_done(conn, fixture_id: int, step: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM hist_fetch_log WHERE fixture_id=%s AND step=%s AND status='ok'",
            (fixture_id, step)
        )
        return cur.fetchone() is not None


def mark_done(conn, league_id: int, season: int, fixture_id: int, step: str,
              status: str = "ok", error_msg: str = None) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO hist_fetch_log (league_id, season, fixture_id, step, status, error_msg)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (fixture_id, step) DO UPDATE
              SET status=EXCLUDED.status, error_msg=EXCLUDED.error_msg, done_at=NOW()
        """, (league_id, season, fixture_id, step, status, error_msg))
    conn.commit()

# ─── API client ──────────────────────────────────────────────────────────────

class APIFootballClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "x-apisports-key": api_key,
            "Accept": "application/json",
        }
        self._requests_today = 0
        self._session: Optional[httpx.Client] = None

    def __enter__(self):
        self._session = httpx.Client(headers=self.headers, timeout=30)
        return self

    def __exit__(self, *args):
        if self._session:
            self._session.close()

    def get(self, endpoint: str, params: dict) -> dict:
        url = f"{API_BASE}{endpoint}"
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                time.sleep(REQUEST_DELAY)
                resp = self._session.get(url, params=params)
                self._requests_today += 1

                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    log.warning(f"Rate limit 429, ждём {wait}с...")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # API-Football возвращает 200 даже при ошибках
                if data.get("errors"):
                    errs = data["errors"]
                    log.warning(f"API errors {endpoint} {params}: {errs}")
                    return {}

                remaining = data.get("response", [])
                quota_left = resp.headers.get("x-ratelimit-requests-remaining")
                if self._requests_today % 100 == 0:
                    log.info(f"  Запросов сегодня: {self._requests_today} | Осталось квоты: {quota_left}")

                return data

            except httpx.HTTPError as e:
                log.warning(f"HTTP ошибка (попытка {attempt}/{RETRY_ATTEMPTS}): {e}")
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_DELAY * attempt)
        return {}

    @property
    def requests_today(self):
        return self._requests_today


# ─── Парсеры ответов ──────────────────────────────────────────────────────────

def parse_fixture(f: dict) -> dict:
    fix  = f.get("fixture", {})
    lge  = f.get("league", {})
    home = f.get("teams", {}).get("home", {})
    away = f.get("teams", {}).get("away", {})
    goals = f.get("goals", {})
    score = f.get("score", {})

    # xG может быть в разных местах в зависимости от версии API
    xg = f.get("statistics", [{}])
    xg_home = f.get("xg", {}).get("home") if isinstance(f.get("xg"), dict) else None
    xg_away = f.get("xg", {}).get("away") if isinstance(f.get("xg"), dict) else None

    return {
        "fixture_id":     fix.get("id"),
        "league_id":      lge.get("id"),
        "season":         lge.get("season"),
        "home_team_id":   home.get("id"),
        "away_team_id":   away.get("id"),
        "home_team_name": home.get("name"),
        "away_team_name": away.get("name"),
        "match_date":     fix.get("date"),
        "status":         fix.get("status", {}).get("short"),
        "goals_home":     goals.get("home"),
        "goals_away":     goals.get("away"),
        "xg_home":        xg_home,
        "xg_away":        xg_away,
        "raw_json":       json.dumps(f),
    }


def parse_statistics(fixture_id: int, stats_response: list) -> list:
    rows = []
    for team_stat in stats_response:
        team = team_stat.get("team", {})
        rows.append({
            "fixture_id": fixture_id,
            "team_id":    team.get("id"),
            "team_name":  team.get("name"),
            "stats_json": json.dumps(team_stat.get("statistics", [])),
        })
    return rows


def parse_odds(fixture_id: int, bookmaker_id: int, odds_response: list) -> list:
    """Парсит odds ответ, ищет 1X2 и Over/Under 2.5."""
    rows = []
    for fixture_odds in odds_response:
        for bookmaker in fixture_odds.get("bookmakers", []):
            if bookmaker.get("id") != bookmaker_id:
                continue
            for bet in bookmaker.get("bets", []):
                bet_name = bet.get("name", "")

                # 1X2
                if bet_name in ("Match Winner", "1X2"):
                    row = {"fixture_id": fixture_id, "bookmaker_id": bookmaker_id,
                           "market": "1X2", "line": "",
                           "odd_home": None, "odd_draw": None, "odd_away": None,
                           "odd_over": None, "odd_under": None}
                    for v in bet.get("values", []):
                        val = v.get("value")
                        odd = _to_float(v.get("odd"))
                        if val == "Home":   row["odd_home"] = odd
                        elif val == "Draw": row["odd_draw"] = odd
                        elif val == "Away": row["odd_away"] = odd
                    rows.append(row)

                # Over/Under — ищем линию 2.5
                elif "Over/Under" in bet_name or bet_name == "Goals Over/Under":
                    for v in bet.get("values", []):
                        val = v.get("value", "")
                        # val может быть "Over 2.5" / "Under 2.5"
                        if "2.5" in str(val):
                            # найти или создать строку для этой линии
                            line_row = next(
                                (r for r in rows
                                 if r["fixture_id"] == fixture_id
                                 and r["market"] == "Over/Under"
                                 and r["line"] == "2.5"),
                                None
                            )
                            if line_row is None:
                                line_row = {
                                    "fixture_id": fixture_id, "bookmaker_id": bookmaker_id,
                                    "market": "Over/Under", "line": "2.5",
                                    "odd_home": None, "odd_draw": None, "odd_away": None,
                                    "odd_over": None, "odd_under": None,
                                }
                                rows.append(line_row)
                            odd = _to_float(v.get("odd"))
                            if "Over" in str(val):  line_row["odd_over"] = odd
                            if "Under" in str(val): line_row["odd_under"] = odd
    return rows


def parse_events(fixture_id: int, events_response: list) -> list:
    rows = []
    for ev in events_response:
        team = ev.get("team", {}) or {}
        player = ev.get("player", {}) or {}
        rows.append({
            "fixture_id":   fixture_id,
            "team_id":      team.get("id"),
            "player_id":    player.get("id"),
            "player_name":  player.get("name"),
            "event_type":   ev.get("type"),
            "event_detail": ev.get("detail"),
            "elapsed":      (ev.get("time") or {}).get("elapsed"),
        })
    return rows


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── DB writers ──────────────────────────────────────────────────────────────

def upsert_fixtures(conn, rows: list) -> int:
    if not rows:
        return 0
    cols = ["fixture_id","league_id","season","home_team_id","away_team_id",
            "home_team_name","away_team_name","match_date","status",
            "goals_home","goals_away","xg_home","xg_away","raw_json"]
    values = [[r[c] for c in cols] for r in rows]
    sql = f"""
        INSERT INTO hist_fixtures ({','.join(cols)})
        VALUES %s
        ON CONFLICT (fixture_id) DO UPDATE SET
            goals_home=EXCLUDED.goals_home, goals_away=EXCLUDED.goals_away,
            xg_home=EXCLUDED.xg_home, xg_away=EXCLUDED.xg_away,
            status=EXCLUDED.status, raw_json=EXCLUDED.raw_json,
            fetched_at=NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values)
    conn.commit()
    return len(rows)


def upsert_statistics(conn, rows: list) -> int:
    if not rows:
        return 0
    cols = ["fixture_id","team_id","team_name","stats_json"]
    values = [[r[c] for c in cols] for r in rows]
    sql = f"""
        INSERT INTO hist_statistics ({','.join(cols)})
        VALUES %s
        ON CONFLICT (fixture_id, team_id) DO UPDATE SET
            stats_json=EXCLUDED.stats_json, fetched_at=NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values)
    conn.commit()
    return len(rows)


def upsert_odds(conn, rows: list) -> int:
    if not rows:
        return 0
    cols = ["fixture_id","bookmaker_id","market","line",
            "odd_home","odd_draw","odd_away","odd_over","odd_under"]
    # Ensure line is never None (PK column)
    for r in rows:
        if r.get("line") is None:
            r["line"] = ""
    values = [[r[c] for c in cols] for r in rows]
    sql = f"""
        INSERT INTO hist_odds ({','.join(cols)})
        VALUES %s
        ON CONFLICT (fixture_id, bookmaker_id, market, line) DO UPDATE SET
            odd_home=EXCLUDED.odd_home, odd_draw=EXCLUDED.odd_draw,
            odd_away=EXCLUDED.odd_away, odd_over=EXCLUDED.odd_over,
            odd_under=EXCLUDED.odd_under, fetched_at=NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values)
    conn.commit()
    return len(rows)


def insert_events(conn, rows: list) -> int:
    if not rows:
        return 0
    # Удаляем старые события для фикстуры и вставляем заново
    fixture_ids = list({r["fixture_id"] for r in rows})
    cols = ["fixture_id","team_id","player_id","player_name",
            "event_type","event_detail","elapsed"]
    values = [[r[c] for c in cols] for r in rows]
    sql = f"""
        INSERT INTO hist_events ({','.join(cols)}) VALUES %s
    """
    with conn.cursor() as cur:
        for fid in fixture_ids:
            cur.execute("DELETE FROM hist_events WHERE fixture_id=%s", (fid,))
        execute_values(cur, sql, values)
    conn.commit()
    return len(rows)


# ─── Основная логика ──────────────────────────────────────────────────────────

def get_leagues_from_env() -> list[int]:
    raw = os.environ.get("LEAGUE_IDS", "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


def fetch_season(
    client: APIFootballClient,
    conn,
    league_id: int,
    season: int,
    resume: bool,
    dry_run: bool,
    from_date: Optional[str] = None,   # "YYYY-MM-DD" — качать только от этой даты
) -> dict:
    """Качает все матчи одного сезона одной лиги.

    Для текущего сезона (2025/2026) качаем FT-матчи до сегодня включительно.
    Для прошлых сезонов — все FT без ограничения по дате.
    """
    stats = {"fixtures": 0, "statistics": 0, "odds": 0, "events": 0, "skipped": 0, "errors": 0}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_season = _current_season()
    is_current = (season == current_season)

    log.info(f"  ▶ League {league_id}, Season {season}"
             f"{' [ТЕКУЩИЙ — до ' + today_str + ']' if is_current else ''}")

    if dry_run:
        log.info(f"    [DRY RUN] пропускаем реальные запросы")
        return stats

    # 1. Список матчей сезона
    # Для текущего сезона: status=FT + to=сегодня (не тянуть будущие матчи)
    # Для прошлых: только status=FT
    params: dict = {"league": league_id, "season": season, "status": "FT"}
    if is_current:
        params["to"] = today_str
    if from_date:
        params["from"] = from_date
        log.info(f"    Фильтр по дате: от {from_date} до {params.get('to', today_str)}")

    data = client.get("/fixtures", params)
    fixtures = data.get("response", [])
    if not fixtures:
        log.info(f"    Нет завершённых матчей (league={league_id}, season={season})")
        return stats

    log.info(f"    Найдено {len(fixtures)} завершённых матчей"
             f"{' до ' + today_str if is_current else ''}")

    # 2. Сохраняем базовые данные по матчам
    parsed = [parse_fixture(f) for f in fixtures]
    # Дополнительно пробуем вытащить xG из raw_json если не пришёл в основном ответе
    for p, f in zip(parsed, fixtures):
        if p["xg_home"] is None:
            score = f.get("score", {})
            # В некоторых версиях API xG в fixture.statistics
            for team_stat in f.get("statistics", []):
                for s in team_stat.get("statistics", []):
                    if s.get("type") == "expected_goals":
                        team_id = team_stat.get("team", {}).get("id")
                        if team_id == p["home_team_id"]:
                            p["xg_home"] = _to_float(s.get("value"))
                        elif team_id == p["away_team_id"]:
                            p["xg_away"] = _to_float(s.get("value"))

    n = upsert_fixtures(conn, parsed)
    stats["fixtures"] += n
    log.info(f"    ✓ Сохранено {n} матчей в hist_fixtures")

    # 3. Для каждого матча — статистика, odds, события
    fixture_ids = [p["fixture_id"] for p in parsed if p["fixture_id"]]

    for i, fixture_id in enumerate(fixture_ids):
        if i % 50 == 0:
            log.info(f"    Прогресс: {i}/{len(fixture_ids)} матчей | "
                     f"API запросов: {client.requests_today}")

        # ── Статистика ─────────────────────────────────────────────────────
        step = "stats"
        if resume and already_done(conn, fixture_id, step):
            stats["skipped"] += 1
        else:
            d = client.get("/fixtures/statistics", {"fixture": fixture_id})
            resp = d.get("response", [])
            rows = parse_statistics(fixture_id, resp)
            if rows:
                upsert_statistics(conn, rows)
                stats["statistics"] += len(rows)
                mark_done(conn, league_id, season, fixture_id, step)
            else:
                mark_done(conn, league_id, season, fixture_id, step, "no_data")

            # Также вытащим xG из статистики если не было в fixtures
            for team_stat in resp:
                team_id = team_stat.get("team", {}).get("id")
                for s in team_stat.get("statistics", []):
                    if s.get("type") in ("expected_goals", "Expected Goals"):
                        val = _to_float(s.get("value"))
                        if val is not None:
                            fix_row = next((p for p in parsed if p["fixture_id"] == fixture_id), None)
                            if fix_row:
                                if team_id == fix_row["home_team_id"] and fix_row["xg_home"] is None:
                                    fix_row["xg_home"] = val
                                    with conn.cursor() as cur:
                                        cur.execute(
                                            "UPDATE hist_fixtures SET xg_home=%s WHERE fixture_id=%s",
                                            (val, fixture_id)
                                        )
                                    conn.commit()
                                elif team_id == fix_row["away_team_id"] and fix_row["xg_away"] is None:
                                    fix_row["xg_away"] = val
                                    with conn.cursor() as cur:
                                        cur.execute(
                                            "UPDATE hist_fixtures SET xg_away=%s WHERE fixture_id=%s",
                                            (val, fixture_id)
                                        )
                                    conn.commit()

        # ── Odds ───────────────────────────────────────────────────────────
        step = "odds"
        if resume and already_done(conn, fixture_id, step):
            stats["skipped"] += 1
        else:
            d = client.get("/odds", {"fixture": fixture_id, "bookmaker": BOOKMAKER_ID})
            resp = d.get("response", [])
            rows = parse_odds(fixture_id, BOOKMAKER_ID, resp)
            if rows:
                upsert_odds(conn, rows)
                stats["odds"] += len(rows)
                mark_done(conn, league_id, season, fixture_id, step)
            else:
                mark_done(conn, league_id, season, fixture_id, step, "no_data")

        # ── События ────────────────────────────────────────────────────────
        step = "events"
        if resume and already_done(conn, fixture_id, step):
            stats["skipped"] += 1
        else:
            d = client.get("/fixtures/events", {"fixture": fixture_id})
            resp = d.get("response", [])
            rows = parse_events(fixture_id, resp)
            if rows:
                insert_events(conn, rows)
                stats["events"] += len(rows)
                mark_done(conn, league_id, season, fixture_id, step)
            else:
                mark_done(conn, league_id, season, fixture_id, step, "no_data")

    return stats


def print_plan(leagues: list[int], seasons: list[int]) -> None:
    """Показывает план закачки и оценку запросов."""
    print("\n" + "="*60)
    print("ПЛАН ЗАКАЧКИ (DRY RUN)")
    print("="*60)
    print(f"Лиги:    {leagues}")
    print(f"Сезоны:  {seasons}")
    est_matches = len(leagues) * len(seasons) * 380
    est_requests = est_matches * 4  # fixtures + stats + odds + events
    est_days = est_requests / 75_000
    print(f"\nОценка матчей:   ~{est_matches:,}")
    print(f"Оценка запросов: ~{est_requests:,}  (4 req/матч)")
    print(f"При 75k/day:     ~{est_days:.1f} дней")
    print(f"\nБукмекер:        ID={BOOKMAKER_ID} (1=Bet365)")
    print("="*60 + "\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical football data from api-football.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Режимы запуска:
  Первичная закачка (один раз):
    python scripts/fetch_historical.py --resume

  Ежедневное обновление (добавить в cron после синхронизации текущих данных):
    python scripts/fetch_historical.py --update-today
    # cron пример: 0 4 * * * python /app/scripts/fetch_historical.py --update-today

  Только конкретные лиги/сезоны:
    python scripts/fetch_historical.py --leagues 39,78 --seasons 2023,2024

  Проверить план без запросов:
    python scripts/fetch_historical.py --dry-run
        """
    )
    parser.add_argument("--leagues",      help="Лиги через запятую (default: из LEAGUE_IDS в .env)")
    parser.add_argument("--seasons",      help="Сезоны через запятую (default: 2022,2023,2024,2025)")
    parser.add_argument("--dry-run",      action="store_true", help="Показать план без закачки")
    parser.add_argument("--resume",       action="store_true",
                        help="Пропускать уже успешно скачанные шаги (из hist_fetch_log)")
    parser.add_argument("--update-today", action="store_true",
                        help="Режим ежедневного обновления: только текущий сезон, "
                             "только матчи за последние 2 дня. Запускать из cron.")
    parser.add_argument("--from-date",    help="Качать только матчи от этой даты (YYYY-MM-DD)")
    parser.add_argument("--bookmaker",    type=int, help="ID букмекера (default: из .env или 1=Bet365)")
    args = parser.parse_args()

    global BOOKMAKER_ID
    if args.bookmaker:
        BOOKMAKER_ID = args.bookmaker

    # ── Лиги ──────────────────────────────────────────────────────────────────
    if args.leagues:
        leagues = [int(x.strip()) for x in args.leagues.split(",") if x.strip().isdigit()]
    else:
        leagues = get_leagues_from_env()
    if not leagues:
        log.error("Нет лиг! Задайте LEAGUE_IDS в .env или передайте --leagues 39,78,...")
        sys.exit(1)

    # ── Сезоны и режим дат ────────────────────────────────────────────────────
    today     = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")

    if args.update_today:
        # Только текущий сезон; from_date = 2 дня назад (захватываем вчерашние матчи)
        seasons   = [_current_season()]
        from_date = (today.replace(hour=0, minute=0, second=0)
                     .__class__(today.year, today.month, today.day, tzinfo=timezone.utc))
        from_date_str = (today.__class__(
            today.year, today.month, today.day, tzinfo=timezone.utc
        ) if False else None)
        # Простой способ: 2 дня назад
        from datetime import timedelta
        from_date_str = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        log.info(f"--update-today: сезон {seasons[0]}, матчи с {from_date_str} по {today_str}")
        # В режиме обновления всегда resume=True (не перекачиваем старые)
        args.resume = True
    elif args.from_date:
        seasons       = [_current_season()] if not args.seasons else \
                        [int(x.strip()) for x in args.seasons.split(",") if x.strip().isdigit()]
        from_date_str = args.from_date
    elif args.seasons:
        seasons       = [int(x.strip()) for x in args.seasons.split(",") if x.strip().isdigit()]
        from_date_str = None
    else:
        seasons       = SEASONS_DEFAULT
        from_date_str = None

    # ── Проверки ──────────────────────────────────────────────────────────────
    if not API_KEY and not args.dry_run:
        log.error("API_FOOTBALL_KEY не задан в .env!")
        sys.exit(1)
    if not DATABASE_URL and not args.dry_run:
        log.error("DATABASE_URL не задан в .env!")
        sys.exit(1)

    print_plan(leagues, seasons)

    if args.dry_run:
        log.info("DRY RUN завершён. Для реальной закачки запустите без --dry-run")
        return

    # ── Подключение к БД ──────────────────────────────────────────────────────
    try:
        conn = get_conn(DATABASE_URL)
        init_db(conn)
    except Exception as e:
        log.error(f"Не могу подключиться к БД: {e}")
        sys.exit(1)

    total_stats = {"fixtures": 0, "statistics": 0, "odds": 0, "events": 0,
                   "skipped": 0, "errors": 0}
    start_time = time.time()

    with APIFootballClient(API_KEY) as client:
        for league_id in leagues:
            for season in seasons:
                log.info(f"\n{'─'*50}")
                log.info(f"League {league_id} / Season {season}")
                log.info(f"{'─'*50}")
                try:
                    s = fetch_season(
                        client, conn, league_id, season,
                        resume=args.resume,
                        dry_run=False,
                        from_date=from_date_str,
                    )
                    for k in total_stats:
                        total_stats[k] += s.get(k, 0)
                except KeyboardInterrupt:
                    log.info("\n⚠ Прервано. Запустите с --resume чтобы продолжить с места остановки.")
                    break
                except Exception as e:
                    log.error(f"Ошибка при league={league_id} season={season}: {e}")
                    total_stats["errors"] += 1
                    continue

    elapsed = time.time() - start_time
    log.info(f"\n{'='*50}")
    log.info(f"ИТОГ за {elapsed/60:.1f} мин:")
    log.info(f"  Матчей сохранено:    {total_stats['fixtures']}")
    log.info(f"  Статистик:           {total_stats['statistics']}")
    log.info(f"  Odds строк:          {total_stats['odds']}")
    log.info(f"  Событий:             {total_stats['events']}")
    log.info(f"  Пропущено (resume):  {total_stats['skipped']}")
    log.info(f"  Ошибок:              {total_stats['errors']}")
    log.info(f"  API запросов итого:  {client.requests_today}")
    log.info(f"{'='*50}")

    conn.close()


if __name__ == "__main__":
    main()