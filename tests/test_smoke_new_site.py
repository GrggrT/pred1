"""E2E smoke tests for the new public site and admin panel.

Tests run against a live server at localhost:8000.
Skip if server is not running.
"""
import pytest
import urllib.request
import urllib.error
import json

BASE = "http://localhost:8000"
ADMIN_TOKEN = "dev"


def _get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", {}


def _server_up():
    try:
        status, _, _ = _get("/health")
        return status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="Server not running at localhost:8000")


# ============ Static file routes ============

class TestStaticRoutes:
    def test_public_site_html(self):
        status, body, _ = _get("/")
        assert status == 200
        assert "Football Value Betting" in body
        assert "pub-nav" in body

    def test_public_css(self):
        status, body, _ = _get("/public.css")
        assert status == 200
        assert "pub-header" in body

    def test_public_js(self):
        status, body, _ = _get("/public.js")
        assert status == 200
        assert "loadHome" in body

    def test_admin_html(self):
        status, body, _ = _get("/admin")
        assert status == 200
        assert "FVB Admin" in body
        assert "adm-nav" in body

    def test_admin_css(self):
        status, body, _ = _get("/admin/admin.css")
        assert status == 200
        assert "adm-sidebar" in body

    def test_admin_js(self):
        status, body, _ = _get("/admin/admin.js")
        assert status == 200
        assert "loadOperations" in body

    def test_shared_tokens(self):
        status, body, _ = _get("/shared/tokens.css")
        assert status == 200
        assert "--accent-primary" in body

    def test_legacy_ui(self):
        status, body, _ = _get("/ui")
        assert status == 200

    def test_health(self):
        status, body, _ = _get("/health")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True


# ============ Public API endpoints ============

class TestPublicAPI:
    def test_leagues(self):
        status, body, _ = _get("/api/public/v1/leagues")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        if data:
            league = data[0]
            assert "id" in league
            assert "name" in league
            assert "country" in league
            # Must NOT expose internal fields
            assert "signal_score" not in league

    def test_stats(self):
        status, body, _ = _get("/api/public/v1/stats")
        assert status == 200
        data = json.loads(body)
        assert "total_bets" in data
        assert "roi" in data
        assert "win_rate" in data
        assert "total_profit" in data
        assert "period_days" in data

    def test_stats_custom_days(self):
        status, body, _ = _get("/api/public/v1/stats?days=30")
        assert status == 200
        data = json.loads(body)
        assert data["period_days"] == 30

    def test_matches(self):
        status, body, _ = _get("/api/public/v1/matches")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        if data:
            m = data[0]
            assert "fixture_id" in m
            assert "home" in m
            assert "away" in m
            assert "kickoff" in m
            assert "pick" in m
            assert "odd" in m
            # Security: internal fields must NOT be exposed
            for forbidden in ["feature_flags", "signal_score", "market_diff", "prob_source", "value_index"]:
                assert forbidden not in m, f"Internal field '{forbidden}' leaked in public API"

    def test_matches_pagination(self):
        status, body, _ = _get("/api/public/v1/matches?limit=2&offset=0")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        assert len(data) <= 2

    def test_matches_league_filter(self):
        status, body, _ = _get("/api/public/v1/matches?league_id=39")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        for m in data:
            assert m["league_id"] == 39

    def test_results(self):
        status, body, _ = _get("/api/public/v1/results")
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        if data:
            r = data[0]
            assert "fixture_id" in r
            assert "status" in r
            assert "profit" in r
            assert r["status"] in ("WIN", "LOSS", "VOID")
            # Security check
            for forbidden in ["feature_flags", "signal_score", "market_diff"]:
                assert forbidden not in r

    def test_matches_cache_control(self):
        status, _, headers = _get("/api/public/v1/matches")
        assert status == 200
        cc = headers.get("Cache-Control", headers.get("cache-control", ""))
        assert "public" in cc
        assert "max-age" in cc


# ============ Security headers ============

class TestSecurityHeaders:
    def test_public_csp(self):
        status, _, headers = _get("/")
        assert status == 200
        csp = headers.get("Content-Security-Policy", headers.get("content-security-policy", ""))
        assert "frame-ancestors" in csp

    def test_admin_csp(self):
        status, _, headers = _get("/admin")
        assert status == 200
        csp = headers.get("Content-Security-Policy", headers.get("content-security-policy", ""))
        assert "frame-ancestors" in csp

    def test_x_content_type_options(self):
        status, _, headers = _get("/")
        assert headers.get("X-Content-Type-Options", headers.get("x-content-type-options", "")) == "nosniff"

    def test_x_frame_options(self):
        status, _, headers = _get("/")
        assert headers.get("X-Frame-Options", headers.get("x-frame-options", "")) == "DENY"

    def test_referrer_policy(self):
        status, _, headers = _get("/")
        rp = headers.get("Referrer-Policy", headers.get("referrer-policy", ""))
        assert "strict-origin" in rp


# ============ Admin API auth ============

class TestAdminAuth:
    def test_admin_api_requires_auth(self):
        status, _, _ = _get("/api/v1/picks")
        assert status == 403

    def test_admin_api_with_auth(self):
        status, body, _ = _get("/api/v1/meta", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_admin_dashboard(self):
        status, body, _ = _get("/api/v1/dashboard?days=30", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert status == 200
        data = json.loads(body)
        assert "kpis" in data

    def test_admin_freshness(self):
        status, body, _ = _get("/api/v1/freshness", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert status == 200

    def test_admin_jobs_status(self):
        status, body, _ = _get("/api/v1/jobs/status", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert status == 200


# ============ New site features ============

class TestNewSiteFeatures:
    def test_admin_js_has_caching(self):
        status, body, _ = _get("/admin/admin.js")
        assert "cacheGet" in body
        assert "cacheSet" in body
        assert "CACHE_TTL" in body

    def test_admin_js_has_confirm_dialog(self):
        status, body, _ = _get("/admin/admin.js")
        assert "showConfirm" in body
        assert "DANGEROUS_JOBS" in body

    def test_admin_js_has_audit_log(self):
        status, body, _ = _get("/admin/admin.js")
        assert "renderAuditLog" in body

    def test_admin_js_has_shortcuts(self):
        status, body, _ = _get("/admin/admin.js")
        assert "showShortcutsHelp" in body
        assert "sectionMap" in body

    def test_admin_js_has_connection_check(self):
        status, body, _ = _get("/admin/admin.js")
        assert "checkConnection" in body

    def test_admin_html_has_audit_section(self):
        status, body, _ = _get("/admin")
        assert "sys-audit" in body

    def test_public_js_has_league_breakdown(self):
        status, body, _ = _get("/public.js")
        assert "renderLeagueBreakdown" in body

    def test_public_js_has_status_badge(self):
        status, body, _ = _get("/public.js")
        assert "matchStatusLabel" in body

    def test_public_has_og_meta(self):
        status, body, _ = _get("/")
        assert 'og:title' in body
        assert 'og:description' in body

    def test_public_has_jsonld(self):
        status, body, _ = _get("/")
        assert 'application/ld+json' in body
        assert 'schema.org' in body
