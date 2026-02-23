from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .decimalutils import q_ev, q_money, q_prob
from .logger import get_logger


def _default_season() -> int:
    """European season year: Jul-Dec -> current year, Jan-Jun -> previous year."""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else (now.year - 1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field("dev", alias="APP_ENV")
    app_mode: str = Field("live", alias="APP_MODE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    database_url: str = Field(..., alias="DATABASE_URL")

    api_football_key: str = Field("", alias="API_FOOTBALL_KEY")
    api_football_host: str = Field("v3.football.api-sports.io", alias="API_FOOTBALL_HOST")
    api_football_base: str = Field("https://v3.football.api-sports.io", alias="API_FOOTBALL_BASE")
    api_football_fixtures_ttl_recent_seconds: int = Field(default=600, alias="API_FOOTBALL_FIXTURES_TTL_RECENT_SECONDS")
    api_football_fixtures_ttl_historical_seconds: int = Field(
        default=24 * 3600, alias="API_FOOTBALL_FIXTURES_TTL_HISTORICAL_SECONDS"
    )
    api_football_odds_ttl_seconds: int = Field(default=300, alias="API_FOOTBALL_ODDS_TTL_SECONDS")
    api_football_odds_season_ttl_seconds: int = Field(default=6 * 3600, alias="API_FOOTBALL_ODDS_SEASON_TTL_SECONDS")
    api_football_injuries_ttl_seconds: int = Field(default=3 * 3600, alias="API_FOOTBALL_INJURIES_TTL_SECONDS")
    api_football_standings_ttl_seconds: int = Field(default=12 * 3600, alias="API_FOOTBALL_STANDINGS_TTL_SECONDS")
    api_football_fixture_stats_ttl_seconds: int = Field(
        default=12 * 3600, alias="API_FOOTBALL_FIXTURE_STATS_TTL_SECONDS"
    )
    api_football_daily_limit: int = Field(default=75000, alias="API_FOOTBALL_DAILY_LIMIT")
    api_football_guard_enabled: bool = Field(default=False, alias="API_FOOTBALL_GUARD_ENABLED")
    api_football_guard_margin: int = Field(default=0, alias="API_FOOTBALL_GUARD_MARGIN")
    api_football_run_budget_cache_misses: int = Field(default=0, alias="API_FOOTBALL_RUN_BUDGET_CACHE_MISSES")

    openweather_key: str = Field("", alias="OPENWEATHER_KEY")
    openweather_base: str = Field("https://api.openweathermap.org/data/2.5", alias="OPENWEATHER_BASE")

    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    telegram_channel_en: str = Field("", alias="TELEGRAM_CHANNEL_EN")
    telegram_channel_uk: str = Field("", alias="TELEGRAM_CHANNEL_UK")
    telegram_channel_ru: str = Field("", alias="TELEGRAM_CHANNEL_RU")
    telegram_channel_fr: str = Field("", alias="TELEGRAM_CHANNEL_FR")
    telegram_channel_de: str = Field("", alias="TELEGRAM_CHANNEL_DE")
    telegram_channel_pl: str = Field("", alias="TELEGRAM_CHANNEL_PL")
    telegram_channel_pt: str = Field("", alias="TELEGRAM_CHANNEL_PT")
    telegram_channel_es: str = Field("", alias="TELEGRAM_CHANNEL_ES")
    deepl_api_key: str = Field("", alias="DEEPL_API_KEY")
    deepl_api_base: str = Field("https://api-free.deepl.com/v2", alias="DEEPL_API_BASE")
    publish_mode: str = Field("manual", alias="PUBLISH_MODE")
    publish_headline_image: bool = Field(default=False, alias="PUBLISH_HEADLINE_IMAGE")
    publish_deepl_fallback: bool = Field(default=False, alias="PUBLISH_DEEPL_FALLBACK")
    publish_metrics_window_hours: int = Field(default=24, alias="PUBLISH_METRICS_WINDOW_HOURS")
    publish_html_fallback_alert_pct: Decimal = Field(default=Decimal("15"), alias="PUBLISH_HTML_FALLBACK_ALERT_PCT")

    league_ids_raw: str = Field("39,78,140,135", alias="LEAGUE_IDS")
    season: int = Field(default_factory=_default_season, alias="SEASON")
    bookmaker_id: int = Field(1, alias="BOOKMAKER_ID")

    min_odd: Decimal = Field(Decimal("1.50"), alias="MIN_ODD")
    max_odd: Decimal = Field(Decimal("3.20"), alias="MAX_ODD")
    value_threshold: Decimal = Field(Decimal("0.05"), alias="VALUE_THRESHOLD")

    weight_short: Decimal = Field(Decimal("0.3"), alias="WEIGHT_SHORT")
    weight_long: Decimal = Field(Decimal("0.2"), alias="WEIGHT_LONG")
    weight_venue: Decimal = Field(Decimal("0.5"), alias="WEIGHT_VENUE")

    historical_season: int = Field(2023, alias="HISTORICAL_SEASON")
    historical_leagues_raw: str = Field("", alias="HISTORICAL_LEAGUES")
    historical_from: str = Field("", alias="HISTORICAL_FROM")
    historical_to: str = Field("", alias="HISTORICAL_TO")

    min_games_history: int = Field(3, alias="MIN_GAMES_HISTORY")
    fallback_home_odd: float = Field(0.0, alias="FALLBACK_HOME_ODD")

    # Legacy name: JOB_FETCH_FIXTURES_CRON used to control fixtures fetching.
    # Production now uses `sync_data`; prefer JOB_SYNC_DATA_CRON.
    job_fetch_fixtures_cron: str = Field("0 6 * * *", alias="JOB_FETCH_FIXTURES_CRON")
    job_sync_data_cron: str = Field("", alias="JOB_SYNC_DATA_CRON")
    job_compute_indices_cron: str = Field("0 7 * * *", alias="JOB_COMPUTE_INDICES_CRON")
    job_build_predictions_cron: str = Field("15 7 * * *", alias="JOB_BUILD_PREDICTIONS_CRON")
    job_evaluate_results_cron: str = Field("0 * * * *", alias="JOB_EVALUATE_RESULTS_CRON")
    job_maintenance_cron: str = Field("30 3 * * *", alias="JOB_MAINTENANCE_CRON")
    job_quality_report_cron: str = Field("30 6,23 * * *", alias="JOB_QUALITY_REPORT_CRON")
    job_fit_dixon_coles_cron: str = Field("5 6 * * *", alias="JOB_FIT_DIXON_COLES_CRON")
    job_fetch_historical_cron: str = Field("0 4 * * *", alias="JOB_FETCH_HISTORICAL_CRON")
    quality_report_cache_ttl_seconds: int = Field(default=12 * 3600, alias="QUALITY_REPORT_CACHE_TTL_SECONDS")

    fetch_rate_ms: int = Field(200, alias="FETCH_RATE_MS")
    stats_batch_limit: int = Field(200, alias="STATS_BATCH_LIMIT")
    backfill_days: int = Field(5, alias="BACKFILL_DAYS")
    backfill_rate_ms: int = Field(300, alias="BACKFILL_RATE_MS")
    injuries_ttl_days: int = Field(30, alias="INJURIES_TTL_DAYS")
    odds_freshness_close_within_minutes: int = Field(default=120, alias="ODDS_FRESHNESS_CLOSE_WITHIN_MINUTES")
    odds_freshness_close_minutes: int = Field(default=5, alias="ODDS_FRESHNESS_CLOSE_MINUTES")
    odds_freshness_soon_within_minutes: int = Field(default=720, alias="ODDS_FRESHNESS_SOON_WITHIN_MINUTES")
    odds_freshness_soon_minutes: int = Field(default=15, alias="ODDS_FRESHNESS_SOON_MINUTES")
    odds_freshness_default_hours: int = Field(default=3, alias="ODDS_FRESHNESS_DEFAULT_HOURS")
    sync_data_odds_lookahead_hours: int = Field(default=7 * 24, alias="SYNC_DATA_ODDS_LOOKAHEAD_HOURS")
    stale_ns_hide_hours: int = Field(default=6, alias="STALE_NS_HIDE_HOURS")
    admin_token: str = Field("", alias="ADMIN_TOKEN")

    @model_validator(mode="after")
    def validate_api_key(self):
        invalid_values = {"", "YOUR_KEY"}
        if self.api_football_key in invalid_values:
            logger = get_logger("settings")
            message = "API_FOOTBALL_KEY is not configured; sync_data will fail until it is set"
            logger.warning(message)
        return self

    @property
    def league_ids(self) -> List[int]:
        return [int(x.strip()) for x in self.league_ids_raw.split(",") if x.strip()]

    @property
    def telegram_channels(self) -> dict[str, int]:
        raw = {
            "en": self.telegram_channel_en,
            "uk": self.telegram_channel_uk,
            "ru": self.telegram_channel_ru,
            "fr": self.telegram_channel_fr,
            "de": self.telegram_channel_de,
            "pl": self.telegram_channel_pl,
            "pt": self.telegram_channel_pt,
            "es": self.telegram_channel_es,
        }
        out: dict[str, int] = {}
        for lang, val in raw.items():
            if not val:
                continue
            try:
                out[lang] = int(str(val).strip())
            except Exception:
                continue
        return out

    @property
    def historical_leagues(self) -> List[int]:
        return [int(x.strip()) for x in self.historical_leagues_raw.split(",") if x.strip()]

    @property
    def is_historical(self) -> bool:
        return self.app_mode.lower() == "historical"

    @property
    def is_live(self) -> bool:
        return self.app_mode.lower() == "live"

    @property
    def historical_mode(self) -> bool:
        return self.is_historical

    @property
    def value_threshold_dec(self) -> Decimal:
        return q_ev(self.value_threshold)

    @property
    def min_odd_dec(self) -> Decimal:
        return q_money(self.min_odd)

    @property
    def max_odd_dec(self) -> Decimal:
        return q_money(self.max_odd)

    @property
    def weights(self) -> tuple[Decimal, Decimal, Decimal]:
        return (
            q_prob(self.weight_short),
            q_prob(self.weight_long),
            q_prob(self.weight_venue),
        )

    elo_home_advantage: int = Field(default=65, alias="ELO_HOME_ADVANTAGE")
    elo_k_factor: int = Field(default=20, alias="ELO_K_FACTOR")
    elo_regression_factor: Decimal = Field(default=Decimal("0.67"), alias="ELO_REGRESSION_FACTOR")

    enable_elo: bool = Field(default=True, alias="ENABLE_ELO")
    enable_venue: bool = Field(default=True, alias="ENABLE_VENUE")
    enable_xg: bool = Field(default=True, alias="ENABLE_XG")
    enable_form: bool = Field(default=True, alias="ENABLE_FORM")
    enable_class: bool = Field(default=True, alias="ENABLE_CLASS")
    market_diff_threshold: Decimal = Field(default=Decimal("0.15"), alias="MARKET_DIFF_THRESHOLD")
    dc_use_xg: bool = Field(default=True, alias="DC_USE_XG")
    use_stacking: bool = Field(default=True, alias="USE_STACKING")
    use_dirichlet_calib: bool = Field(default=False, alias="USE_DIRICHLET_CALIB")
    enable_rest_adjustment: bool = Field(default=True, alias="ENABLE_REST_ADJUSTMENT")
    enable_kelly: bool = Field(default=False, alias="ENABLE_KELLY")
    kelly_fraction: str = Field(default="0.25", alias="KELLY_FRACTION")
    kelly_max_fraction: str = Field(default="0.05", alias="KELLY_MAX_FRACTION")
    enable_injuries: bool = Field(default=True, alias="ENABLE_INJURIES")
    enable_standings: bool = Field(default=True, alias="ENABLE_STANDINGS")
    enable_league_baselines: bool = Field(default=True, alias="ENABLE_LEAGUE_BASELINES")
    stats_max_attempts: int = Field(default=6, alias="STATS_MAX_ATTEMPTS")
    stats_retry_base_minutes: int = Field(default=30, alias="STATS_RETRY_BASE_MINUTES")
    stats_retry_max_minutes: int = Field(default=720, alias="STATS_RETRY_MAX_MINUTES")
    backtest_mode: bool = Field(default=False, alias="BACKTEST_MODE")
    backtest_current_date: Optional[str] = Field(default=None, alias="BACKTEST_CURRENT_DATE")
    backtest_kind: str = Field(default="pseudo", alias="BACKTEST_KIND")

    snapshot_autofill_enabled: bool = Field(default=False, alias="SNAPSHOT_AUTOFILL_ENABLED")
    snapshot_autofill_interval_minutes: int = Field(default=10, alias="SNAPSHOT_AUTOFILL_INTERVAL_MINUTES")
    snapshot_autofill_window_hours: int = Field(default=12, alias="SNAPSHOT_AUTOFILL_WINDOW_HOURS")
    snapshot_autofill_min_interval_minutes: int = Field(default=10, alias="SNAPSHOT_AUTOFILL_MIN_INTERVAL_MINUTES")
    snapshot_autofill_urgent_minutes: int = Field(default=60, alias="SNAPSHOT_AUTOFILL_URGENT_MINUTES")
    snapshot_autofill_trigger_before_minutes: int = Field(default=360, alias="SNAPSHOT_AUTOFILL_TRIGGER_BEFORE_MINUTES")
    snapshot_autofill_accel_due_gaps_threshold: int = Field(default=20, alias="SNAPSHOT_AUTOFILL_ACCEL_DUE_GAPS_THRESHOLD")
    snapshot_autofill_accel_trigger_before_minutes: int = Field(default=120, alias="SNAPSHOT_AUTOFILL_ACCEL_TRIGGER_BEFORE_MINUTES")

    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    allow_web_scheduler: bool = Field(default=False, alias="ALLOW_WEB_SCHEDULER")

    write_metrics_file: bool = Field(default=False, alias="WRITE_METRICS_FILE")
    metrics_output_path: str = Field(default="/tmp/metrics_eval.json", alias="METRICS_OUTPUT_PATH")

    api_cache_max_rows: int = Field(default=0, alias="API_CACHE_MAX_ROWS")
    job_runs_retention_days: int = Field(default=90, alias="JOB_RUNS_RETENTION_DAYS")
    odds_snapshots_retention_days: int = Field(default=0, alias="ODDS_SNAPSHOTS_RETENTION_DAYS")

    run_now_min_interval_seconds: int = Field(default=3, alias="RUN_NOW_MIN_INTERVAL_SECONDS")
    run_now_max_per_minute: int = Field(default=20, alias="RUN_NOW_MAX_PER_MINUTE")

    # Per-league betting controls
    # Comma-separated league IDs where 1X2 bets are allowed. Empty = all leagues.
    league_1x2_enabled_raw: str = Field(default="", alias="LEAGUE_1X2_ENABLED")
    # Per-league EV threshold overrides (format: "39:0.12,61:0.12")
    league_ev_threshold_overrides_raw: str = Field(default="39:0.12,61:0.12", alias="LEAGUE_EV_THRESHOLD_OVERRIDES")
    # Enable/disable TOTAL market bets globally
    enable_total_bets: bool = Field(default=True, alias="ENABLE_TOTAL_BETS")
    # EV threshold for TOTAL market (higher than 1X2 due to lower model edge)
    value_threshold_total: Decimal = Field(Decimal("0.12"), alias="VALUE_THRESHOLD_TOTAL")

    # New markets (test mode: all ON by default)
    enable_total_1_5_bets: bool = Field(default=True, alias="ENABLE_TOTAL_1_5_BETS")
    value_threshold_total_1_5: Decimal = Field(Decimal("0.12"), alias="VALUE_THRESHOLD_TOTAL_1_5")
    enable_total_3_5_bets: bool = Field(default=True, alias="ENABLE_TOTAL_3_5_BETS")
    value_threshold_total_3_5: Decimal = Field(Decimal("0.12"), alias="VALUE_THRESHOLD_TOTAL_3_5")
    enable_btts_bets: bool = Field(default=True, alias="ENABLE_BTTS_BETS")
    value_threshold_btts: Decimal = Field(Decimal("0.04"), alias="VALUE_THRESHOLD_BTTS")
    enable_double_chance_bets: bool = Field(default=True, alias="ENABLE_DOUBLE_CHANCE_BETS")
    value_threshold_double_chance: Decimal = Field(Decimal("0.03"), alias="VALUE_THRESHOLD_DOUBLE_CHANCE")
    max_total_bets_per_fixture: int = Field(default=1, alias="MAX_TOTAL_BETS_PER_FIXTURE")

    @property
    def league_1x2_enabled(self) -> List[int]:
        """List of league IDs where 1X2 bets are enabled. Empty list = all leagues allowed."""
        raw = (self.league_1x2_enabled_raw or "").strip()
        if not raw:
            return []
        return [int(x.strip()) for x in raw.split(",") if x.strip()]

    @property
    def league_ev_threshold_overrides(self) -> dict[int, Decimal]:
        """Per-league EV threshold overrides. E.g. {39: 0.12, 61: 0.12}."""
        overrides: dict[int, Decimal] = {}
        raw = (self.league_ev_threshold_overrides_raw or "").strip()
        if not raw:
            return overrides
        try:
            for pair in raw.split(","):
                if ":" not in pair:
                    continue
                lid_raw, val_raw = pair.split(":", 1)
                lid = int(lid_raw.strip())
                overrides[lid] = q_ev(Decimal(val_raw.strip()))
        except Exception:
            return {}
        return overrides

    @property
    def value_threshold_total_dec(self) -> Decimal:
        return q_ev(self.value_threshold_total)

    @property
    def value_threshold_total_1_5_dec(self) -> Decimal:
        return q_ev(self.value_threshold_total_1_5)

    @property
    def value_threshold_total_3_5_dec(self) -> Decimal:
        return q_ev(self.value_threshold_total_3_5)

    @property
    def value_threshold_btts_dec(self) -> Decimal:
        return q_ev(self.value_threshold_btts)

    @property
    def value_threshold_double_chance_dec(self) -> Decimal:
        return q_ev(self.value_threshold_double_chance)

    @property
    def sync_data_cron(self) -> str:
        v = (self.job_sync_data_cron or "").strip()
        return v or self.job_fetch_fixtures_cron


default_settings = Settings()
settings = default_settings
