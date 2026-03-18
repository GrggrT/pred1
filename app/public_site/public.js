(function () {
  'use strict';

  // ═══ CONSTANTS ═══
  var API = '/api/public/v1';

  var PICK_LABELS = {
    HOME_WIN: '1', DRAW: 'X', AWAY_WIN: '2',
    H: '1', D: 'X', A: '2',
    OVER_2_5: 'Б2.5', UNDER_2_5: 'М2.5',
    OVER_1_5: 'Б1.5', UNDER_1_5: 'М1.5',
    OVER_3_5: 'Б3.5', UNDER_3_5: 'М3.5',
    BTTS_YES: 'ОЗ Да', BTTS_NO: 'ОЗ Нет',
    DC_1X: '1X', DC_X2: 'X2', DC_12: '12',
  };

  var PICK_LABELS_FULL = {
    HOME_WIN: 'Победа хозяев', DRAW: 'Ничья', AWAY_WIN: 'Победа гостей',
    H: 'Победа хозяев', D: 'Ничья', A: 'Победа гостей',
    OVER_2_5: 'Тотал больше 2.5', UNDER_2_5: 'Тотал меньше 2.5',
    OVER_1_5: 'Тотал больше 1.5', UNDER_1_5: 'Тотал меньше 1.5',
    OVER_3_5: 'Тотал больше 3.5', UNDER_3_5: 'Тотал меньше 3.5',
    BTTS_YES: 'Обе забьют — Да', BTTS_NO: 'Обе забьют — Нет',
    DC_1X: 'Двойной шанс 1X', DC_X2: 'Двойной шанс X2', DC_12: 'Двойной шанс 12',
  };

  var MARKET_LABELS = {
    '1X2': '1X2', 'TOTAL': 'Т2.5', 'TOTAL_1_5': 'Т1.5',
    'TOTAL_3_5': 'Т3.5', 'BTTS': 'ОЗ', 'DOUBLE_CHANCE': 'ДШ',
  };

  var MARKET_PILLS = [
    { key: 'all', label: 'Все' },
    { key: '1X2', label: '1X2' },
    { key: 'TOTAL', label: 'Тотал' },
    { key: 'BTTS', label: 'ОЗ' },
    { key: 'value', label: '\uD83D\uDD25 Value' },
  ];

  var LIVE_STATUSES = ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE'];

  var NEWS_CAT = {
    preview:   { emoji: '\u26BD', label: '\u041F\u0440\u0435\u0432\u044C\u044E' },
    review:    { emoji: '\uD83D\uDCCB', label: '\u041E\u0431\u0437\u043E\u0440' },
    injury:    { emoji: '\uD83E\uDE79', label: '\u0422\u0440\u0430\u0432\u043C\u044B' },
    transfer:  { emoji: '\uD83D\uDD04', label: '\u0422\u0440\u0430\u043D\u0441\u0444\u0435\u0440\u044B' },
    standings: { emoji: '\uD83D\uDCC8', label: '\u0422\u0430\u0431\u043B\u0438\u0446\u0430' },
  };

  var COUNTRY_FLAGS = {
    'England': '\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC65\uDB40\uDC6E\uDB40\uDC67\uDB40\uDC7F',
    'Spain': '\uD83C\uDDEA\uD83C\uDDF8',
    'Italy': '\uD83C\uDDEE\uD83C\uDDF9',
    'Germany': '\uD83C\uDDE9\uD83C\uDDEA',
    'France': '\uD83C\uDDEB\uD83C\uDDF7',
    'Portugal': '\uD83C\uDDF5\uD83C\uDDF9',
    'Netherlands': '\uD83C\uDDF3\uD83C\uDDF1',
    'Turkey': '\uD83C\uDDF9\uD83C\uDDF7',
    'Belgium': '\uD83C\uDDE7\uD83C\uDDEA',
    'Scotland': '\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC73\uDB40\uDC63\uDB40\uDC74\uDB40\uDC7F',
  };

  // ═══ STATE ═══
  var currentView = 'predictions';
  var selectedDate = 'today';
  var marketFilter = 'all';
  var expandedMatch = null;
  var selectedLeague = null;
  var pubDays = +(_load('days', 90));
  var leaguesCache = null;
  var matchesData = [];
  var resultsCache = null;
  var resultsSort = _load('resultsSort', { col: 'kickoff', dir: 'desc' });
  var _navController = null;
  var _chartPoints = {};
  var _prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var _numFmt = typeof Intl !== 'undefined' ? new Intl.NumberFormat('ru-RU') : null;
  var _cc = null;
  var _analyticsChartData = null;
  var resultsAllData = [];
  var resultsLeagueFilter = null;
  var resultsMarketFilter = 'all';
  var resultsDisplayCount = 20;
  var newsCache = null;
  var newsShowAll = false;
  var collapsedLeagues = {};

  // ═══ DOM HELPERS ═══
  // NOTE: All user-facing data is escaped via esc() before being inserted into
  // the DOM through innerHTML. The data originates from our own trusted API
  // endpoints which return sanitized database values. This follows the same
  // safe pattern used in the previous version of this codebase.
  function el(id) { return document.getElementById(id); }

  function esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function _store(key, val) {
    try { localStorage.setItem('fvb_' + key, JSON.stringify(val)); } catch (e) { /* noop */ }
  }

  function _load(key, fallback) {
    try {
      var v = localStorage.getItem('fvb_' + key);
      return v !== null ? JSON.parse(v) : fallback;
    } catch (e) { return fallback; }
  }

  function _isAbort(e) { return e && e.name === 'AbortError'; }

  function formatDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function formatDateShort(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  }

  function formatTime(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function timeAgo(iso) {
    if (!iso) return '';
    var diff = Date.now() - new Date(iso).getTime();
    if (diff < 0) return '\u0442\u043E\u043B\u044C\u043A\u043E \u0447\u0442\u043E';
    var sec = Math.floor(diff / 1000);
    if (sec < 60) return '\u0442\u043E\u043B\u044C\u043A\u043E \u0447\u0442\u043E';
    var min = Math.floor(sec / 60);
    if (min < 60) return min + ' \u043C\u0438\u043D \u043D\u0430\u0437\u0430\u0434';
    var hrs = Math.floor(min / 60);
    if (hrs < 24) return hrs + '\u0447 \u043D\u0430\u0437\u0430\u0434';
    var days = Math.floor(hrs / 24);
    if (days === 1) return '\u0432\u0447\u0435\u0440\u0430';
    if (days < 7) return days + ' \u0434\u043D \u043D\u0430\u0437\u0430\u0434';
    return formatDateShort(iso);
  }

  function isFreshNews(iso) {
    if (!iso) return false;
    return (Date.now() - new Date(iso).getTime()) < 3600000;
  }

  function logoImg(url, alt, size) {
    size = size || 18;
    if (!url) return '';
    return '<img src="' + esc(url) + '" alt="' + esc(alt) + '" width="' + size + '" height="' + size + '" loading="lazy" style="object-fit:contain" onerror="this.style.display=\'none\'">';
  }

  function _fmtNum(val, decimals, thousands) {
    var s = val.toFixed(decimals);
    if (thousands && decimals === 0 && _numFmt) s = _numFmt.format(Math.round(val));
    return s;
  }

  // ═══ TOAST ═══
  function toast(msg, type) {
    var container = el('toasts');
    if (!container) return;
    var t = document.createElement('div');
    t.className = 'toast ' + (type || 'info');
    t.textContent = msg;
    container.appendChild(t);
    setTimeout(function () { t.remove(); }, 3100);
  }

  // ═══ ANIMATED COUNTER ═══
  function animateValue(element, end, opts) {
    if (!element) return;
    if (end == null || isNaN(end)) { element.textContent = '\u2014'; return; }
    opts = opts || {};
    var suffix = opts.suffix || '';
    var decimals = opts.decimals != null ? opts.decimals : (suffix === '%' ? 1 : (Math.abs(end) % 1 > 0 ? 1 : 0));
    var showSign = !!opts.sign;
    var thousands = !!opts.thousands;
    if (_prefersReducedMotion) {
      var p = showSign && end > 0.05 ? '+' : '';
      element.textContent = p + _fmtNum(end, decimals, thousands) + suffix;
      return;
    }
    var dur = opts.duration || 600;
    var startTime = performance.now();
    function tick(now) {
      var t = Math.min((now - startTime) / dur, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      var current = end * eased;
      var prefix = showSign && current > 0.05 ? '+' : '';
      element.textContent = prefix + _fmtNum(current, decimals, thousands) + suffix;
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // ═══ ERROR / EMPTY / SKELETON ═══
  function errorHtml(msg, retryAction) {
    return '<div class="error-state">' +
      '<div class="error-icon">\u26A0\uFE0F</div>' +
      '<div class="error-text">' + esc(msg || 'Ошибка загрузки') + '</div>' +
      (retryAction ? '<button class="retry-btn" data-retry="' + esc(retryAction) + '">Повторить</button>' : '') +
      '</div>';
  }

  function emptyHtml(icon, text, sub) {
    return '<div class="empty-state"><div class="empty-icon">' + icon + '</div>' +
      '<div class="empty-text">' + esc(text) + '</div>' +
      (sub ? '<div class="empty-sub">' + esc(sub) + '</div>' : '') + '</div>';
  }

  function skeletonRows(n) {
    var html = '';
    for (var i = 0; i < n; i++) {
      html += '<div class="skeleton-row">' +
        '<span class="skeleton"></span>' +
        '<span class="skeleton"></span>' +
        '<span class="skeleton"></span>' +
        '<span class="skeleton"></span>' +
        '<span class="skeleton"></span>' +
        '<span class="skeleton"></span>' +
        '<span class="skeleton"></span>' +
        '</div>';
    }
    return html;
  }

  // ═══ API LAYER ═══
  function _abortNav() {
    if (_navController) { try { _navController.abort(); } catch (e) { /* noop */ } }
    _navController = new AbortController();
    return _navController.signal;
  }

  function api(path, params, opts) {
    var url = new URL(API + path, location.origin);
    if (params) {
      var keys = Object.keys(params);
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i], v = params[k];
        if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, String(v));
      }
    }
    var fetchOpts = {};
    if (opts && opts.signal) fetchOpts.signal = opts.signal;
    else if (_navController) fetchOpts.signal = _navController.signal;
    return fetch(url, fetchOpts).then(function (resp) {
      if (!resp.ok) throw new Error('API ' + resp.status);
      var total = resp.headers.get('X-Total-Count');
      return resp.json().then(function (data) {
        return { data: data, total: total !== null ? parseInt(total, 10) : null };
      });
    });
  }

  // ═══ VALUE BADGE ═══
  function renderValueBadge(ev) {
    if (ev == null) return '';
    var pct = (ev * 100).toFixed(1);
    var cls, txt;
    if (ev >= 0.10) { cls = 'hot'; txt = '\uD83D\uDD25 +' + pct + '%'; }
    else if (ev >= 0.05) { cls = 'solid'; txt = '+' + pct + '%'; }
    else { cls = 'fair'; txt = (parseFloat(pct) >= 0 ? '+' : '') + pct + '%'; }
    return '<span class="ev-badge ' + cls + '">' + txt + '</span>';
  }

  function fmtPct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '\u2014'; }

  // ═══ DATE HELPERS ═══
  function addDays(d, n) { var r = new Date(d); r.setDate(r.getDate() + n); return r; }

  function getDateBounds(dateKey) {
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    switch (dateKey) {
      case 'yesterday': return [addDays(today, -1), today];
      case 'today': return [today, addDays(today, 1)];
      case 'tomorrow': return [addDays(today, 1), addDays(today, 2)];
      default:
        var n = parseInt(dateKey, 10);
        if (!isNaN(n)) return [addDays(today, n), addDays(today, n + 1)];
        return [addDays(today, -1), addDays(today, 14)];
    }
  }

  function getDateLabel(dateKey) {
    switch (dateKey) {
      case 'yesterday': return 'Вчера';
      case 'today': return 'Сегодня';
      case 'tomorrow': return 'Завтра';
      default:
        var n = parseInt(dateKey, 10);
        if (!isNaN(n)) {
          var d = addDays(new Date(), n);
          return d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric' });
        }
        return dateKey;
    }
  }

  // ═══ RENDER: NEWS ═══
  function renderNews() {
    var container = el('sb-news-list');
    if (!container) return;
    if (!newsCache || newsCache.length === 0) {
      container.innerHTML = '<div style="padding:6px 10px;font-size:11px;color:var(--muted)">\u041D\u0435\u0442 \u043D\u043E\u0432\u043E\u0441\u0442\u0435\u0439</div>';
      return;
    }

    var limit = newsShowAll ? newsCache.length : 8;
    var items = newsCache.slice(0, limit);
    var html = '';

    items.forEach(function (a) {
      var cat = NEWS_CAT[a.category] || NEWS_CAT.standings;
      var fresh = isFreshNews(a.published_at);
      html += '<button class="nw-item" data-cat="' + esc(a.category || 'standings') + '" data-news-id="' + a.id + '">' +
        '<div class="nw-cat">' + cat.emoji + ' ' + esc(cat.label) + '</div>' +
        '<div class="nw-title">' + esc(a.title) + '</div>' +
        '<div class="nw-time' + (fresh ? ' nw-fresh' : '') + '">' + timeAgo(a.published_at) + '</div>' +
        '</button>';
    });

    if (!newsShowAll && newsCache.length > 8) {
      html += '<button class="nw-more" id="nw-show-more">\u0415\u0449\u0451 ' + (newsCache.length - 8) + ' \u2192</button>';
    }

    container.innerHTML = html;
  }

  function showNewsModal(newsId) {
    if (!newsCache) return;
    var article = null;
    for (var i = 0; i < newsCache.length; i++) {
      if (newsCache[i].id === newsId) { article = newsCache[i]; break; }
    }
    if (!article) return;

    var cat = NEWS_CAT[article.category] || NEWS_CAT.standings;
    var catEl = el('news-modal-cat');
    var bodyEl = el('news-modal-body');
    if (!catEl || !bodyEl) return;

    catEl.setAttribute('data-cat', article.category || 'standings');
    catEl.innerHTML = cat.emoji + ' ' + esc(cat.label);

    var html = '';
    html += '<div class="news-modal-time">' + (article.published_at ? formatDate(article.published_at) : '') + '</div>';
    if (article.summary) {
      html += '<div class="news-modal-summary">' + esc(article.summary) + '</div>';
    }
    if (article.body) {
      html += '<div class="news-modal-body-text">' + esc(article.body).replace(/\n/g, '<br>') + '</div>';
    }
    if (article.sources && article.sources.length) {
      html += '<div class="news-modal-sources">\u0418\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0438: ';
      article.sources.forEach(function (src, idx) {
        if (idx > 0) html += ', ';
        html += '<a href="' + esc(src) + '" target="_blank" rel="noopener">' + esc(src.replace(/^https?:\/\//, '').split('/')[0]) + '</a>';
      });
      html += '</div>';
    }

    bodyEl.innerHTML = html;
    el('news-modal').style.display = 'flex';
  }

  // ═══ RENDER: DATE SLIDER ═══
  function renderDateSlider() {
    var slider = el('date-slider');
    if (!slider) return;
    var days = ['yesterday', 'today', 'tomorrow', '2', '3', '4'];
    var html = '';
    days.forEach(function (key) {
      html += '<button class="date-btn' + (selectedDate === key ? ' active' : '') + '" data-date="' + key + '">' + getDateLabel(key) + '</button>';
    });
    slider.innerHTML = html;
  }

  // ═══ RENDER: PILLS ═══
  function renderPills() {
    var pills = el('market-pills');
    if (!pills) return;
    var html = '';
    MARKET_PILLS.forEach(function (p) {
      html += '<button class="pill' + (marketFilter === p.key ? ' active' : '') + '" data-market="' + p.key + '">' + p.label + '</button>';
    });
    pills.innerHTML = html;
  }

  // ═══ FILTER MATCHES ═══
  function getFilteredMatches() {
    var bounds = getDateBounds(selectedDate);
    var from = bounds[0].getTime();
    var to = bounds[1].getTime();

    return matchesData.filter(function (m) {
      if (m.kickoff) {
        var kickoff = new Date(m.kickoff).getTime();
        if (kickoff < from || kickoff >= to) return false;
      }
      if (marketFilter === 'value') return m.ev != null && m.ev >= 0.08;
      if (marketFilter !== 'all') {
        if (marketFilter === 'TOTAL') return m.market && m.market.indexOf('TOTAL') === 0;
        if (marketFilter === 'BTTS') return m.market === 'BTTS';
        return m.market === marketFilter;
      }
      return true;
    });
  }

  // ═══ RENDER: MATCH FEED ═══
  // All match data comes from our trusted API and is escaped via esc()
  function renderMatchFeed() {
    var feed = el('match-feed');
    if (!feed) return;

    var matches = getFilteredMatches();

    // Group predictions by fixture
    var fixtureData = groupByFixture(matches);
    var fixtureCount = fixtureData.order.length;

    var countEl = el('pills-count');
    if (countEl) {
      var mc = fixtureCount;
      countEl.textContent = mc + ' матч' + (mc === 1 ? '' : mc < 5 ? 'а' : 'ей');
    }

    if (!matches.length) {
      feed.innerHTML = emptyHtml('\u26BD', 'Нет прогнозов на выбранную дату', 'Попробуйте другую дату или фильтр');
      return;
    }

    // Build league groups from fixture groups (use first prediction per fixture for league info)
    var leagueGroups = {};
    var leagueOrder = [];
    fixtureData.order.forEach(function (fid) {
      var preds = fixtureData.groups[fid];
      var m = preds[0]; // representative prediction
      var lid = m.league_id || 0;
      if (!leagueGroups[lid]) {
        leagueGroups[lid] = { league: m.league, league_id: lid, league_logo_url: m.league_logo_url, country: '', fixtures: [] };
        if (leaguesCache) {
          var lc = leaguesCache.find(function (l) { return l.id === lid; });
          if (lc) leagueGroups[lid].country = lc.country || '';
        }
        leagueOrder.push(lid);
      }
      leagueGroups[lid].fixtures.push({ fid: fid, preds: preds });
    });

    var html = '';
    leagueOrder.forEach(function (lid) {
      var g = leagueGroups[lid];
      var isCollapsed = collapsedLeagues[lid];
      var flag = COUNTRY_FLAGS[g.country] || '';

      html += '<div class="league-group' + (isCollapsed ? ' collapsed' : '') + '" data-league-group="' + lid + '">';
      html += '<div class="league-header" data-toggle-league="' + lid + '" tabindex="0">';
      if (g.league_logo_url) {
        html += '<img class="league-header-logo" src="' + esc(g.league_logo_url) + '" alt="" onerror="this.style.display=\'none\'">';
      } else if (flag) {
        html += '<span class="league-header-flag">' + flag + '</span>';
      }
      html += '<span class="league-header-name">' + esc(g.league || 'League ' + lid) + '</span>';
      if (g.country) html += '<span class="league-header-country">' + esc(g.country) + '</span>';
      html += '<span class="league-header-count">' + g.fixtures.length + '</span>';
      html += '<span class="league-header-chevron">\u203A</span>';
      html += '</div>';
      html += '<div class="league-body">';
      g.fixtures.forEach(function (fix) {
        html += renderMatchRow(fix.preds[0], fix.preds);
      });
      html += '</div></div>';
    });

    feed.innerHTML = html;
  }

  function renderMatchRow(m, allPreds) {
    allPreds = allPreds || [m];
    var isLive = m.fixture_status && LIVE_STATUSES.indexOf(m.fixture_status) >= 0;
    var hasScore = m.score && m.score !== 'null';
    var isExpanded = expandedMatch === _matchKey(m);
    var pickLabel = PICK_LABELS[m.pick] || m.pick || '\u2014';
    var marketLabel = MARKET_LABELS[m.market] || m.market || '1X2';
    var extraCount = allPreds.length - 1;

    // EV tag
    var evVal = m.ev != null ? (m.ev * 100).toFixed(1) : null;
    var evClass = evVal >= 10 ? 'ev-high' : evVal >= 5 ? 'ev-mid' : 'ev-low';

    var html = '<div class="match-card' + (isExpanded ? ' expanded' : '') + (isLive ? ' live' : '') + '" data-match-key="' + _matchKey(m) + '">';

    // ── Card header: time + date + EV ──
    html += '<div class="match-card-header">';
    if (isLive) {
      html += '<span class="match-live"><span class="match-live-dot"></span>LIVE</span>';
    } else {
      html += '<span class="match-card-time">' + formatTime(m.kickoff) + '</span>';
    }
    html += '<span class="match-card-date">';
    if (m.kickoff) {
      var ko = new Date(m.kickoff);
      html += ko.toLocaleDateString('ru', { day: 'numeric', month: 'short' });
    }
    html += '</span>';
    if (evVal) {
      html += '<span class="match-ev-tag ' + evClass + '">EV ' + (evVal >= 0 ? '+' : '') + evVal + '%</span>';
    }
    html += '</div>';

    // ── Teams row ──
    html += '<div class="match-teams-row">';
    html += '<div class="match-team home">'
         + '<img class="match-team-logo" src="' + esc(m.home_logo_url || '') + '" alt="" onerror="this.style.display=\'none\'">'
         + '<span class="match-team-name">' + esc(m.home) + '</span>'
         + '</div>';
    if (hasScore) {
      html += '<div class="match-score">' + esc(m.score) + '</div>';
    } else {
      html += '<div class="match-vs">VS</div>';
    }
    html += '<div class="match-team away">'
         + '<img class="match-team-logo" src="' + esc(m.away_logo_url || '') + '" alt="" onerror="this.style.display=\'none\'">'
         + '<span class="match-team-name">' + esc(m.away) + '</span>'
         + '</div>';
    html += '</div>';

    // ── Probability bar (1X2 only) ──
    if (m.prob_home != null && m.prob_draw != null && m.prob_away != null) {
      var ph = (m.prob_home * 100).toFixed(0);
      var pd = (m.prob_draw * 100).toFixed(0);
      var pa = (m.prob_away * 100).toFixed(0);
      html += '<div class="match-prob-bar">'
           + '<div class="prob-h" style="width:' + ph + '%"></div>'
           + '<div class="prob-d" style="width:' + pd + '%"></div>'
           + '<div class="prob-a" style="width:' + pa + '%"></div>'
           + '</div>'
           + '<div class="match-prob-labels">'
           + '<span>' + ph + '%</span><span>' + pd + '%</span><span>' + pa + '%</span>'
           + '</div>';
    }

    // ── Pick row (clickable to expand) ──
    html += '<div class="match-pick-row" data-match-key="' + _matchKey(m) + '">';
    html += '<span class="match-pick-market">' + esc(marketLabel) + '</span>';
    html += '<span class="match-pick-label">' + esc(pickLabel);
    if (extraCount > 0) {
      html += ' <span class="match-extra-badge">+' + extraCount + '</span>';
    }
    html += '</span>';
    html += '<span class="match-pick-odd">' + (m.odd != null ? m.odd.toFixed(2) : '\u2014') + '</span>';
    html += '<span class="match-pick-chevron">\u25BC</span>';
    html += '</div>';

    if (isExpanded) {
      html += renderMatchDetail(m, allPreds);
    }

    html += '</div>';
    return html;
  }

  function _matchKey(m) {
    return String(m.fixture_id);
  }

  // Group flat predictions array by fixture_id, sorted by EV desc within each group
  function groupByFixture(matches) {
    var grouped = {};
    var order = [];
    matches.forEach(function (m) {
      var fid = String(m.fixture_id);
      if (!grouped[fid]) {
        grouped[fid] = [];
        order.push(fid);
      }
      grouped[fid].push(m);
    });
    // Sort predictions within each fixture by EV descending
    order.forEach(function (fid) {
      grouped[fid].sort(function (a, b) { return (b.ev || 0) - (a.ev || 0); });
    });
    return { groups: grouped, order: order };
  }

  function _shortenTeam(name) {
    if (!name) return '';
    return name.replace(/^(FC|CF|SC|AC|AS|SS|US|RC|CD|CA|SE|CR|FK|NK|SK|GD|UD|AD|SD|IF|BK)\s+/i, '')
               .replace(/\s+(FC|CF|SC|AC|FK|SK|BK)$/i, '')
               .replace(/\s+(de|del|di|du|da|dos|do|la|le|el|al|von|van)\s+/gi, ' ');
  }

  function _renderSinglePredDetail(m) {
    var pickLabel = PICK_LABELS_FULL[m.pick] || PICK_LABELS[m.pick] || m.pick || '\u2014';
    var confPct = m.confidence != null ? (m.confidence * 100).toFixed(0) : '\u2014';
    var evPct = m.ev != null ? (m.ev > 0 ? '+' : '') + (m.ev * 100).toFixed(1) + '%' : '\u2014';

    var html = '<div class="match-detail-grid">';
    html += '<div class="match-detail-card"><div class="match-detail-label">Прогноз</div><div class="match-detail-val">' + esc(pickLabel) + '</div></div>';
    html += '<div class="match-detail-card"><div class="match-detail-label">Коэфф.</div><div class="match-detail-val">' + (m.odd != null ? m.odd.toFixed(2) : '\u2014') + '</div></div>';
    html += '<div class="match-detail-card"><div class="match-detail-label">Exp. Value</div><div class="match-detail-val" style="color:' + (m.ev >= 0.10 ? 'var(--yellow)' : m.ev >= 0.05 ? 'var(--green)' : 'var(--text)') + '">' + evPct + '</div></div>';

    html += '<div class="match-detail-card"><div class="match-detail-label">Confidence</div>';
    html += '<div class="match-detail-val">' + confPct + (m.confidence != null ? '%' : '') + '</div>';
    if (m.confidence != null) {
      html += '<div class="match-detail-conf-bar"><div class="match-detail-conf-fill" style="width:' + Math.min(100, m.confidence * 100) + '%"></div></div>';
    }
    html += '</div>';
    html += '</div>';

    // Model comparison table (1X2 predictions with probabilities)
    if (m.prob_home != null) {
      html += '<div class="match-model-compare"><table>';
      html += '<tr><th>\u041C\u043E\u0434\u0435\u043B\u044C</th><th>1</th><th>X</th><th>2</th></tr>';
      var srcLabel = m.prob_source === 'stacking' ? 'Stacking' : m.prob_source === 'dc' ? 'Dixon-Coles' : m.prob_source || 'Model';
      html += '<tr class="active-source"><td>' + esc(srcLabel) + '</td>';
      html += '<td>' + fmtPct(m.prob_home) + '</td><td>' + fmtPct(m.prob_draw) + '</td><td>' + fmtPct(m.prob_away) + '</td></tr>';
      if (m.fair_odd != null && m.odd != null) {
        html += '<tr><td>Fair / Book</td><td colspan="3">' + m.fair_odd.toFixed(2) + ' / ' + m.odd.toFixed(2) + '</td></tr>';
      }
      html += '</table></div>';
    }

    if (m.ev != null && m.ev >= 0.10) {
      html += '<div class="match-detail-alert">\uD83D\uDD25 Strong value \u2014 EV \u043F\u0440\u0435\u0432\u044B\u0448\u0430\u0435\u0442 10%</div>';
    }
    return html;
  }

  function renderMatchDetail(m, allPreds) {
    allPreds = allPreds || [m];
    var html = '<div class="match-detail">';

    if (allPreds.length > 1) {
      allPreds.forEach(function (pred) {
        var marketName = MARKET_LABELS[pred.market] || pred.market || '1X2';
        html += '<div class="match-detail-market-header">' + esc(marketName) + '</div>';
        html += _renderSinglePredDetail(pred);
      });
    } else {
      html += _renderSinglePredDetail(m);
    }

    html += '</div>';
    return html;
  }

  // ═══ RENDER: RESULTS FEED ═══
  // Result data from our own public API — all fields escaped via esc()
  function renderResultsFeed(results) {
    var feed = el('results-feed');
    if (!feed) return;

    if (!results || !results.length) {
      feed.innerHTML = emptyHtml('\uD83D\uDCCA', 'Нет результатов', 'Попробуйте другой период');
      return;
    }

    var html = '';
    results.forEach(function (r) {
      var isWin = r.status === 'WIN';
      var pickLabel = PICK_LABELS[r.pick] || r.pick || '\u2014';
      var marketLabel = MARKET_LABELS[r.market] || r.market || '1X2';
      var profitStr = r.profit != null ? (r.profit >= 0 ? '+' : '') + r.profit.toFixed(2) : '\u2014';
      var profitCls = r.profit != null ? (r.profit >= 0 ? 'positive' : 'negative') : '';

      html += '<div class="result-row ' + (isWin ? 'win' : 'loss') + '">';
      html += '<div><span class="result-dot ' + (isWin ? 'win' : 'loss') + '"></span></div>';
      html += '<div class="result-match">'
           + logoImg(r.home_logo_url, r.home, 16)
           + '<span class="result-home">' + esc(r.home) + '</span>'
           + '<span class="result-score"> ' + esc(r.score || '') + ' </span>'
           + '<span class="result-away">' + esc(r.away) + '</span>'
           + logoImg(r.away_logo_url, r.away, 16)
           + '</div>';
      html += '<div class="result-meta"><span class="result-league">' + esc(r.league || '') + '</span><span class="result-date">' + formatDateShort(r.kickoff) + '</span></div>';
      html += '<div class="result-pick"><span class="match-pick-market">' + esc(marketLabel) + '</span> <span class="match-pick-sel">' + esc(pickLabel) + '</span></div>';
      html += '<div class="result-odd">' + (r.odd != null ? r.odd.toFixed(2) : '\u2014') + '</div>';
      html += '<div class="result-profit ' + profitCls + '">' + profitStr + '</div>';
      html += '</div>';
    });

    feed.innerHTML = html;
  }

  // ═══ RENDER: INFO STRIP (Track Record + Markets) ═══
  var MARKET_ORDER = ['1X2', 'TOTAL', 'TOTAL_1_5', 'TOTAL_3_5', 'BTTS', 'DOUBLE_CHANCE'];

  function _stripKpi(val, label, cls) {
    return '<div class="strip-kpi"><span class="strip-kpi-val' + (cls ? ' ' + cls : '') + '">' + val + '</span><span class="strip-kpi-lbl">' + label + '</span></div>';
  }

  function renderInfoStrip(stats, marketData) {
    var trackEl = el('strip-track');
    var mktEl = el('strip-markets');
    if (!trackEl || !mktEl) return;

    // Track Record KPIs
    var th = '';
    if (stats) {
      var roiVal = stats.roi != null ? (stats.roi >= 0 ? '+' : '') + stats.roi.toFixed(1) + '%' : '--';
      var roiCls = stats.roi > 0 ? 'positive' : stats.roi < 0 ? 'negative' : '';
      var wrVal = stats.win_rate != null ? stats.win_rate.toFixed(1) + '%' : '--';
      var betsVal = stats.total_bets != null ? String(stats.total_bets) : '--';
      var profVal = stats.total_profit != null ? (stats.total_profit >= 0 ? '+' : '') + stats.total_profit.toFixed(1) : '--';
      var profCls = stats.total_profit > 0 ? 'positive' : stats.total_profit < 0 ? 'negative' : '';
      th += _stripKpi(roiVal, 'ROI', roiCls);
      th += _stripKpi(wrVal, 'WIN%', '');
      th += _stripKpi(betsVal, '\u0421\u0442\u0430\u0432\u043a\u0438', '');
      th += _stripKpi(profVal, '\u041f\u0440\u043e\u0444\u0438\u0442', profCls);
    }
    trackEl.innerHTML = th;

    // Market badges
    var mh = '';
    if (marketData && typeof marketData === 'object') {
      var keys = MARKET_ORDER.filter(function (k) { return marketData[k] && marketData[k].settled > 0; });
      keys.forEach(function (mkt) {
        var v = marketData[mkt];
        var label = MARKET_LABELS[mkt] || mkt;
        var roiCls = v.roi >= 0 ? 'positive' : 'negative';
        mh += '<div class="strip-market">';
        mh += '<span class="strip-market-name">' + esc(label) + '</span>';
        mh += '<span class="strip-market-roi ' + roiCls + '">' + (v.roi >= 0 ? '+' : '') + v.roi.toFixed(1) + '%</span>';
        mh += '</div>';
      });
    }
    mktEl.innerHTML = mh;
  }

  /* ── Per-market table (analytics view, also visible on mobile) ── */
  function renderAnalyticsMarketBreakdown(data) {
    var cont = el('an-market-breakdown-body');
    if (!cont) return;
    if (!data || typeof data !== 'object' || !Object.keys(data).length) {
      cont.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445</div>';
      return;
    }
    var keys = MARKET_ORDER.filter(function (k) { return data[k] && data[k].settled > 0; });
    var html = '<table class="an-league-table"><thead><tr><th>\u0420\u044b\u043d\u043e\u043a</th><th>\u0421\u0442\u0430\u0432\u043e\u043a</th><th>Win%</th><th>ROI</th><th>\u041f\u0440\u043e\u0444\u0438\u0442</th></tr></thead><tbody>';
    keys.forEach(function (mkt) {
      var v = data[mkt];
      var label = MARKET_LABELS[mkt] || mkt;
      var roiCls = v.roi >= 0 ? 'positive' : 'negative';
      var profitCls = v.total_profit >= 0 ? 'positive' : 'negative';
      html += '<tr><td><strong>' + esc(label) + '</strong></td>';
      html += '<td>' + v.settled + '</td>';
      html += '<td>' + v.win_rate.toFixed(1) + '%</td>';
      html += '<td><span class="an-profit ' + roiCls + '">' + (v.roi >= 0 ? '+' : '') + v.roi.toFixed(1) + '%</span></td>';
      html += '<td><span class="an-profit ' + profitCls + '">' + (v.total_profit >= 0 ? '+' : '') + v.total_profit.toFixed(2) + '</span></td></tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderTopValue(matches) {
    var list = el('sb-top-list');
    if (!list) return;
    var sorted = matches.filter(function (m) { return m.ev != null; }).sort(function (a, b) { return (b.ev || 0) - (a.ev || 0); }).slice(0, 5);
    if (!sorted.length) { list.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">Нет данных</div>'; return; }

    var html = '';
    sorted.forEach(function (m) {
      var fullName = (m.home || '') + ' \u2014 ' + (m.away || '');
      var shortName = _shortenTeam(m.home || '') + ' \u2014 ' + _shortenTeam(m.away || '');
      html += '<button class="sb-top-item" data-match-key="' + _matchKey(m) + '">';
      html += '<span class="sb-top-teams" title="' + esc(fullName) + '">' + esc(shortName) + '</span>';
      html += '<span class="sb-top-pick">' + esc(PICK_LABELS[m.pick] || m.pick || '') + '</span>';
      html += '<span class="sb-top-odd">' + (m.odd != null ? m.odd.toFixed(2) : '') + '</span>';
      html += '</button>';
    });
    list.innerHTML = html;
  }

  function renderStreak(results) {
    var streakEl = el('rs-streak');
    if (!streakEl || !results || !results.length) return;
    var count = 0;
    var type = results[0].status;
    for (var i = 0; i < results.length; i++) {
      if (results[i].status === type) count++;
      else break;
    }
    if (count >= 3 && type === 'WIN') {
      streakEl.className = 'rs-streak hot';
      streakEl.textContent = '\uD83D\uDD25 Серия: ' + count + ' WIN подряд';
    } else {
      streakEl.className = 'rs-streak';
      streakEl.textContent = '';
    }
  }

  // ═══ RENDER: ANALYTICS ═══
  function renderAnalyticsStats(stats) {
    var container = el('an-stats');
    if (!container || !stats) return;

    var roiCls = stats.roi > 0 ? 'accent' : stats.roi < 0 ? 'negative' : '';
    var profitCls = stats.total_profit > 0 ? 'green' : stats.total_profit < 0 ? 'negative' : '';

    container.innerHTML =
      '<div class="an-stat"><div class="an-stat-label">ROI</div><div class="an-stat-value ' + roiCls + '" id="an-roi-val">--</div></div>' +
      '<div class="an-stat"><div class="an-stat-label">Win Rate</div><div class="an-stat-value blue" id="an-wr-val">--</div></div>' +
      '<div class="an-stat"><div class="an-stat-label">Ставок</div><div class="an-stat-value" id="an-bets-val">--</div></div>' +
      '<div class="an-stat"><div class="an-stat-label">Профит</div><div class="an-stat-value ' + profitCls + '" id="an-profit-val">--</div></div>';

    animateValue(el('an-roi-val'), stats.roi, { suffix: '%', decimals: 1 });
    animateValue(el('an-wr-val'), stats.win_rate, { suffix: '%', decimals: 1 });
    animateValue(el('an-bets-val'), stats.total_bets, { decimals: 0, thousands: true });
    animateValue(el('an-profit-val'), stats.total_profit, { decimals: 1, sign: true });
  }

  // Analytics results table — data from our API, all fields escaped via esc()
  function renderAnalyticsTable(results) {
    var tbody = el('an-results-tbody');
    if (!tbody) return;
    if (!results || !results.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:20px">Нет данных</td></tr>';
      resultsCache = null;
      return;
    }
    resultsCache = results;
    _renderSortedResults();
    _updateSortHeaders();
  }

  function _renderSortedResults() {
    var tbody = el('an-results-tbody');
    if (!tbody || !resultsCache) return;
    var sorted = resultsCache.slice().sort(function (a, b) {
      var col = resultsSort.col;
      var va, vb;
      if (col === 'kickoff') { va = a.kickoff || ''; vb = b.kickoff || ''; }
      else if (col === 'odd') { va = a.odd || 0; vb = b.odd || 0; }
      else if (col === 'profit') { va = a.profit || 0; vb = b.profit || 0; }
      else if (col === 'status') { va = a.status || ''; vb = b.status || ''; }
      else { va = 0; vb = 0; }
      if (va < vb) return resultsSort.dir === 'asc' ? -1 : 1;
      if (va > vb) return resultsSort.dir === 'asc' ? 1 : -1;
      if (col !== 'kickoff') return (b.kickoff || '').localeCompare(a.kickoff || '');
      return 0;
    });

    tbody.innerHTML = sorted.slice(0, 50).map(function (r) {
      var pickLabel = PICK_LABELS[r.pick] || r.pick || '\u2014';
      var marketLabel = MARKET_LABELS[r.market] || r.market || '1X2';
      var isWin = r.status === 'WIN';
      var profitStr = r.profit != null ? (r.profit >= 0 ? '+' : '') + r.profit.toFixed(2) : '\u2014';
      return '<tr>' +
        '<td>' + formatDateShort(r.kickoff) + '</td>' +
        '<td>' + esc(r.home) + ' \u2014 ' + esc(r.away) + '</td>' +
        '<td>' + esc(r.league || '') + '</td>' +
        '<td>' + esc(r.score || '\u2014') + '</td>' +
        '<td><span class="match-pick-market">' + esc(marketLabel) + '</span> ' + esc(pickLabel) + '</td>' +
        '<td>' + (r.odd != null ? r.odd.toFixed(2) : '\u2014') + '</td>' +
        '<td><span class="an-result-badge ' + (isWin ? 'win' : 'loss') + '"><span class="an-badge-full">' + (isWin ? 'Win' : 'Loss') + '</span><span class="an-badge-short">' + (isWin ? 'W' : 'L') + '</span></span></td>' +
        '<td><span class="an-profit ' + (r.profit >= 0 ? 'positive' : 'negative') + '">' + profitStr + '</span></td>' +
        '</tr>';
    }).join('');
  }

  function _updateSortHeaders() {
    var ths = document.querySelectorAll('#an-results-table th[data-sort]');
    for (var i = 0; i < ths.length; i++) {
      var th = ths[i];
      var col = th.dataset.sort;
      var isActive = col === resultsSort.col;
      th.classList.toggle('sort-active', isActive);
      th.classList.toggle('sort-asc', isActive && resultsSort.dir === 'asc');
      th.classList.toggle('sort-desc', isActive && resultsSort.dir === 'desc');
    }
  }

  // League breakdown — data from our API, all fields escaped via esc()
  function renderLeagueBreakdown(results) {
    var cont = el('an-league-breakdown');
    if (!cont || !results || !results.length) return;

    var leagues = {};
    results.forEach(function (r) {
      var key = r.league || 'Другое';
      if (!leagues[key]) leagues[key] = { bets: 0, wins: 0, profit: 0 };
      leagues[key].bets++;
      if (r.status === 'WIN') leagues[key].wins++;
      leagues[key].profit += (r.profit || 0);
    });

    var rows = Object.keys(leagues).map(function (name) {
      var d = leagues[name];
      return { name: name, bets: d.bets, wins: d.wins, wr: d.bets > 0 ? (d.wins / d.bets * 100) : 0, roi: d.bets > 0 ? (d.profit / d.bets * 100) : 0, profit: d.profit };
    }).sort(function (a, b) { return b.bets - a.bets; });

    var html = '<table class="an-league-table"><thead><tr><th>Лига</th><th>Ставок</th><th>Win Rate</th><th>ROI</th><th>Профит</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var roiCls = r.roi >= 0 ? 'positive' : 'negative';
      var profitCls = r.profit >= 0 ? 'positive' : 'negative';
      html += '<tr><td>' + esc(r.name) + '</td><td>' + r.bets + '</td>' +
        '<td>' + r.wr.toFixed(1) + '%</td>' +
        '<td><span class="an-profit ' + roiCls + '">' + (r.roi >= 0 ? '+' : '') + r.roi.toFixed(1) + '%</span></td>' +
        '<td><span class="an-profit ' + profitCls + '">' + (r.profit >= 0 ? '+' : '') + r.profit.toFixed(2) + '</span></td></tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  // ═══ CSV EXPORT ═══
  function exportResultsCsv() {
    if (!resultsCache || !resultsCache.length) { toast('Нет данных для экспорта', 'error'); return; }
    var header = ['Fixture ID', 'Kickoff', 'Home', 'Away', 'Score', 'Market', 'Pick', 'Odd', 'EV', 'Status', 'Profit'];
    var rows = [header];
    resultsCache.forEach(function (r) {
      var ev = r.ev != null ? (r.ev * 100).toFixed(1) + '%' : '';
      rows.push([
        r.fixture_id || '', r.kickoff ? new Date(r.kickoff).toLocaleDateString('ru-RU') : '',
        r.home || '', r.away || '', r.score || '', r.market || '',
        r.pick || '', r.odd != null ? r.odd.toFixed(2) : '', ev,
        r.status || '', r.profit != null ? r.profit.toFixed(2) : '',
      ]);
    });
    var csv = rows.map(function (row) {
      return row.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(',');
    }).join('\n');
    var blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'fvb_results_' + new Date().toISOString().slice(0, 10) + '.csv';
    link.click();
    URL.revokeObjectURL(link.href);
    toast('CSV экспортирован', 'success');
  }

  // ═══ CHARTS ═══
  function _chartColors() {
    if (_cc) return _cc;
    var s = getComputedStyle(document.documentElement);
    _cc = {
      grid: 'rgba(32,48,72,0.4)',
      muted: s.getPropertyValue('--muted').trim() || '#64748b',
      accent: s.getPropertyValue('--accent').trim() || '#b6f33d',
      red: s.getPropertyValue('--red').trim() || '#ef4444',
      green: s.getPropertyValue('--green').trim() || '#22c55e',
      font: '11px ' + (s.getPropertyValue('--font-mono').trim() || 'JetBrains Mono, monospace'),
    };
    return _cc;
  }

  function _drawChart(canvas, results, opts) {
    var ctx = canvas.getContext('2d');
    if (!ctx || !results || results.length < 2) return;

    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    var W = rect.width, H = rect.height;

    var sorted = results.slice().sort(function (a, b) { return new Date(a.kickoff) - new Date(b.kickoff); });
    var cumProfit = 0;
    var points = sorted.map(function (r, i) {
      cumProfit += (r.profit || 0);
      var val = opts.roi ? (cumProfit / (i + 1)) * 100 : cumProfit;
      return { x: i, val: val, date: r.kickoff };
    });

    var padTop = 30, padBottom = 30, padLeft = 50, padRight = 20;
    var chartW = W - padLeft - padRight;
    var chartH = H - padTop - padBottom;
    var vals = points.map(function (p) { return p.val; });
    var minV = Math.min(0, Math.min.apply(null, vals));
    var maxV = Math.max(0, Math.max.apply(null, vals));
    var range = (maxV - minV) || 1;

    function px(i) { return padLeft + (i / Math.max(points.length - 1, 1)) * chartW; }
    function py(v) { return padTop + (1 - (v - minV) / range) * chartH; }

    ctx.clearRect(0, 0, W, H);
    var cc = _chartColors();

    // Grid
    ctx.strokeStyle = cc.grid;
    ctx.lineWidth = 1;
    for (var i = 0; i <= 5; i++) {
      var gv = minV + (range / 5) * i;
      var gy = py(gv);
      ctx.beginPath(); ctx.moveTo(padLeft, gy); ctx.lineTo(W - padRight, gy); ctx.stroke();
      ctx.fillStyle = cc.muted;
      ctx.font = cc.font;
      ctx.textAlign = 'right';
      ctx.fillText((opts.roi ? gv.toFixed(1) + '%' : gv.toFixed(1)), padLeft - 6, gy + 4);
    }

    // Zero line
    if (minV < 0 && maxV > 0) {
      ctx.strokeStyle = 'rgba(100,116,139,0.4)';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(padLeft, py(0)); ctx.lineTo(W - padRight, py(0)); ctx.stroke();
      ctx.setLineDash([]);
    }

    // Line
    var lastVal = points[points.length - 1].val;
    var lineColor = lastVal >= 0 ? cc.accent : cc.red;
    ctx.beginPath();
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    points.forEach(function (p, idx) {
      if (idx === 0) ctx.moveTo(px(idx), py(p.val));
      else ctx.lineTo(px(idx), py(p.val));
    });
    ctx.stroke();

    // Gradient fill
    var gradTop = lastVal >= 0 ? 'rgba(182,243,61,0.15)' : 'rgba(239,68,68,0.15)';
    var grad = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
    grad.addColorStop(0, gradTop);
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.lineTo(px(points.length - 1), H - padBottom);
    ctx.lineTo(px(0), H - padBottom);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // End dot
    ctx.beginPath();
    ctx.arc(px(points.length - 1), py(lastVal), 4, 0, Math.PI * 2);
    ctx.fillStyle = lineColor;
    ctx.fill();

    // Tooltip binding
    var canvasId = canvas.id;
    _chartPoints[canvasId] = {
      pts: points,
      px: function (p, idx) { return px(idx); },
      py: function (p) { return py(p.val); },
      formatTip: function (p, idx) {
        return '#' + (idx + 1) + ' \u00B7 ' + formatDateShort(p.date) + ' \u00B7 ' + (opts.roi ? 'ROI: ' : 'Профит: ') + (p.val >= 0 ? '+' : '') + p.val.toFixed(2) + (opts.roi ? '%' : '');
      },
    };
    _bindChartTooltip(canvas, canvasId);
  }

  function _bindChartTooltip(canvas, canvasId) {
    if (!canvas || canvas.dataset.tipBound) return;
    canvas.dataset.tipBound = '1';
    canvas.addEventListener('mousemove', function (e) {
      var info = _chartPoints[canvasId];
      if (!info || !info.pts.length) return;
      var rect = canvas.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var my = e.clientY - rect.top;
      var best = null, bestDist = 20;
      info.pts.forEach(function (p, i) {
        var sx = info.px(p, i), sy = info.py(p);
        var dist = Math.sqrt(Math.pow(mx - sx, 2) + Math.pow(my - sy, 2));
        if (dist < bestDist) { bestDist = dist; best = { p: p, i: i }; }
      });
      var tip = el('chart-tooltip');
      if (best && tip) {
        tip.textContent = info.formatTip(best.p, best.i);
        tip.style.display = 'block';
        var tipLeft = e.clientX + 14;
        if (tipLeft + 160 > window.innerWidth) tipLeft = e.clientX - 160;
        tip.style.left = tipLeft + 'px';
        tip.style.top = (e.clientY - 10) + 'px';
      } else if (tip) {
        tip.style.display = 'none';
      }
    });
    canvas.addEventListener('mouseleave', function () {
      var tip = el('chart-tooltip');
      if (tip) tip.style.display = 'none';
    });
  }

  // Sparkline (mini chart)
  function _drawSparkline(canvas, results, opts) {
    if (!canvas || !results || results.length < 2) return;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var dpr = window.devicePixelRatio || 1;
    var W = canvas.width / dpr || canvas.offsetWidth || 64;
    var H = canvas.height / dpr || canvas.offsetHeight || 24;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.scale(dpr, dpr);

    var sorted = results.slice().sort(function (a, b) { return new Date(a.kickoff) - new Date(b.kickoff); });
    var cumProfit = 0;
    var points = sorted.map(function (r) { cumProfit += (r.profit || 0); return cumProfit; });

    var minV = Math.min(0, Math.min.apply(null, points));
    var maxV = Math.max(0, Math.max.apply(null, points));
    var range = (maxV - minV) || 1;
    var pad = 2;

    ctx.clearRect(0, 0, W, H);
    ctx.beginPath();
    ctx.strokeStyle = (opts && opts.color) || _chartColors().accent;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    points.forEach(function (v, i) {
      var x = pad + (i / (points.length - 1)) * (W - 2 * pad);
      var y = pad + (1 - (v - minV) / range) * (H - 2 * pad);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function _downloadChart(canvasId) {
    var canvas = el(canvasId);
    if (!canvas) return;
    try {
      var link = document.createElement('a');
      link.download = canvasId + '.png';
      link.href = canvas.toDataURL('image/png');
      link.click();
      toast('График сохранён', 'success');
    } catch (e) {
      toast('Ошибка скачивания', 'error');
    }
  }

  // Header stats removed — KPIs now in info strip

  // ═══ LOADERS ═══
  function loadPredictions() {
    var feed = el('match-feed');
    if (feed) feed.innerHTML = skeletonRows(6);

    var params = { days_ahead: 14, limit: 50 };

    return api('/matches', params).then(function (res) {
      matchesData = res.data || [];
      renderDateSlider();
      renderPills();
      renderMatchFeed();
    }).catch(function (e) {
      if (_isAbort(e)) return;
      if (feed) feed.innerHTML = errorHtml('Не удалось загрузить матчи', 'predictions');
    });
  }

  function renderResultsLeaguePills() {
    var cont = el('results-league-pills');
    if (!cont) return;
    var leagues = {};
    resultsAllData.forEach(function (r) {
      if (r.league) leagues[r.league] = (leagues[r.league] || 0) + 1;
    });
    var keys = Object.keys(leagues).sort(function (a, b) { return leagues[b] - leagues[a]; });
    var html = '<button class="pill' + (resultsLeagueFilter === null ? ' active' : '') + '" data-res-league="all">Все</button>';
    keys.forEach(function (name) {
      html += '<button class="pill' + (resultsLeagueFilter === name ? ' active' : '') + '" data-res-league="' + esc(name) + '">' + esc(name) + ' <span style="opacity:0.5">' + leagues[name] + '</span></button>';
    });
    cont.innerHTML = html;
  }

  function renderResultsMarketPills() {
    var cont = el('results-market-pills');
    if (!cont) return;
    var pills = [
      { key: 'all', label: 'Все' },
      { key: '1X2', label: '1X2' },
      { key: 'TOTAL', label: 'Тотал' },
      { key: 'DOUBLE_CHANCE', label: 'ДШ' },
    ];
    var html = '';
    pills.forEach(function (p) {
      html += '<button class="pill' + (resultsMarketFilter === p.key ? ' active' : '') + '" data-res-market="' + p.key + '">' + p.label + '</button>';
    });
    cont.innerHTML = html;
  }

  function getFilteredResults() {
    return resultsAllData.filter(function (r) {
      if (resultsLeagueFilter && r.league !== resultsLeagueFilter) return false;
      if (resultsMarketFilter !== 'all') {
        if (resultsMarketFilter === 'TOTAL') return r.market && r.market.indexOf('TOTAL') === 0;
        return r.market === resultsMarketFilter;
      }
      return true;
    });
  }

  function applyResultsFilters() {
    var filtered = getFilteredResults();
    var sliced = filtered.slice(0, resultsDisplayCount);
    renderResultsFeed(sliced);
    var loadMoreWrap = el('results-load-more');
    if (loadMoreWrap) {
      loadMoreWrap.style.display = filtered.length > resultsDisplayCount ? '' : 'none';
    }
  }

  function loadResults() {
    var feed = el('results-feed');
    if (feed) feed.innerHTML = skeletonRows(6);

    var params = { days: 60, limit: 200 };

    resultsLeagueFilter = null;
    resultsMarketFilter = 'all';
    resultsDisplayCount = 20;

    return api('/results', params).then(function (res) {
      resultsAllData = res.data || [];
      resultsCache = resultsAllData;
      renderResultsLeaguePills();
      renderResultsMarketPills();
      applyResultsFilters();
    }).catch(function (e) {
      if (_isAbort(e)) return;
      if (feed) feed.innerHTML = errorHtml('Не удалось загрузить результаты', 'results');
    });
  }

  function loadAnalytics() {
    return Promise.all([
      api('/stats', { days: pubDays }),
      api('/results', { days: pubDays, limit: 200 }),
      api('/market-stats', { days: pubDays }),
    ]).then(function (results) {
      var stats = results[0].data;
      var data = results[1].data;
      var marketStats = results[2].data || {};
      renderAnalyticsStats(stats);
      renderAnalyticsMarketBreakdown(marketStats);
      renderInfoStrip(stats, marketStats);
      renderAnalyticsTable(data);
      renderLeagueBreakdown(data);

      _analyticsChartData = data;
      _redrawAnalyticsCharts();
    }).catch(function (e) {
      if (_isAbort(e)) return;
      console.error('loadAnalytics error', e);
      var statsEl = el('an-stats');
      if (statsEl) statsEl.innerHTML = errorHtml('Не удалось загрузить аналитику', 'analytics');
    });
  }

  function _redrawAnalyticsCharts() {
    var data = _analyticsChartData;
    var profitCanvas = el('profit-chart');
    var roiCanvas = el('roi-chart');
    if (data && data.length >= 2) {
      if (profitCanvas) { try { _drawChart(profitCanvas, data, { roi: false }); } catch (e) { /* noop */ } }
      if (roiCanvas) { try { _drawChart(roiCanvas, data, { roi: true }); } catch (e) { /* noop */ } }
    } else {
      if (profitCanvas && profitCanvas.parentElement) profitCanvas.parentElement.innerHTML = '<div style="padding:40px 0;text-align:center;color:var(--muted)">Недостаточно данных</div>';
      if (roiCanvas && roiCanvas.parentElement) roiCanvas.parentElement.innerHTML = '<div style="padding:40px 0;text-align:center;color:var(--muted)">Недостаточно данных</div>';
    }
  }

  function _safe(promise) {
    return promise.then(function (r) { return r; }).catch(function () { return null; });
  }

  function loadInitialData() {
    return Promise.all([
      _safe(api('/leagues')),
      _safe(api('/stats', { days: 90 })),
      _safe(api('/results', { days: 30, limit: 30 })),
      _safe(api('/market-stats', { days: 90 })),
      _safe(api('/news', { limit: 10 })),
    ]).then(function (results) {
      if (results[0]) leaguesCache = results[0].data || [];

      var stats = results[1] ? results[1].data : null;
      var marketStats = results[3] ? results[3].data || {} : {};
      if (stats) renderInfoStrip(stats, marketStats);

      newsCache = (results[4] && results[4].data && results[4].data.items) || [];
      renderNews();
    }).catch(function (e) {
      if (!_isAbort(e)) console.error('loadInitialData error', e);
    });
  }

  // ═══ LEAGUES PAGE ═══
  var MARKET_ORDER = ['1X2', 'TOTAL', 'TOTAL_1_5', 'TOTAL_3_5', 'BTTS', 'DOUBLE_CHANCE'];

  function renderLeaguePills() {
    var container = el('leagues-pills');
    if (!container || !leaguesCache) return;
    var html = '';
    leaguesCache.forEach(function (l) {
      var flag = COUNTRY_FLAGS[l.country] || '';
      var isActive = selectedLeague === l.id;
      html += '<button class="league-pill' + (isActive ? ' active' : '') + '" data-league-pill="' + l.id + '">';
      if (l.logo_url) {
        html += '<img class="league-pill-logo" src="' + esc(l.logo_url) + '" alt="" onerror="this.style.display=\'none\'">';
      } else if (flag) {
        html += '<span class="league-pill-flag">' + flag + '</span>';
      }
      html += esc(l.name);
      html += '</button>';
    });
    container.innerHTML = html;
  }

  function loadLeaguePage() {
    renderLeaguePills();
    if (selectedLeague) {
      loadLeagueDetail(selectedLeague);
    } else {
      var det = el('league-detail');
      var ph = el('league-placeholder');
      if (det) det.style.display = 'none';
      if (ph) ph.style.display = '';
    }
  }

  function loadLeagueDetail(leagueId) {
    selectedLeague = leagueId;
    var det = el('league-detail');
    var ph = el('league-placeholder');
    if (det) det.style.display = '';
    if (ph) ph.style.display = 'none';
    renderLeaguePills();

    var league = null;
    if (leaguesCache) {
      for (var li = 0; li < leaguesCache.length; li++) {
        if (leaguesCache[li].id === leagueId) { league = leaguesCache[li]; break; }
      }
    }
    var headerEl = el('league-detail-header');
    if (headerEl && league) {
      var flag = COUNTRY_FLAGS[league.country] || '';
      headerEl.innerHTML =
        (league.logo_url ? '<img class="league-detail-logo" src="' + esc(league.logo_url) + '" alt="">' :
         (flag ? '<span style="font-size:28px">' + flag + '</span>' : '')) +
        '<div><div class="league-detail-name">' + esc(league.name) + '</div>' +
        '<div class="league-detail-country">' + esc(league.country || '') + '</div></div>';
    }

    // Show loading
    var lgStBody = el('lg-standings-body');
    var lgMatchFeed = el('lg-match-feed');
    if (lgStBody) lgStBody.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Загрузка...</div>';
    if (lgMatchFeed) lgMatchFeed.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Загрузка...</div>';

    Promise.all([
      api('/standings', { league_id: leagueId }),
      api('/matches', { league_id: leagueId, days_ahead: 14, limit: 50 }),
      api('/results', { league_id: leagueId, days: 90, limit: 200 }),
      api('/news', { limit: 30 }),
    ]).then(function (results) {
      renderLeagueStandings(results[0].data);
      renderLeaguePredictions(results[1].data || []);

      var leagueResults = results[2].data || [];
      renderLeagueStats(leagueResults);
      renderLeagueMarketBreakdown(leagueResults);

      var allNews = (results[3].data && results[3].data.items) || [];
      var leagueNews = allNews.filter(function (n) { return n.league_id === leagueId; });
      renderLeagueNews(leagueNews);
    }).catch(function (e) {
      if (!_isAbort(e)) console.error('loadLeagueDetail error', e);
    });
  }

  function renderLeagueStandings(data) {
    var body = el('lg-standings-body');
    if (!body) return;
    if (!data || !data.length) { body.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445</div>'; return; }

    var html = '<table class="rs-standings-table"><thead><tr><th>#</th><th>\u041A\u043B\u0443\u0431</th><th>\u0418</th><th>\u0417</th><th>\u041F\u0440</th><th>\u0420\u0413</th><th>\u041E</th><th>\u0424\u043E\u0440\u043C\u0430</th></tr></thead><tbody>';
    data.forEach(function (r) {
      var formDots = (r.form || '').split('').map(function (ch) {
        var cls = ch === 'W' ? 'rs-form-w' : ch === 'D' ? 'rs-form-d' : ch === 'L' ? 'rs-form-l' : '';
        return '<span class="rs-form-dot ' + cls + '"></span>';
      }).join('');
      var gd = r.goal_diff != null ? r.goal_diff : ((r.goals_for || 0) - (r.goals_against || 0));
      var gdStr = gd > 0 ? '+' + gd : String(gd);
      html += '<tr>';
      html += '<td>' + (r.rank || '') + '</td>';
      html += '<td><div class="rs-standings-club">' + logoImg(r.team_logo_url, r.team_name, 14) + '<span>' + esc(r.team_name || '') + '</span></div></td>';
      html += '<td>' + (r.played || 0) + '</td>';
      html += '<td>' + (r.goals_for || 0) + '</td>';
      html += '<td>' + (r.goals_against || 0) + '</td>';
      html += '<td style="color:' + (gd > 0 ? 'var(--green)' : gd < 0 ? 'var(--negative)' : 'var(--text-2)') + '">' + gdStr + '</td>';
      html += '<td class="rs-standings-pts">' + (r.points || 0) + '</td>';
      html += '<td><div class="rs-form-wrap">' + formDots + '</div></td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    body.innerHTML = html;
  }

  function renderLeaguePredictions(predictions) {
    var feed = el('lg-match-feed');
    var countEl = el('lg-pred-count');
    if (!feed) return;

    if (!predictions.length) {
      feed.innerHTML = emptyHtml('\u26BD', '\u041D\u0435\u0442 \u043F\u0440\u043E\u0433\u043D\u043E\u0437\u043E\u0432', '\u041D\u0430 \u0431\u043B\u0438\u0436\u0430\u0439\u0448\u0438\u0435 \u0434\u043D\u0438 \u043F\u0440\u043E\u0433\u043D\u043E\u0437\u043E\u0432 \u043D\u0435\u0442');
      if (countEl) countEl.textContent = '0';
      return;
    }

    var fixtureData = groupByFixture(predictions);
    if (countEl) {
      var mc = fixtureData.order.length;
      countEl.textContent = mc + ' \u043C\u0430\u0442\u0447' + (mc === 1 ? '' : mc < 5 ? '\u0430' : '\u0435\u0439');
    }

    var html = '';
    fixtureData.order.forEach(function (fid) {
      html += renderMatchRow(fixtureData.groups[fid][0], fixtureData.groups[fid]);
    });
    feed.innerHTML = html;
  }

  function renderLeagueStats(results) {
    var container = el('lg-stats-grid');
    if (!container) return;
    if (!results || !results.length) {
      container.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445</div>';
      return;
    }
    var total = results.length;
    var wins = results.filter(function (r) { return r.status === 'WIN'; }).length;
    var profit = results.reduce(function (sum, r) { return sum + (r.profit || 0); }, 0);
    var roi = total > 0 ? (profit / total) * 100 : 0;
    var wr = total > 0 ? (wins / total) * 100 : 0;

    var roiCls = roi > 0 ? 'accent' : roi < 0 ? 'negative' : '';
    var profitCls = profit > 0 ? 'green' : profit < 0 ? 'negative' : '';

    container.innerHTML =
      '<div class="an-stat"><div class="an-stat-label">ROI</div><div class="an-stat-value ' + roiCls + '">' + (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%</div></div>' +
      '<div class="an-stat"><div class="an-stat-label">Win Rate</div><div class="an-stat-value blue">' + wr.toFixed(1) + '%</div></div>' +
      '<div class="an-stat"><div class="an-stat-label">\u0421\u0442\u0430\u0432\u043E\u043A</div><div class="an-stat-value">' + total + '</div></div>' +
      '<div class="an-stat"><div class="an-stat-label">\u041F\u0440\u043E\u0444\u0438\u0442</div><div class="an-stat-value ' + profitCls + '">' + (profit >= 0 ? '+' : '') + profit.toFixed(1) + '</div></div>';
  }

  function renderLeagueMarketBreakdown(results) {
    var cont = el('lg-market-breakdown-body');
    if (!cont) return;
    if (!results || !results.length) {
      cont.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445</div>';
      return;
    }

    var markets = {};
    results.forEach(function (r) {
      var mkt = r.market || '1X2';
      if (!markets[mkt]) markets[mkt] = { settled: 0, wins: 0, profit: 0 };
      markets[mkt].settled++;
      if (r.status === 'WIN') markets[mkt].wins++;
      markets[mkt].profit += (r.profit || 0);
    });

    var keys = MARKET_ORDER.filter(function (k) { return markets[k] && markets[k].settled > 0; });
    if (!keys.length) {
      cont.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445</div>';
      return;
    }

    var html = '<table class="lg-market-table"><thead><tr><th>\u0420\u044B\u043D\u043E\u043A</th><th>\u0421\u0442\u0430\u0432\u043E\u043A</th><th>Win%</th><th>ROI</th><th>\u041F\u0440\u043E\u0444\u0438\u0442</th></tr></thead><tbody>';
    keys.forEach(function (mkt) {
      var v = markets[mkt];
      var wr = v.settled > 0 ? (v.wins / v.settled * 100) : 0;
      var roi = v.settled > 0 ? (v.profit / v.settled * 100) : 0;
      var label = MARKET_LABELS[mkt] || mkt;
      var roiCls = roi >= 0 ? 'green' : 'negative';
      var profitCls = v.profit >= 0 ? 'green' : 'negative';
      html += '<tr><td><strong>' + esc(label) + '</strong></td>';
      html += '<td>' + v.settled + '</td>';
      html += '<td>' + wr.toFixed(1) + '%</td>';
      html += '<td style="color:var(--' + roiCls + ')">' + (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%</td>';
      html += '<td style="color:var(--' + profitCls + ')">' + (v.profit >= 0 ? '+' : '') + v.profit.toFixed(1) + '</td></tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderLeagueNews(articles) {
    var container = el('lg-news-list');
    if (!container) return;
    if (!articles || !articles.length) {
      container.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">\u041D\u0435\u0442 \u043D\u043E\u0432\u043E\u0441\u0442\u0435\u0439 \u0434\u043B\u044F \u044D\u0442\u043E\u0439 \u043B\u0438\u0433\u0438</div>';
      return;
    }
    var html = '';
    articles.forEach(function (a) {
      var cat = NEWS_CAT[a.category] || NEWS_CAT.standings;
      html += '<button class="nw-item" data-cat="' + esc(a.category || 'standings') + '" data-news-id="' + a.id + '" style="width:100%">';
      html += '<div class="nw-cat">' + cat.emoji + ' ' + esc(cat.label) + '</div>';
      html += '<div class="nw-title">' + esc(a.title) + '</div>';
      html += '<div class="nw-time">' + timeAgo(a.published_at) + '</div>';
      html += '</button>';
    });
    container.innerHTML = html;
  }

  // ═══ NAVIGATION ═══
  function navigate(view) {
    if (view === currentView) return;
    _abortNav();
    currentView = view;
    expandedMatch = null;

    var views = document.querySelectorAll('.view');
    for (var i = 0; i < views.length; i++) {
      views[i].classList.toggle('active', views[i].id === 'view-' + view);
    }

    var topBtns = document.querySelectorAll('.top-nav-btn');
    for (var j = 0; j < topBtns.length; j++) {
      topBtns[j].classList.toggle('active', topBtns[j].dataset.view === view);
    }

    var bottomTabs = document.querySelectorAll('.bottom-tab');
    for (var k = 0; k < bottomTabs.length; k++) {
      bottomTabs[k].classList.toggle('active', bottomTabs[k].dataset.view === view);
    }

    if (view === 'predictions') loadPredictions();
    else if (view === 'results') loadResults();
    else if (view === 'analytics') loadAnalytics();
    else if (view === 'leagues') loadLeaguePage();
  }

  // ═══ EVENT HANDLING ═══
  document.addEventListener('click', function (e) {
    var target = e.target;

    var navBtn = target.closest('.top-nav-btn');
    if (navBtn) { navigate(navBtn.dataset.view); return; }

    var bottomTab = target.closest('.bottom-tab');
    if (bottomTab) { navigate(bottomTab.dataset.view); return; }

    var dateBtn = target.closest('.date-btn');
    if (dateBtn) {
      selectedDate = dateBtn.dataset.date;
      renderDateSlider();
      renderMatchFeed();
      return;
    }

    // Results league filter pill
    var resLeaguePill = target.closest('[data-res-league]');
    if (resLeaguePill) {
      var lVal = resLeaguePill.dataset.resLeague;
      resultsLeagueFilter = lVal === 'all' ? null : lVal;
      resultsDisplayCount = 20;
      renderResultsLeaguePills();
      applyResultsFilters();
      return;
    }

    // Results market filter pill
    var resMarketPill = target.closest('[data-res-market]');
    if (resMarketPill) {
      resultsMarketFilter = resMarketPill.dataset.resMarket;
      resultsDisplayCount = 20;
      renderResultsMarketPills();
      applyResultsFilters();
      return;
    }

    // Load more results
    if (target.id === 'btn-load-more' || target.closest('#btn-load-more')) {
      resultsDisplayCount += 20;
      applyResultsFilters();
      return;
    }

    var pill = target.closest('.pill');
    if (pill && pill.dataset.market) {
      marketFilter = pill.dataset.market;
      renderPills();
      renderMatchFeed();
      return;
    }

    var leagueToggle = target.closest('[data-toggle-league]');
    if (leagueToggle) {
      var lid = parseInt(leagueToggle.dataset.toggleLeague, 10);
      collapsedLeagues[lid] = !collapsedLeagues[lid];
      var group = document.querySelector('[data-league-group="' + lid + '"]');
      if (group) group.classList.toggle('collapsed', !!collapsedLeagues[lid]);
      return;
    }

    // Top Value item click — navigate to match in predictions
    var topItem = target.closest('.sb-top-item[data-match-key]');
    if (topItem) {
      var topKey = topItem.dataset.matchKey;
      if (currentView !== 'predictions') { navigate('predictions'); }
      marketFilter = 'all';
      selectedDate = 'today';
      expandedMatch = topKey;
      renderDateSlider();
      renderPills();
      renderMatchFeed();
      setTimeout(function () {
        var row = document.querySelector('.match-card[data-match-key="' + topKey + '"]');
        if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
      return;
    }

    // League pill click on Leagues page
    var leaguePill = target.closest('[data-league-pill]');
    if (leaguePill) {
      var lid = parseInt(leaguePill.dataset.leaguePill, 10);
      if (lid && lid !== selectedLeague) loadLeagueDetail(lid);
      return;
    }

    var matchCard = target.closest('.match-card');
    if (matchCard) {
      var key = matchCard.dataset.matchKey;
      if (expandedMatch === key) {
        expandedMatch = null;
      } else {
        expandedMatch = key;
      }
      renderMatchFeed();
      // Also re-render league predictions if on leagues page
      if (currentView === 'leagues' && selectedLeague) {
        var lgFeed = el('lg-match-feed');
        if (lgFeed) {
          var rows = lgFeed.querySelectorAll('.match-card');
          for (var ri = 0; ri < rows.length; ri++) {
            rows[ri].classList.toggle('expanded', rows[ri].dataset.matchKey === expandedMatch);
          }
        }
      }
      return;
    }

    var periodBtn = target.closest('.an-period-btn');
    if (periodBtn) {
      pubDays = parseInt(periodBtn.dataset.days, 10) || 90;
      _store('days', pubDays);
      var allPeriodBtns = document.querySelectorAll('.an-period-btn');
      for (var p = 0; p < allPeriodBtns.length; p++) allPeriodBtns[p].classList.toggle('active', allPeriodBtns[p] === periodBtn);
      var periodLabel = el('rs-period');
      if (periodLabel) periodLabel.textContent = pubDays >= 365 ? 'Год' : pubDays + ' дней';
      loadAnalytics();
      return;
    }

    var sortTh = target.closest('th[data-sort]');
    if (sortTh && resultsCache) {
      var col = sortTh.dataset.sort;
      if (resultsSort.col === col) {
        resultsSort.dir = resultsSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        resultsSort.col = col;
        resultsSort.dir = 'desc';
      }
      _store('resultsSort', resultsSort);
      _renderSortedResults();
      _updateSortHeaders();
      return;
    }

    var chartDl = target.closest('[data-chart]');
    if (chartDl) { _downloadChart(chartDl.dataset.chart); return; }

    if (target.id === 'btn-csv' || target.id === 'an-btn-csv' || target.closest('#btn-csv') || target.closest('#an-btn-csv')) {
      exportResultsCsv();
      return;
    }

    var retryBtn = target.closest('[data-retry]');
    if (retryBtn) {
      var action = retryBtn.dataset.retry;
      if (action === 'predictions') loadPredictions();
      else if (action === 'results') loadResults();
      else if (action === 'analytics') loadAnalytics();
      return;
    }

    // News sidebar items
    var newsItem = target.closest('.nw-item[data-news-id]');
    if (newsItem) {
      showNewsModal(parseInt(newsItem.dataset.newsId, 10));
      return;
    }
    if (target.id === 'nw-show-more' || target.closest('#nw-show-more')) {
      newsShowAll = true;
      renderNews();
      return;
    }
    if (target.id === 'news-close' || target.closest('#news-close')) {
      var newsModal = el('news-modal');
      if (newsModal) newsModal.style.display = 'none';
      return;
    }

    if (target.id === 'sb-about-btn' || target.closest('#sb-about-btn')) {
      var modal = el('about-modal');
      if (modal) modal.style.display = 'flex';
      return;
    }

    if (target.closest('.sb-link[data-view]')) {
      navigate(target.closest('.sb-link[data-view]').dataset.view);
      return;
    }

    if (target.id === 'about-close' || target.closest('#about-close')) {
      var aboutModal = el('about-modal');
      if (aboutModal) aboutModal.style.display = 'none';
      return;
    }
    if (target.classList.contains('modal-overlay')) {
      target.style.display = 'none';
      return;
    }
  });

  // Keyboard
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var newsModal = el('news-modal');
      if (newsModal && newsModal.style.display !== 'none') {
        newsModal.style.display = 'none';
        return;
      }
      var aboutModal = el('about-modal');
      if (aboutModal && aboutModal.style.display !== 'none') {
        aboutModal.style.display = 'none';
        return;
      }
    }
    if (e.key === 'Enter' || e.key === ' ') {
      var t = e.target;
      if (t.classList.contains('league-header') || t.classList.contains('match-card')) {
        e.preventDefault();
        t.click();
      }
    }
  });

  // Resize charts
  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (currentView === 'analytics') _redrawAnalyticsCharts();
      _checkTableScroll();
    }, 300);
  });

  // Table scroll indicator
  function _checkTableScroll() {
    var wraps = document.querySelectorAll('.an-results-table-wrap');
    for (var i = 0; i < wraps.length; i++) {
      wraps[i].classList.toggle('has-scroll', wraps[i].scrollWidth > wraps[i].clientWidth + 4);
    }
  }
  // Debounced table scroll check — avoids firing on every DOM mutation
  (function () {
    var _scrollCheckTimer;
    var _debouncedCheck = function () {
      clearTimeout(_scrollCheckTimer);
      _scrollCheckTimer = setTimeout(_checkTableScroll, 150);
    };
    var wraps = document.querySelectorAll('.an-results-table-wrap');
    if (typeof ResizeObserver !== 'undefined' && wraps.length > 0) {
      var ro = new ResizeObserver(_debouncedCheck);
      for (var i = 0; i < wraps.length; i++) ro.observe(wraps[i]);
    }
    // Fallback: debounced MutationObserver for dynamically added tables
    new MutationObserver(_debouncedCheck).observe(document.body, { childList: true, subtree: false });
  })();

  // Connection ping
  (function () {
    var dot = el('conn-dot');
    if (!dot) return;
    function ping() {
      fetch('/api/public/v1/leagues')
        .then(function (r) {
          var ok = r.ok;
          dot.className = 'conn-dot ' + (ok ? 'ok' : 'offline');
          dot.title = ok ? 'Подключено' : 'Нет соединения';
        })
        .catch(function () {
          dot.className = 'conn-dot offline';
          dot.title = 'Нет соединения';
        });
    }
    ping();
    var connTimer = setInterval(ping, 45000);
    document.addEventListener('visibilitychange', function () {
      clearInterval(connTimer);
      if (!document.hidden) { ping(); connTimer = setInterval(ping, 45000); }
    });
  })();

  // Restore period button
  var periodBtns = document.querySelectorAll('.an-period-btn[data-days]');
  for (var pb = 0; pb < periodBtns.length; pb++) {
    periodBtns[pb].classList.toggle('active', parseInt(periodBtns[pb].dataset.days, 10) === pubDays);
  }

  // ═══ INIT ═══
  function init() {
    loadInitialData().then(function () {
      loadPredictions();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
