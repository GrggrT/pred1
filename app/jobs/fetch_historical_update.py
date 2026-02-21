"""
Daily update job for historical data.
Wraps scripts/fetch_historical.py --update-today in an async job.
Runs after sync_data to pick up newly finished matches.
"""
from __future__ import annotations

import asyncio
import os
import sys

from app.core.logger import get_logger

log = get_logger("jobs.fetch_historical_update")


async def run(session=None):
    """Run fetch_historical.py --update-today as a subprocess."""
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
        "fetch_historical.py",
    )

    if not os.path.exists(script_path):
        log.warning("fetch_historical.py not found at %s", script_path)
        return {"status": "skipped", "reason": "script_not_found"}

    python = sys.executable
    cmd = [python, script_path, "--update-today"]

    log.info("fetch_historical_update starting: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""

        if proc.returncode == 0:
            log.info("fetch_historical_update completed: %s", output[-500:] if len(output) > 500 else output)
            return {"status": "ok", "returncode": 0}
        else:
            log.error("fetch_historical_update failed (rc=%d): %s", proc.returncode, output[-500:])
            return {"status": "error", "returncode": proc.returncode}
    except asyncio.TimeoutError:
        log.error("fetch_historical_update timed out after 600s")
        return {"status": "timeout"}
    except Exception as e:
        log.error("fetch_historical_update error: %s", e)
        return {"status": "error", "error": str(e)}
