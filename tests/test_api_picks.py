import pytest


def test_picks_fields(api_client):
    client = api_client
    resp = client.get("/api/v1/picks?limit=1")
    assert resp.status_code == 200
    data = resp.json()
    if not data:
        pytest.skip("no picks available")
    item = data[0]
    for key in ["signal_score", "prob_source", "value_threshold", "market_diff"]:
        assert key in item
