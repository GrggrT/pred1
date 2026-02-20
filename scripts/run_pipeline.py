import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def load_env_file(path: str):
    if not path:
        return
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            os.environ[str(k)] = str(v)
    else:
        # Fallback: simple KEY=VALUE per line
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()


async def run_pipeline():
    from app.core.db import SessionLocal, init_db
    from app.core.http import init_http_clients, close_http_clients
    from app.jobs import sync_data, compute_indices, build_predictions, evaluate_results

    await init_db()
    await init_http_clients()
    try:
        async with SessionLocal() as s:
            await sync_data.run(s)
            await compute_indices.run(s)
            await build_predictions.run(s)
            await evaluate_results.run(s)
    finally:
        await close_http_clients()

async def run_backtest(date_from: str, date_to: str):
    from datetime import datetime, timedelta

    from app.core.config import settings
    from app.core.db import SessionLocal, init_db
    from app.core.http import init_http_clients, close_http_clients
    from app.jobs import sync_data, compute_indices, build_predictions, evaluate_results

    os.environ["BACKTEST_MODE"] = "true"
    settings.backtest_mode = True
    settings.backtest_kind = (os.getenv("BACKTEST_KIND") or "pseudo").strip().lower()

    d_start = datetime.fromisoformat(date_from)
    d_end = datetime.fromisoformat(date_to)
    await init_db()
    await init_http_clients()

    try:
        cur = d_start
        while cur <= d_end:
            day_str = cur.date().isoformat()
            os.environ["BACKTEST_CURRENT_DATE"] = day_str
            settings.backtest_current_date = day_str
            print(f"Backtest day {day_str}")
            async with SessionLocal() as s:
                await sync_data.run(s)
                await compute_indices.run(s)
                await build_predictions.run(s)
                await evaluate_results.run(s)
            cur += timedelta(days=1)
    finally:
        await close_http_clients()


def main():
    parser = argparse.ArgumentParser(description="Run live pipeline with optional feature-ablation/backtest config")
    parser.add_argument("--config", help="Path to env-like file or JSON with overrides (e.g. disable ELO)", default=None)
    parser.add_argument("--backtest-mode", action="store_true", help="Enable backtest day-by-day mode")
    parser.add_argument("--backtest-kind", default="pseudo", help="pseudo | true (true = no fresh odds fetch)")
    parser.add_argument("--date-from", help="YYYY-MM-DD for backtest start")
    parser.add_argument("--date-to", help="YYYY-MM-DD for backtest end")
    args = parser.parse_args()
    if args.config:
        load_env_file(args.config)
    if args.backtest_mode:
        if args.date_from and args.date_to:
            start = args.date_from
            end = args.date_to
        else:
            raise SystemExit("backtest requires --date-from and --date-to")
        kind = (args.backtest_kind or "pseudo").strip().lower()
        if kind not in {"pseudo", "true"}:
            raise SystemExit("--backtest-kind must be pseudo or true")
        os.environ["BACKTEST_KIND"] = kind
        asyncio.run(run_backtest(start, end))
    else:
        asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
