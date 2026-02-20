def test_stats_bins(api_client):
    client = api_client
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "bins" in data
    assert isinstance(data["bins"], list)
