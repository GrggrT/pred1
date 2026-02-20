from typing import Optional


def normalize_status(short_status: Optional[str]) -> str:
    code = (short_status or "").upper()
    if not code:
        return "UNK"

    finished = {"FT", "AET", "PEN"}
    not_started = {"NS"}
    canceled = {"CANC", "ABD", "AWD", "WO"}
    postponed = {"PST"}
    suspended = {"SUSP"}
    in_play = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
    time_tbd = {"TBD"}
    # PENDING is an ambiguous status from API-Football that should be treated as NS
    # unless the match has already started/finished (handled by caller)
    pending = {"PENDING"}

    if code in finished:
        return code
    if code in not_started:
        return "NS"
    if code in canceled:
        return code
    if code in postponed:
        return "PST"
    if code in suspended:
        return "SUSP"
    if code in time_tbd:
        return "TBD"
    if code in in_play:
        return "LIVE"
    if code in pending:
        return "NS"  # Treat PENDING as NS; caller should determine actual status
    return "UNK"
