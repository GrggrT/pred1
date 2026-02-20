from app.data.mappers import normalize_status


def test_normalize_status_finished_variants():
    assert normalize_status("FT") == "FT"
    assert normalize_status("AET") == "AET"
    assert normalize_status("PEN") == "PEN"


def test_normalize_status_not_started_and_inplay():
    assert normalize_status("NS") == "NS"
    assert normalize_status("1H") == "LIVE"
    assert normalize_status("HT") == "LIVE"
    assert normalize_status("2H") == "LIVE"


def test_normalize_status_postponed_canceled_and_unknown():
    assert normalize_status("PST") == "PST"
    assert normalize_status("CANC") == "CANC"
    assert normalize_status("ABD") == "ABD"
    assert normalize_status("TBD") == "TBD"
    assert normalize_status(None) == "UNK"
    assert normalize_status("???") == "UNK"


def test_normalize_status_pending():
    # PENDING should be treated as NS - the caller should determine actual status
    assert normalize_status("PENDING") == "NS"
    assert normalize_status("pending") == "NS"

