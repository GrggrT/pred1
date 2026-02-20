def test_standings_endpoint(api_client):
    resp = api_client.get("/api/v1/standings?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if not data:
        return
    row = data[0]
    for key in ["team_id", "team_name", "league_id", "season", "rank", "points", "updated_at"]:
        assert key in row

