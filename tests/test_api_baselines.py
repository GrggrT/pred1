def test_league_baselines_endpoint(api_client):
    client = api_client
    # use defaults; may return not found if no data
    resp = client.get("/api/v1/league_baselines?league_id=39&season=2025")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        for key in ["league_id", "season", "date_key", "avg_home_xg", "avg_away_xg"]:
            assert key in data
