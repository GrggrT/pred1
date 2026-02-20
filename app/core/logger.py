import logging
import os
from typing import Optional


def _configure():
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn + apscheduler logs through root with the same formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "apscheduler"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level)

    # SQL logs are very noisy; keep them separate and quiet by default.
    sql_level_name = (os.getenv("SQL_LOG_LEVEL") or "WARNING").strip().upper()
    sql_level = getattr(logging, sql_level_name, logging.WARNING)
    sql_lg = logging.getLogger("sqlalchemy.engine")
    sql_lg.handlers.clear()
    sql_lg.propagate = True
    sql_lg.setLevel(sql_level)


_configure()


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "fatigue-chaos")


logger = get_logger()
