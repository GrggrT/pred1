(() => {
  'use strict';

  const API = '/api/public/v1';
  const PICK_LABELS = {
    HOME_WIN: 'Дома', DRAW: 'Ничья', AWAY_WIN: 'Гости',
    H: 'Дома', D: 'Ничья', A: 'Гости',
    OVER_2_5: 'Больше 2.5', UNDER_2_5: 'Меньше 2.5',
    OVER_1_5: 'Больше 1.5', UNDER_1_5: 'Меньше 1.5',
    OVER_3_5: 'Больше 3.5', UNDER_3_5: 'Меньше 3.5',
    BTTS_YES: 'Обе забьют — Да', BTTS_NO: 'Обе забьют — Нет',
    DC_1X: 'Двойной шанс 1X', DC_X2: 'Двойной шанс X2', DC_12: 'Двойной шанс 12',
  };

  const MARKET_SHORT = { '1X2': '1X2', 'TOTAL': 'T2.5', 'TOTAL_1_5': 'T1.5', 'TOTAL_3_5': 'T3.5', 'BTTS': 'ОЗ', 'DOUBLE_CHANCE': 'ДШ' };

  /* ---------- Storage ---------- */
  function _store(key, val) { try { localStorage.setItem('fvb_' + key, JSON.stringify(val)); } catch(e) {} }
  function _load(key, fallback) { try { var v = localStorage.getItem('fvb_' + key); return v !== null ? JSON.parse(v) : fallback; } catch(e) { return fallback; } }

  function _isAbort(e) { return e && e.name === 'AbortError'; }

  /* ---------- State ---------- */
  const matchesState = { league: _load('league', ''), limit: 20, offset: 0 };
  let leaguesCache = null;
  let pubDays = _load('days', 90);
  let currentLeagueId = null;
  const _pubChartPoints = {};
  let _favoriteLeagues = _load('fav_leagues', []);
  var _periodDebounce = null;

  /* ---------- Helpers ---------- */
  function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function el(id) { return document.getElementById(id); }

  function _guardBtn(btn, ms) {
    if (!btn || btn.disabled) return false;
    btn.disabled = true;
    setTimeout(function() { btn.disabled = false; }, ms || 2000);
    return true;
  }

  function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function formatDateShort(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  }

  function _logoPlaceholder(alt, size) {
    var letter = alt ? alt.charAt(0).toUpperCase() : '?';
    return `<span style="display:inline-flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;background:var(--surface-3);border-radius:50%;font-size:${Math.round(size * 0.45)}px;color:var(--text-muted);font-weight:600">${esc(letter)}</span>`;
  }

  function logoImg(url, alt, size) {
    size = size || 32;
    if (!url) return _logoPlaceholder(alt, size);
    return `<img src="${esc(url)}" alt="${esc(alt)}" width="${size}" height="${size}" loading="lazy" onerror="this.outerHTML=this.dataset.fb" data-fb="${esc(_logoPlaceholder(alt, size))}">`;
  }

  /* ---------- Toast ---------- */
  function toast(msg, type) {
    var container = el('pub-toasts');
    if (!container) return;
    var t = document.createElement('div');
    t.className = 'pub-toast ' + (type || 'info');
    t.textContent = msg;
    container.appendChild(t);
    setTimeout(function() { t.remove(); }, 3100);
  }

  /* ---------- Animated Counter ---------- */
  var _prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var _numFmt = typeof Intl !== 'undefined' ? new Intl.NumberFormat('ru-RU') : null;

  function _fmtNum(val, decimals, thousands) {
    var s = val.toFixed(decimals);
    if (thousands && decimals === 0 && _numFmt) s = _numFmt.format(Math.round(val));
    return s;
  }

  function animateValue(element, end, opts) {
    if (!element) return;
    if (end == null || isNaN(end)) { element.textContent = '—'; return; }
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

  /* ---------- Error / Retry ---------- */
  function errorHtml(msg, retryAction) {
    return '<div class="pub-error">' +
      '<div class="pub-error-text">' + esc(msg || 'Ошибка загрузки') + '</div>' +
      (retryAction ? '<button class="pub-retry-btn" data-retry="' + esc(retryAction) + '">Повторить</button>' : '') +
    '</div>';
  }

  function emptyHtml(icon, text, sub) {
    return '<div class="pub-empty-state"><div class="pub-empty-icon">' + icon + '</div>' +
      '<div class="pub-empty-text">' + esc(text) + '</div>' +
      (sub ? '<p class="pub-empty-sub">' + esc(sub) + '</p>' : '') + '</div>';
  }

  const retryActions = {
    home: loadHome,
    matches: fetchMatches,
    standings: loadStandings,
    analytics: loadAnalytics,
    league: loadLeagueDetail,
    about: loadAbout,
  };

  /* ---------- Skeleton ---------- */
  function skeletonCards(n) {
    let html = '';
    for (let i = 0; i < n; i++) {
      html += '<div class="pub-skeleton-card">' +
        '<div class="pub-skeleton pub-skeleton-line w60"></div>' +
        '<div class="pub-skeleton-teams">' +
          '<div class="pub-skeleton-team"><div class="pub-skeleton pub-skeleton-circle"></div><div class="pub-skeleton pub-skeleton-line w80"></div></div>' +
          '<div class="pub-skeleton pub-skeleton-line" style="width:30px;height:20px"></div>' +
          '<div class="pub-skeleton-team"><div class="pub-skeleton pub-skeleton-circle"></div><div class="pub-skeleton pub-skeleton-line w80"></div></div>' +
        '</div>' +
        '<div class="pub-skeleton pub-skeleton-line w40"></div>' +
        '<div class="pub-skeleton pub-skeleton-bar"></div>' +
      '</div>';
    }
    return html;
  }

  /* ---------- API ---------- */
  var _navController = null;

  function _abortNav() {
    if (_navController) { try { _navController.abort(); } catch (e) {} }
    _navController = new AbortController();
    return _navController.signal;
  }

  async function api(path, params, opts) {
    const url = new URL(API + path, location.origin);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, String(v));
      }
    }
    var fetchOpts = {};
    if (opts && opts.signal) fetchOpts.signal = opts.signal;
    else if (_navController) fetchOpts.signal = _navController.signal;
    const resp = await fetch(url, fetchOpts);
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    const total = resp.headers.get('X-Total-Count');
    const data = await resp.json();
    return { data, total: total !== null ? parseInt(total, 10) : null };
  }

  /* ---------- Match Card ---------- */
  function matchStatusLabel(status) {
    if (!status) return '';
    if (status === 'NS') return '<span class="pub-status-badge ns">Ожидается</span>';
    if (status === 'FT' || status === 'AET' || status === 'PEN') return '<span class="pub-status-badge ft">Завершён</span>';
    if (['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE'].includes(status)) return '<span class="pub-status-badge live">Live</span>';
    return '<span class="pub-status-badge">' + esc(status) + '</span>';
  }

  function matchCardHtml(m) {
    const pickLabel = PICK_LABELS[m.pick] || m.pick || '—';
    const evPct = m.ev != null ? (m.ev * 100).toFixed(1) : null;
    const evClass = m.ev != null ? (m.ev >= 0.10 ? 'strong' : m.ev >= 0.0 ? 'positive' : '') : '';
    const hasScore = m.score && m.score !== 'null';
    const hasResult = m.status === 'WIN' || m.status === 'LOSS';
    const isFinished = hasResult || (m.fixture_status && ['FT', 'AET', 'PEN'].includes(m.fixture_status));
    const isWin = m.status === 'WIN';

    let html = '<div class="pub-match-card' + (isFinished ? ' finished' : '') + (hasResult ? ' has-result' : '') + '" data-fixture-id="' + (m.fixture_id || '') + '" tabindex="0">';

    // Result badge overlay for finished matches
    if (hasResult) {
      html += '<div class="pub-match-result-badge ' + (isWin ? 'win' : 'loss') + '">' + (isWin ? 'WIN' : 'LOSS') + '</div>';
    }

    html += '<div class="pub-match-league">' +
        logoImg(m.league_logo_url, m.league, 16) +
        '<span>' + esc(m.league || '') + '</span>' +
        (hasResult ? '' : matchStatusLabel(m.fixture_status)) +
      '</div>' +
      '<div class="pub-match-teams">' +
        '<div class="pub-match-team">' +
          logoImg(m.home_logo_url, m.home, 40) +
          '<span class="pub-match-team-name">' + esc(m.home) + '</span>' +
        '</div>' +
        (hasScore
          ? '<span class="pub-match-score">' + esc(m.score) + '</span>'
          : '<span class="pub-match-vs">VS</span>') +
        '<div class="pub-match-team">' +
          logoImg(m.away_logo_url, m.away, 40) +
          '<span class="pub-match-team-name">' + esc(m.away) + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="pub-match-kickoff">' + formatDate(m.kickoff) + '</div>' +
      '<div class="pub-match-prediction">' +
        '<span class="pub-market-badge">' + esc(MARKET_SHORT[m.market] || m.market || '1X2') + '</span>' +
        '<span class="pub-pick-badge">' + esc(pickLabel) + '</span>' +
        '<span class="pub-match-odd">' + (m.odd != null ? m.odd.toFixed(2) : '—') + '</span>' +
        (evPct !== null ? '<span class="pub-match-ev ' + evClass + '">EV ' + evPct + '%</span>' : '') +
      '</div>';

    // Profit row for settled matches
    if (hasResult) {
      const profitStr = m.profit != null ? (m.profit >= 0 ? '+' : '') + m.profit.toFixed(2) : '—';
      const profitCls = m.profit != null ? (m.profit >= 0 ? 'positive' : 'negative') : '';
      html += '<div class="pub-match-result-row">' +
        '<span class="pub-result-badge ' + (isWin ? 'win' : 'loss') + '">' + (isWin ? 'Выигрыш' : 'Проигрыш') + '</span>' +
        '<span class="pub-profit ' + profitCls + '">' + profitStr + '</span>' +
      '</div>';
    }

    html += '</div>';
    return html;
  }

  /* ---------- Home ---------- */
  async function loadHome() {
    try {
      const results = await Promise.allSettled([
        api('/stats', { days: pubDays }),
        api('/matches', { limit: 8 }),
        api('/results', { days: 30, limit: 6 }),
        api('/leagues'),
      ]);
      var statsRes = results[0].status === 'fulfilled' ? results[0].value : null;
      var matchesRes = results[1].status === 'fulfilled' ? results[1].value : null;
      var resultsRes = results[2].status === 'fulfilled' ? results[2].value : null;
      var leaguesRes = results[3].status === 'fulfilled' ? results[3].value : null;
      if (statsRes) renderHomeStats(statsRes.data);
      if (matchesRes) renderHomeUpcoming(matchesRes.data); else el('home-upcoming').innerHTML = errorHtml('Не удалось загрузить матчи', 'home');
      if (resultsRes) renderHomeResults(resultsRes.data);
      if (leaguesRes) { renderHomeLeagues(leaguesRes.data); leaguesCache = leaguesRes.data; }
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadHome error', e);
      el('home-upcoming').innerHTML = errorHtml('Не удалось загрузить данные', 'home');
    }
  }

  function renderHomeStats(s) {
    const roiEl = el('stat-roi');
    const wrEl = el('stat-winrate');
    const betsEl = el('stat-bets');
    const profEl = el('stat-profit');

    roiEl.className = 'pub-stat-value' + (s.roi > 0 ? '' : s.roi < 0 ? ' negative' : ' neutral');
    animateValue(roiEl, s.roi, { suffix: '%', decimals: 1 });

    animateValue(wrEl, s.win_rate, { suffix: '%', decimals: 1 });
    animateValue(betsEl, s.total_bets, { decimals: 0, thousands: true });

    profEl.className = 'pub-stat-value' + (s.total_profit > 0 ? '' : s.total_profit < 0 ? ' negative' : ' neutral');
    animateValue(profEl, s.total_profit, { suffix: '', decimals: 1, sign: true });
  }

  function renderHomeUpcoming(matches) {
    const cont = el('home-upcoming');
    if (!matches || !matches.length) {
      cont.innerHTML = emptyHtml('\u26bd', 'Нет предстоящих прогнозов', 'Новые появятся ближе к матчам');
      return;
    }
    cont.innerHTML = matches.map(matchCardHtml).join('');
  }

  function renderHomeResults(results) {
    const cont = el('home-results');
    if (!results || !results.length) {
      cont.innerHTML = emptyHtml('\ud83d\udcca', 'Нет завершённых прогнозов', 'За последние 30 дней результатов нет');
      return;
    }
    cont.innerHTML = results.map(matchCardHtml).join('');
  }

  function toggleFavoriteLeague(leagueId) {
    var id = String(leagueId);
    var idx = _favoriteLeagues.indexOf(id);
    if (idx >= 0) { _favoriteLeagues.splice(idx, 1); } else { _favoriteLeagues.push(id); }
    _store('fav_leagues', _favoriteLeagues);
  }

  function _isFav(leagueId) { return _favoriteLeagues.indexOf(String(leagueId)) >= 0; }

  function _leagueCardHtml(l) {
    var fav = _isFav(l.id);
    return '<div class="pub-league-card' + (fav ? ' fav' : '') + '" data-league-id="' + l.id + '" tabindex="0" role="button">' +
      '<button class="pub-fav-btn' + (fav ? ' active' : '') + '" data-fav-league="' + l.id + '" aria-label="' + (fav ? 'Убрать из избранного' : 'Добавить в избранное') + '" title="Избранное">&#9733;</button>' +
      logoImg(l.logo_url, l.name, 28) +
      '<div class="pub-league-info"><span class="pub-league-name">' + esc(l.name) + '</span>' +
      (l.country ? '<span class="pub-league-country">' + esc(l.country) + '</span>' : '') +
      '</div></div>';
  }

  function renderHomeLeagues(leagues) {
    const cont = el('home-leagues');
    if (!leagues || !leagues.length) {
      cont.textContent = 'Нет данных о лигах';
      return;
    }
    // Sort: favorites first
    var sorted = [...leagues].sort(function(a, b) {
      var fa = _isFav(a.id) ? 0 : 1;
      var fb = _isFav(b.id) ? 0 : 1;
      return fa - fb;
    });
    // Note: all values go through esc() or logoImg() which use safe escaping
    cont.innerHTML = sorted.map(_leagueCardHtml).join('');
  }

  /* ---------- Matches ---------- */
  async function loadMatches() {
    try {
      if (!leaguesCache) {
        const lr = await api('/leagues');
        leaguesCache = lr.data;
      }
      populateLeagueFilter();
      await fetchMatches();
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadMatches error', e);
      el('matches-list').innerHTML = errorHtml('Ошибка загрузки матчей', 'matches');
    }
  }

  function populateLeagueFilter() {
    const sel = el('filter-league');
    if (!sel || sel.options.length > 1) return;
    if (leaguesCache) {
      leaguesCache.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.id;
        opt.textContent = l.name;
        sel.appendChild(opt);
      });
    }
  }

  async function fetchMatches() {
    const cont = el('matches-list');
    cont.innerHTML = skeletonCards(4);
    const params = { limit: matchesState.limit, offset: matchesState.offset, days_ahead: 14 };
    if (matchesState.league) params.league_id = matchesState.league;
    const res = await api('/matches', params);
    if (!res.data.length) {
      cont.innerHTML = emptyHtml('\ud83d\udd0d', 'Нет прогнозов по фильтрам', 'Попробуйте другую лигу или подождите обновления');
      el('matches-pagination').innerHTML = '';
      return;
    }
    cont.innerHTML = res.data.map(matchCardHtml).join('');
    renderPagination(res.total || res.data.length);
  }

  function renderPagination(total) {
    const cont = el('matches-pagination');
    const pages = Math.ceil(total / matchesState.limit);
    const current = Math.floor(matchesState.offset / matchesState.limit);
    if (pages <= 1) { cont.innerHTML = ''; return; }

    let html = '';
    html += `<button ${current === 0 ? 'disabled' : ''} data-page="${current - 1}" aria-label="Предыдущая страница">\u2190 Пред</button>`;
    for (let i = 0; i < Math.min(pages, 7); i++) {
      html += `<button class="${i === current ? 'active' : ''}" data-page="${i}" aria-label="Страница ${i + 1}">${i + 1}</button>`;
    }
    html += `<button ${current >= pages - 1 ? 'disabled' : ''} data-page="${current + 1}" aria-label="Следующая страница">След \u2192</button>`;
    cont.innerHTML = html;
  }

  /* ---------- Results sort state ---------- */
  let _resultsCache = null;
  let _resultsSort = _load('resultsSort', { col: 'kickoff', dir: 'desc' });

  /* ---------- Analytics ---------- */
  async function loadAnalytics() {
    try {
      const [statsRes, resultsRes] = await Promise.all([
        api('/stats', { days: pubDays }),
        api('/results', { days: pubDays, limit: 200 }),
      ]);
      renderAnalyticsSummary(statsRes.data);
      renderResultsTable(resultsRes.data);
      renderRoiChart(resultsRes.data);
      renderProfitChart(resultsRes.data);
      renderLeagueBreakdown(resultsRes.data);
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadAnalytics error', e);
      const cont = el('results-tbody');
      if (cont) cont.innerHTML = '<tr><td colspan="9">' + errorHtml('Ошибка загрузки аналитики', 'analytics') + '</td></tr>';
    }
  }

  function renderLeagueBreakdown(results) {
    const cont = el('league-breakdown');
    if (!cont || !results || !results.length) return;
    // Group by league
    const leagues = {};
    results.forEach(r => {
      const key = r.league || 'Другое';
      if (!leagues[key]) leagues[key] = { bets: 0, wins: 0, profit: 0 };
      leagues[key].bets++;
      if (r.status === 'WIN') leagues[key].wins++;
      leagues[key].profit += (r.profit || 0);
    });
    const rows = Object.entries(leagues).map(([name, d]) => ({
      name, bets: d.bets, wins: d.wins, wr: d.bets > 0 ? (d.wins / d.bets * 100) : 0,
      roi: d.bets > 0 ? (d.profit / d.bets * 100) : 0, profit: d.profit,
    })).sort((a, b) => b.bets - a.bets);

    let html = '<table class="pub-table"><thead><tr><th>Лига</th><th>Ставок</th><th>Win Rate</th><th>ROI</th><th>Профит</th></tr></thead><tbody>';
    rows.forEach(r => {
      const roiCls = r.roi >= 0 ? 'positive' : 'negative';
      const profitCls = r.profit >= 0 ? 'positive' : 'negative';
      html += '<tr><td>' + esc(r.name) + '</td><td>' + r.bets + '</td>' +
        '<td>' + r.wr.toFixed(1) + '%</td>' +
        '<td><span class="pub-profit ' + roiCls + '">' + (r.roi >= 0 ? '+' : '') + r.roi.toFixed(1) + '%</span></td>' +
        '<td><span class="pub-profit ' + profitCls + '">' + (r.profit >= 0 ? '+' : '') + r.profit.toFixed(2) + '</span></td></tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderAnalyticsSummary(s) {
    var roiEl = el('an-roi');
    roiEl.className = 'pub-stat-value' + (s.roi > 0 ? '' : s.roi < 0 ? ' negative' : ' neutral');
    animateValue(roiEl, s.roi, { suffix: '%', decimals: 1 });

    animateValue(el('an-winrate'), s.win_rate, { suffix: '%', decimals: 1 });
    animateValue(el('an-bets'), s.total_bets, { decimals: 0, thousands: true });

    var profEl = el('an-profit');
    profEl.className = 'pub-stat-value' + (s.total_profit > 0 ? '' : s.total_profit < 0 ? ' negative' : ' neutral');
    animateValue(profEl, s.total_profit, { suffix: '', decimals: 1, sign: true });
  }

  function renderResultsTable(results) {
    // Results data comes from our own public API — all fields are escaped via esc()
    const tbody = el('results-tbody');
    if (!results || !results.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="pub-loading">Нет данных</td></tr>';
      _resultsCache = null;
      return;
    }
    _resultsCache = results;
    _renderSortedResults();
    _updateSortHeaders();
  }

  function _renderSortedResults() {
    const tbody = el('results-tbody');
    if (!_resultsCache) return;
    const sorted = [..._resultsCache].sort(function(a, b) {
      var col = _resultsSort.col;
      var va, vb;
      if (col === 'kickoff') { va = a.kickoff || ''; vb = b.kickoff || ''; }
      else if (col === 'odd') { va = a.odd || 0; vb = b.odd || 0; }
      else if (col === 'profit') { va = a.profit || 0; vb = b.profit || 0; }
      else if (col === 'status') { va = a.status || ''; vb = b.status || ''; }
      else { va = 0; vb = 0; }
      if (va < vb) return _resultsSort.dir === 'asc' ? -1 : 1;
      if (va > vb) return _resultsSort.dir === 'asc' ? 1 : -1;
      // Tiebreaker: sort by kickoff descending for stability
      if (col !== 'kickoff') return (b.kickoff || '').localeCompare(a.kickoff || '');
      return 0;
    });
    tbody.innerHTML = sorted.slice(0, 50).map(function(r) {
      var pickLabel = PICK_LABELS[r.pick] || r.pick || '—';
      var isWin = r.status === 'WIN';
      var profitStr = r.profit != null ? (r.profit >= 0 ? '+' : '') + r.profit.toFixed(2) : '—';
      var marketLabel = MARKET_SHORT[r.market] || r.market || '1X2';
      return '<tr>' +
        '<td>' + formatDateShort(r.kickoff) + '</td>' +
        '<td>' + esc(r.home) + ' — ' + esc(r.away) + '</td>' +
        '<td>' + esc(r.league || '') + '</td>' +
        '<td>' + esc(r.score || '—') + '</td>' +
        '<td>' + esc(pickLabel) + '</td>' +
        '<td>' + esc(marketLabel) + '</td>' +
        '<td>' + (r.odd != null ? r.odd.toFixed(2) : '—') + '</td>' +
        '<td><span class="pub-result-badge ' + (isWin ? 'win' : 'loss') + '">' + (isWin ? 'Win' : 'Loss') + '</span></td>' +
        '<td><span class="pub-profit ' + (r.profit >= 0 ? 'positive' : 'negative') + '">' + profitStr + '</span></td>' +
      '</tr>';
    }).join('');
  }

  function _updateSortHeaders() {
    document.querySelectorAll('#results-table th[data-sort]').forEach(function(th) {
      var col = th.dataset.sort;
      var isActive = col === _resultsSort.col;
      th.classList.toggle('sort-active', isActive);
      th.classList.toggle('sort-asc', isActive && _resultsSort.dir === 'asc');
      th.classList.toggle('sort-desc', isActive && _resultsSort.dir === 'desc');
      th.setAttribute('aria-sort', isActive ? (_resultsSort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
    });
  }

  function exportResultsCsv() {
    if (!_resultsCache || !_resultsCache.length) { toast('Нет данных для экспорта', 'error'); return; }
    var header = ['Дата', 'Дома', 'Гости', 'Лига', 'Счёт', 'Прогноз', 'Рынок', 'Кф.', 'Результат', 'Профит'];
    var rows = [header];
    _resultsCache.forEach(function(r) {
      rows.push([
        r.kickoff ? new Date(r.kickoff).toLocaleDateString('ru-RU') : '',
        r.home || '', r.away || '', r.league || '', r.score || '',
        r.pick || '', r.market || '', r.odd != null ? Number(r.odd).toFixed(2) : '',
        r.status || '', r.profit != null ? r.profit.toFixed(2) : ''
      ]);
    });
    var csv = rows.map(function(row) {
      return row.map(function(c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(',');
    }).join('\n');
    var blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'results_' + new Date().toISOString().slice(0, 10) + '.csv';
    link.click();
    URL.revokeObjectURL(link.href);
    toast('CSV экспортирован', 'success');
  }

  function renderRoiChart(results) {
    const canvas = el('roi-chart');
    if (!canvas) return;
    if (!results || results.length < 2) {
      const wrap = canvas.parentElement;
      if (wrap) wrap.innerHTML = '<div class="pub-loading" style="padding:40px 0">Недостаточно данных для графика. Нужно минимум 2 завершённых прогноза — попробуйте увеличить период.</div>';
      return;
    }
    try { _drawRoiChart(canvas, results); } catch (e) {
      console.error('Chart error', e);
      const wrap = canvas.parentElement;
      if (wrap) wrap.innerHTML = '<div class="pub-loading" style="padding:40px 0;color:var(--accent-danger)">Ошибка отрисовки графика</div>';
    }
  }

  function _getOrCreatePubTooltip() {
    let tip = document.querySelector('.pub-chart-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'pub-chart-tooltip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  function _bindPubChartTooltip(canvas, canvasId) {
    if (!canvas || canvas.dataset.tipBound) return;
    canvas.dataset.tipBound = '1';
    canvas.addEventListener('mousemove', (e) => {
      const info = _pubChartPoints[canvasId];
      if (!info || !info.pts.length) return;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      let best = null, bestDist = 20;
      info.pts.forEach((p, i) => {
        const sx = info.px(p, i), sy = info.py(p);
        const dist = Math.sqrt((mx - sx) ** 2 + (my - sy) ** 2);
        if (dist < bestDist) { bestDist = dist; best = { p, i }; }
      });
      const tip = _getOrCreatePubTooltip();
      if (best) {
        tip.textContent = info.formatTip(best.p, best.i);
        tip.style.display = 'block';
        var tipLeft = e.clientX + 14;
        var tipTop = e.clientY - 10;
        var tipW = tip.offsetWidth || 150;
        if (tipLeft + tipW > window.innerWidth - 8) tipLeft = e.clientX - tipW - 10;
        if (tipTop < 4) tipTop = 4;
        tip.style.left = tipLeft + 'px';
        tip.style.top = tipTop + 'px';
      } else {
        tip.style.display = 'none';
      }
    });
    canvas.addEventListener('mouseleave', () => {
      const tip = document.querySelector('.pub-chart-tooltip');
      if (tip) tip.style.display = 'none';
    });
  }

  function _downloadPubChart(canvasId) {
    const canvas = el(canvasId);
    if (!canvas) return;
    try {
      const link = document.createElement('a');
      link.download = canvasId + '.png';
      link.href = canvas.toDataURL('image/png');
      link.click();
      toast('График сохранён', 'success');
    } catch (e) {
      toast('Ошибка скачивания графика', 'error');
    }
  }

  var _cc = null;
  function _chartColors() {
    if (_cc) return _cc;
    var s = getComputedStyle(document.documentElement);
    _cc = {
      grid: 'rgba(31,43,60,0.5)',
      muted: s.getPropertyValue('--text-muted').trim() || '#8a97ad',
      accent: s.getPropertyValue('--accent-primary').trim() || '#b6f33d',
      danger: s.getPropertyValue('--accent-danger').trim() || '#f43f5e',
      secondary: s.getPropertyValue('--accent-secondary').trim() || '#38bdf8',
      font: '11px ' + (s.getPropertyValue('--font-body').trim() || 'Onest, sans-serif'),
    };
    return _cc;
  }

  function _drawRoiChart(canvas, results) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;

    // Sort by kickoff ascending
    const sorted = [...results].sort((a, b) => new Date(a.kickoff) - new Date(b.kickoff));

    // Compute running ROI
    let cumProfit = 0;
    const points = sorted.map((r, i) => {
      cumProfit += (r.profit || 0);
      return { x: i, roi: (cumProfit / (i + 1)) * 100, date: r.kickoff };
    });

    const padTop = 30, padBottom = 30, padLeft = 50, padRight = 20;
    const chartW = W - padLeft - padRight;
    const chartH = H - padTop - padBottom;

    const minRoi = Math.min(0, ...points.map(p => p.roi));
    const maxRoi = Math.max(0, ...points.map(p => p.roi));
    const roiRange = (maxRoi - minRoi) || 1;

    function px(i) { return padLeft + (i / Math.max(points.length - 1, 1)) * chartW; }
    function py(roi) { return padTop + (1 - (roi - minRoi) / roiRange) * chartH; }

    // Background
    ctx.fillStyle = 'transparent';
    ctx.clearRect(0, 0, W, H);

    // Grid lines
    var cc = _chartColors();
    ctx.strokeStyle = cc.grid;
    ctx.lineWidth = 1;
    const steps = 5;
    for (let i = 0; i <= steps; i++) {
      const val = minRoi + (roiRange / steps) * i;
      const y = py(val);
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(W - padRight, y);
      ctx.stroke();
      ctx.fillStyle = cc.muted;
      ctx.font = cc.font;
      ctx.textAlign = 'right';
      ctx.fillText(val.toFixed(1) + '%', padLeft - 6, y + 4);
    }

    // Zero line
    if (minRoi < 0 && maxRoi > 0) {
      ctx.strokeStyle = 'rgba(138,151,173,0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(padLeft, py(0));
      ctx.lineTo(W - padRight, py(0));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Line
    ctx.beginPath();
    ctx.strokeStyle = cc.accent;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    points.forEach((p, i) => {
      if (i === 0) ctx.moveTo(px(i), py(p.roi));
      else ctx.lineTo(px(i), py(p.roi));
    });
    ctx.stroke();

    // Gradient fill
    const grad = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
    grad.addColorStop(0, 'rgba(182, 243, 61, 0.15)');
    grad.addColorStop(1, 'rgba(182, 243, 61, 0)');
    ctx.lineTo(px(points.length - 1), H - padBottom);
    ctx.lineTo(px(0), H - padBottom);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // End dot
    const last = points[points.length - 1];
    ctx.beginPath();
    ctx.arc(px(points.length - 1), py(last.roi), 4, 0, Math.PI * 2);
    ctx.fillStyle = cc.accent;
    ctx.fill();

    // Store points for tooltip
    _pubChartPoints['roi-chart'] = {
      pts: points, px: (p, i) => px(i), py: p => py(p.roi),
      formatTip: (p, i) => 'Ставка #' + (i + 1) + ' \u00b7 ' + formatDateShort(p.date) + ' \u00b7 ROI: ' + p.roi.toFixed(2) + '%',
    };
    _bindPubChartTooltip(canvas, 'roi-chart');
  }

  /* ---------- Profit Chart ---------- */
  function renderProfitChart(results) {
    const canvas = el('profit-chart');
    if (!canvas) return;
    if (!results || results.length < 2) {
      const wrap = canvas.parentElement;
      if (wrap) wrap.innerHTML = '<div class="pub-loading" style="padding:40px 0">Недостаточно данных для графика. Нужно минимум 2 завершённых прогноза — попробуйте увеличить период.</div>';
      return;
    }
    try { _drawProfitChart(canvas, results); } catch (e) {
      console.error('Profit chart error', e);
      const wrap = canvas.parentElement;
      if (wrap) wrap.innerHTML = '<div class="pub-loading" style="padding:40px 0;color:var(--accent-danger)">Ошибка отрисовки графика</div>';
    }
  }

  function _drawProfitChart(canvas, results) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;

    const sorted = [...results].sort((a, b) => new Date(a.kickoff) - new Date(b.kickoff));
    let cumProfit = 0;
    const points = sorted.map((r, i) => {
      cumProfit += (r.profit || 0);
      return { x: i, profit: cumProfit, date: r.kickoff };
    });

    const padTop = 30, padBottom = 30, padLeft = 50, padRight = 20;
    const chartW = W - padLeft - padRight;
    const chartH = H - padTop - padBottom;

    const minP = Math.min(0, ...points.map(p => p.profit));
    const maxP = Math.max(0, ...points.map(p => p.profit));
    const range = (maxP - minP) || 1;

    function px(i) { return padLeft + (i / Math.max(points.length - 1, 1)) * chartW; }
    function py(v) { return padTop + (1 - (v - minP) / range) * chartH; }

    ctx.clearRect(0, 0, W, H);

    // Grid
    var cc = _chartColors();
    ctx.strokeStyle = cc.grid;
    ctx.lineWidth = 1;
    const steps = 5;
    for (let i = 0; i <= steps; i++) {
      const val = minP + (range / steps) * i;
      const y = py(val);
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(W - padRight, y);
      ctx.stroke();
      ctx.fillStyle = cc.muted;
      ctx.font = cc.font;
      ctx.textAlign = 'right';
      ctx.fillText(val.toFixed(1), padLeft - 6, y + 4);
    }

    // Zero line
    if (minP < 0 && maxP > 0) {
      ctx.strokeStyle = 'rgba(138,151,173,0.4)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(padLeft, py(0));
      ctx.lineTo(W - padRight, py(0));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Line
    const lastProfit = points[points.length - 1].profit;
    const lineColor = lastProfit >= 0 ? cc.accent : cc.danger;
    ctx.beginPath();
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    points.forEach((p, i) => {
      if (i === 0) ctx.moveTo(px(i), py(p.profit));
      else ctx.lineTo(px(i), py(p.profit));
    });
    ctx.stroke();

    // Gradient fill
    const gradTop = lastProfit >= 0 ? 'rgba(182, 243, 61, 0.15)' : 'rgba(244, 63, 94, 0.15)';
    const grad = ctx.createLinearGradient(0, padTop, 0, H - padBottom);
    grad.addColorStop(0, gradTop);
    grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.lineTo(px(points.length - 1), H - padBottom);
    ctx.lineTo(px(0), H - padBottom);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // End dot
    const last = points[points.length - 1];
    ctx.beginPath();
    ctx.arc(px(points.length - 1), py(last.profit), 4, 0, Math.PI * 2);
    ctx.fillStyle = lineColor;
    ctx.fill();

    // Store points for tooltip
    _pubChartPoints['profit-chart'] = {
      pts: points, px: (p, i) => px(i), py: p => py(p.profit),
      formatTip: (p, i) => 'Ставка #' + (i + 1) + ' \u00b7 ' + formatDateShort(p.date) + ' \u00b7 Профит: ' + (p.profit >= 0 ? '+' : '') + p.profit.toFixed(2),
    };
    _bindPubChartTooltip(canvas, 'profit-chart');
  }

  /* ---------- Match Detail Modal ---------- */
  // All data rendered via esc() which safely escapes HTML entities
  let _currentModalFixtureId = null;
  var _modalOpener = null;

  function _trapFocus(e) {
    var modal = el('pub-match-modal');
    if (!modal || modal.style.display === 'none') return;
    var focusable = modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) { e.preventDefault(); last.focus(); }
    } else {
      if (document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function openMatchModal(fixtureId) {
    const modal = el('pub-match-modal');
    const body = el('pub-modal-body');
    if (!modal || !body) return;
    _modalOpener = document.activeElement;
    _currentModalFixtureId = fixtureId;
    modal.style.display = 'flex';
    body.innerHTML = '<div class="pub-modal-spinner">Загрузка...</div>';
    var closeBtn = modal.querySelector('.pub-modal-close');
    if (closeBtn) closeBtn.focus();

    api('/matches/' + fixtureId).then(res => {
      const d = res.data;
      if (!d) { body.textContent = 'Нет данных'; return; }
      let html = '';

      // Match header
      html += '<div class="pub-modal-match">';
      html += '<div class="pub-modal-team">' + logoImg(d.home_logo_url, d.home, 48) + '<span>' + esc(d.home || '') + '</span></div>';
      if (d.score && d.score !== 'null' && String(d.score).trim()) {
        html += '<span class="pub-modal-score">' + esc(d.score) + '</span>';
      } else {
        html += '<span class="pub-modal-vs">VS</span>';
      }
      html += '<div class="pub-modal-team">' + logoImg(d.away_logo_url, d.away, 48) + '<span>' + esc(d.away || '') + '</span></div>';
      html += '</div>';

      html += '<div class="pub-modal-meta">';
      if (d.league) html += '<span>' + esc(d.league) + '</span>';
      if (d.kickoff) html += '<span>' + formatDate(d.kickoff) + '</span>';
      html += '</div>';

      // Predictions — array (multi-market) with singular fallback
      const preds = d.predictions && d.predictions.length ? d.predictions : (d.prediction ? [d.prediction] : []);
      if (preds.length) {
        html += '<div class="pub-modal-section-title">' + (preds.length > 1 ? 'Прогнозы' : 'Прогноз') + '</div>';
        html += '<div class="pub-modal-preds">';
        preds.forEach(function(pred) {
          const pickLabel = PICK_LABELS[pred.pick] || pred.pick || '—';
          const marketLabel = MARKET_SHORT[pred.market] || pred.market || '1X2';
          const evPct = pred.ev != null ? (pred.ev * 100).toFixed(1) + '%' : '—';
          const confPct = pred.confidence != null ? (pred.confidence * 100).toFixed(1) + '%' : '—';
          const statusCls = pred.status === 'WIN' ? 'win' : pred.status === 'LOSS' ? 'loss' : 'pending';
          const statusLabel = pred.status === 'WIN' ? 'Выигрыш' : pred.status === 'LOSS' ? 'Проигрыш' : pred.status === 'VOID' ? 'Отмена' : 'Ожидание';
          html += '<div class="pub-modal-pred-card">';
          html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">Рынок</span><span>' + esc(marketLabel) + '</span></div>';
          html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">Прогноз</span><span class="pub-pick-badge">' + esc(pickLabel) + '</span></div>';
          html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">Кф.</span><span style="font-family:var(--font-display);font-weight:700">' + (pred.odd != null ? Number(pred.odd).toFixed(2) : '—') + '</span></div>';
          html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">Уверенность</span><span>' + confPct + '</span></div>';
          html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">EV</span><span>' + evPct + '</span></div>';
          html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">Статус</span><span class="pub-result-badge ' + statusCls + '">' + esc(statusLabel) + '</span></div>';
          if (pred.profit != null) {
            const profitCls = pred.profit >= 0 ? 'positive' : 'negative';
            const profitStr = (pred.profit >= 0 ? '+' : '') + pred.profit.toFixed(2);
            html += '<div class="pub-modal-pred-row"><span class="pub-modal-pred-label">Профит</span><span class="pub-profit ' + profitCls + '">' + profitStr + '</span></div>';
          }
          html += '</div>';
        });
        html += '</div>';
      }

      // Odds table
      if (d.odds && Object.values(d.odds).some(function(v) { return v != null; })) {
        html += '<div class="pub-modal-section-title">Коэффициенты</div>';
        html += '<table class="pub-table" style="margin-bottom:0"><tbody>';
        if (d.odds.home_win != null) html += '<tr><td>П1 / Н / П2</td><td>' + [d.odds.home_win, d.odds.draw, d.odds.away_win].map(v => v != null ? Number(v).toFixed(2) : '—').join(' / ') + '</td></tr>';
        if (d.odds.over_2_5 != null) html += '<tr><td>Б/М 2.5</td><td>' + Number(d.odds.over_2_5).toFixed(2) + ' / ' + (d.odds.under_2_5 != null ? Number(d.odds.under_2_5).toFixed(2) : '—') + '</td></tr>';
        html += '</tbody></table>';
      }

      body.innerHTML = html;
      el('pub-modal-title').textContent = (d.home || '') + ' — ' + (d.away || '');
    }).catch(e => {
      if (_isAbort(e)) return;
      console.error('openMatchModal error', e);
      body.textContent = 'Не удалось загрузить данные матча. Попробуйте позже.';
    });
  }

  function closeMatchModal() {
    const modal = el('pub-match-modal');
    if (modal) modal.style.display = 'none';
    _currentModalFixtureId = null;
    if (_modalOpener && _modalOpener.focus) { try { _modalOpener.focus(); } catch (e) {} }
    _modalOpener = null;
  }

  function shareMatch() {
    if (!_currentModalFixtureId) return;
    var url = location.origin + '/?fixture=' + _currentModalFixtureId + '#matches';
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function() {
        toast('Ссылка скопирована', 'success');
      });
    } else {
      // Fallback
      var inp = document.createElement('input');
      inp.value = url;
      document.body.appendChild(inp);
      inp.select();
      document.execCommand('copy');
      inp.remove();
      toast('Ссылка скопирована', 'success');
    }
  }

  /* ---------- Standings ---------- */
  async function loadStandings() {
    try {
      if (!leaguesCache) {
        const lr = await api('/leagues');
        leaguesCache = lr.data;
      }
      populateStandingsLeagueFilter();
      const sel = el('standings-league');
      if (sel && sel.value) {
        await fetchStandings(sel.value);
      }
    } catch (e) {
      if (!_isAbort(e)) console.error('loadStandings error', e);
    }
  }

  function populateStandingsLeagueFilter() {
    const sel = el('standings-league');
    if (!sel || sel.options.length > 0) return;
    if (leaguesCache && leaguesCache.length) {
      leaguesCache.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.id;
        opt.textContent = l.name;
        sel.appendChild(opt);
      });
      if (leaguesCache.length > 0) sel.value = leaguesCache[0].id;
    }
  }

  async function fetchStandings(leagueId) {
    const cont = el('standings-table-wrap');
    cont.innerHTML = skeletonCards(1);
    try {
      const res = await api('/standings', { league_id: leagueId });
      renderStandingsTable(res.data);
    } catch (e) {
      cont.innerHTML = errorHtml('Ошибка загрузки таблицы', 'standings');
    }
  }

  // Shared standings table HTML generator — all fields escaped via esc()/logoImg()
  function _standingsTableHtml(rows) {
    if (!rows || !rows.length) return '';
    let html = '<table class="pub-table pub-standings-table"><thead><tr><th>#</th><th>Клуб</th><th>И</th><th>ГЗ</th><th>ГП</th><th>РМ</th><th>О</th><th>Форма</th></tr></thead><tbody>';
    rows.forEach(r => {
      const form = r.form || '';
      const formDots = form.split('').map(ch => {
        const cls = ch === 'W' ? 'pub-form-w' : ch === 'D' ? 'pub-form-d' : ch === 'L' ? 'pub-form-l' : '';
        var tip = ch === 'W' ? 'Победа' : ch === 'D' ? 'Ничья' : ch === 'L' ? 'Поражение' : ch;
        return '<span class="pub-form-dot ' + cls + '" title="' + esc(tip) + '"></span>';
      }).join('');
      html += '<tr>';
      html += '<td>' + (r.rank || '') + '</td>';
      html += '<td><div class="pub-standings-club">' + logoImg(r.team_logo_url, r.team_name, 20) + '<span>' + esc(r.team_name || '') + '</span></div></td>';
      html += '<td>' + (r.played || 0) + '</td>';
      html += '<td>' + (r.goals_for || 0) + '</td>';
      html += '<td>' + (r.goals_against || 0) + '</td>';
      html += '<td>' + (r.goal_diff != null ? (r.goal_diff > 0 ? '+' : '') + r.goal_diff : '0') + '</td>';
      html += '<td class="pub-standings-pts">' + (r.points || 0) + '</td>';
      html += '<td><div class="pub-form-wrap">' + formDots + '</div></td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
  }

  // Standings data from our own public API — all fields escaped via esc()
  function renderStandingsTable(rows) {
    const cont = el('standings-table-wrap');
    if (!rows || !rows.length) {
      cont.textContent = 'Нет данных';
      return;
    }
    // All data from authenticated public API, all values escaped via esc()/logoImg()
    cont.innerHTML = _standingsTableHtml(rows);
  }

  /* ---------- League Detail ---------- */
  async function loadLeagueDetail() {
    if (!currentLeagueId) {
      location.hash = '#home';
      return;
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
    const lid = currentLeagueId;

    // Render league header from cache
    const headerEl = el('league-header');
    if (leaguesCache) {
      const league = leaguesCache.find(l => String(l.id) === String(lid));
      if (league) {
        headerEl.innerHTML = logoImg(league.logo_url, league.name, 48) +
          '<div class="pub-league-detail-info">' +
          '<span class="pub-league-detail-name">' + esc(league.name) + '</span>' +
          (league.country ? '<span class="pub-league-detail-country">' + esc(league.country) + '</span>' : '') +
          '</div>';
      }
    }

    try {
      const [standingsRes, matchesRes, resultsRes] = await Promise.all([
        api('/standings', { league_id: lid }),
        api('/matches', { league_id: lid, limit: 10 }),
        api('/results', { league_id: lid, days: 90, limit: 30 }),
      ]);

      // Standings
      const standWrap = el('league-standings-wrap');
      const tblHtml = _standingsTableHtml(standingsRes.data);
      standWrap.innerHTML = tblHtml || '<div class="pub-loading">Нет данных</div>';

      // Upcoming matches
      const matchesEl = el('league-matches');
      if (matchesRes.data && matchesRes.data.length) {
        matchesEl.innerHTML = matchesRes.data.map(matchCardHtml).join('');
      } else {
        matchesEl.innerHTML = emptyHtml('\ud83d\udcc5', 'Нет предстоящих матчей', '');
      }

      // Recent results — rendered as match cards with WIN/LOSS badges
      const resultsEl = el('league-results');
      if (resultsRes.data && resultsRes.data.length) {
        resultsEl.innerHTML = resultsRes.data.map(matchCardHtml).join('');
      } else {
        resultsEl.innerHTML = emptyHtml('\ud83d\udcdd', 'Нет результатов', '');
      }
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadLeagueDetail error', e);
      el('league-standings-wrap').innerHTML = errorHtml('Ошибка загрузки', 'league');
    }
  }

  /* ---------- About ---------- */
  async function loadAbout() {
    try {
      const [statsRes, leaguesRes] = await Promise.all([
        api('/stats', { days: 90 }),
        leaguesCache ? Promise.resolve({ data: leaguesCache }) : api('/leagues'),
      ]);
      if (!leaguesCache) leaguesCache = leaguesRes.data;
      const s = statsRes.data;
      animateValue(el('about-leagues'), leaguesRes.data ? leaguesRes.data.length : 0, { decimals: 0 });
      animateValue(el('about-bets'), s.total_bets || 0, { decimals: 0, thousands: true });
      var roiEl = el('about-roi');
      roiEl.className = 'pub-stat-value' + (s.roi > 0 ? '' : s.roi < 0 ? ' negative' : ' neutral');
      animateValue(roiEl, s.roi || 0, { suffix: '%', decimals: 1 });
      animateValue(el('about-winrate'), s.win_rate || 0, { suffix: '%', decimals: 1 });
    } catch (e) {
      if (!_isAbort(e)) console.error('loadAbout error', e);
    }
  }

  /* ---------- Router ---------- */
  const sections = ['home', 'matches', 'standings', 'analytics', 'league', 'about'];
  const loaders = { home: loadHome, matches: loadMatches, standings: loadStandings, analytics: loadAnalytics, league: loadLeagueDetail, about: loadAbout };

  function navigate(hash) {
    _abortNav();
    let target = (hash || '#home').replace('#', '');
    if (!sections.includes(target)) target = 'home';
    if (target === 'league' && !currentLeagueId) target = 'home';
    // Reset league state when leaving league detail
    if (target !== 'league') currentLeagueId = null;

    sections.forEach(s => {
      const sec = el(s);
      if (sec) sec.classList.toggle('active', s === target);
    });

    document.querySelectorAll('.pub-nav-link').forEach(link => {
      link.classList.toggle('active', link.dataset.section === target);
    });
    // Update bottom nav tabs
    document.querySelectorAll('.pub-bottom-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.section === target);
    });

    // Close mobile nav
    const nav = el('pub-nav');
    if (nav) nav.classList.remove('open');

    if (loaders[target]) loaders[target]();
  }

  /* ---------- Events ---------- */
  window.addEventListener('hashchange', () => navigate(location.hash));

  document.addEventListener('click', (e) => {
    const target = e.target;

    // Sortable table headers
    const sortTh = target.closest('th[data-sort]');
    if (sortTh && _resultsCache) {
      var col = sortTh.dataset.sort;
      if (_resultsSort.col === col) {
        _resultsSort.dir = _resultsSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        _resultsSort.col = col;
        _resultsSort.dir = col === 'kickoff' ? 'desc' : 'desc';
      }
      _store('resultsSort', _resultsSort);
      _renderSortedResults();
      _updateSortHeaders();
      return;
    }

    // Retry button
    const retryBtn = target.closest('[data-retry]');
    if (retryBtn) {
      const action = retryBtn.dataset.retry;
      if (retryActions[action]) retryActions[action]();
      return;
    }

    // CSV export results
    if (target.id === 'export-results-csv' || target.closest('#export-results-csv')) {
      var csvBtn = target.id === 'export-results-csv' ? target : target.closest('#export-results-csv');
      if (!_guardBtn(csvBtn, 1500)) return;
      exportResultsCsv();
      return;
    }

    // Chart PNG download
    const dlBtn = target.closest('[data-download-chart]');
    if (dlBtn) {
      _downloadPubChart(dlBtn.dataset.downloadChart);
      return;
    }

    // Period selector (debounced — rapid clicks only fire last one)
    const periodBtn = target.closest('[data-pub-days]');
    if (periodBtn) {
      pubDays = parseInt(periodBtn.dataset.pubDays, 10) || 90;
      _store('days', pubDays);
      document.querySelectorAll('.pub-period-btn[data-pub-days]').forEach(b => b.classList.toggle('active', b === periodBtn));
      const lbl = el('an-roi-label');
      if (lbl) lbl.textContent = 'ROI за ' + (pubDays >= 365 ? 'год' : pubDays + ' дней');
      clearTimeout(_periodDebounce);
      _periodDebounce = setTimeout(function() {
        const active = sections.find(s => el(s) && el(s).classList.contains('active'));
        if (active && loaders[active]) loaders[active]();
      }, 250);
      return;
    }

    // Mobile toggle
    if (target.closest('#mobile-toggle')) {
      const nav = el('pub-nav');
      if (nav) {
        nav.classList.toggle('open');
        const btn = el('mobile-toggle');
        if (btn) btn.setAttribute('aria-label', nav.classList.contains('open') ? 'Закрыть меню' : 'Открыть меню');
      }
      return;
    }

    // Favorite league toggle
    const favBtn = target.closest('[data-fav-league]');
    if (favBtn) {
      e.stopPropagation();
      toggleFavoriteLeague(favBtn.dataset.favLeague);
      if (leaguesCache) renderHomeLeagues(leaguesCache);
      toast(_isFav(favBtn.dataset.favLeague) ? 'Лига добавлена в избранное' : 'Лига убрана из избранного', 'success');
      return;
    }

    // League card click → navigate to league detail page
    const leagueCard = target.closest('.pub-league-card');
    if (leagueCard) {
      const lid = leagueCard.dataset.leagueId;
      if (lid) {
        currentLeagueId = lid;
        location.hash = '#league';
      }
      return;
    }

    // Match card click → modal
    const matchCard = target.closest('.pub-match-card[data-fixture-id]');
    if (matchCard && matchCard.dataset.fixtureId) {
      openMatchModal(matchCard.dataset.fixtureId);
      return;
    }

    // Modal share
    if (target.id === 'pub-modal-share-btn' || target.closest('#pub-modal-share-btn')) {
      shareMatch();
      return;
    }

    // Modal close
    if (target.id === 'pub-modal-close-btn' || target.closest('#pub-modal-close-btn')) {
      closeMatchModal();
      return;
    }
    if (target.classList.contains('pub-modal-overlay')) {
      closeMatchModal();
      return;
    }

    // Pagination
    const pageBtn = target.closest('.pub-pagination button');
    if (pageBtn && !pageBtn.disabled) {
      const page = parseInt(pageBtn.dataset.page, 10);
      matchesState.offset = page * matchesState.limit;
      fetchMatches();
      return;
    }
  });

  // Escape closes modal, Enter/Space activates role=button elements
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeMatchModal(); return; }
    if (e.key === 'Tab') { _trapFocus(e); return; }
    if (e.key === 'Enter' || e.key === ' ') {
      const t = e.target;
      if (t.getAttribute('role') === 'button' || t.classList.contains('pub-match-card')) {
        e.preventDefault();
        t.click();
      }
    }
  });

  // Standings league filter
  const standingsLeagueSel = el('standings-league');
  if (standingsLeagueSel) {
    standingsLeagueSel.addEventListener('change', () => {
      if (standingsLeagueSel.value) fetchStandings(standingsLeagueSel.value);
    });
  }

  // League filter
  const filterLeague = el('filter-league');
  if (filterLeague) {
    // Restore saved league filter
    if (matchesState.league) filterLeague.value = matchesState.league;
    filterLeague.addEventListener('change', () => {
      matchesState.league = filterLeague.value;
      matchesState.offset = 0;
      _store('league', matchesState.league);
      fetchMatches();
    });
  }

  // Resize chart
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (el('analytics') && el('analytics').classList.contains('active')) {
        loadAnalytics();
      }
    }, 300);
  });

  // Table scroll indicators
  function _checkTableScroll() {
    document.querySelectorAll('.pub-results-table-wrap').forEach(function(wrap) {
      wrap.classList.toggle('has-scroll', wrap.scrollWidth > wrap.clientWidth + 4);
    });
  }
  window.addEventListener('resize', _checkTableScroll);
  new MutationObserver(_checkTableScroll).observe(document.body, { childList: true, subtree: true });

  // Connection status ping
  (function() {
    var dot = el('conn-dot');
    if (!dot) return;
    function ping() {
      fetch('/api/public/v1/leagues', { method: 'HEAD' })
        .then(function(r) { dot.className = 'pub-conn-dot ' + (r.ok ? 'ok' : 'offline'); })
        .catch(function() { dot.className = 'pub-conn-dot offline'; });
    }
    ping();
    var connTimer = setInterval(ping, 45000);
    document.addEventListener('visibilitychange', function() {
      if (document.hidden) { clearInterval(connTimer); }
      else { ping(); connTimer = setInterval(ping, 45000); }
    });
  })();

  // Scroll-to-top button
  (function() {
    var btn = el('scroll-top-btn');
    if (!btn) return;
    var scrollTick = false;
    window.addEventListener('scroll', function() {
      if (scrollTick) return;
      scrollTick = true;
      requestAnimationFrame(function() {
        btn.classList.toggle('visible', window.scrollY > 600);
        scrollTick = false;
      });
    }, { passive: true });
    btn.addEventListener('click', function() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  })();

  /* ---------- Init ---------- */
  // Restore period button active state
  document.querySelectorAll('.pub-period-btn[data-pub-days]').forEach(function(b) {
    b.classList.toggle('active', parseInt(b.dataset.pubDays, 10) === pubDays);
  });
  var roiLabel = el('an-roi-label');
  if (roiLabel) roiLabel.textContent = 'ROI за ' + (pubDays >= 365 ? 'год' : pubDays + ' дней');

  navigate(location.hash || '#home');

  // Auto-open match modal from shared URL (?fixture=123)
  (function() {
    var params = new URLSearchParams(location.search);
    var fid = params.get('fixture');
    if (fid) openMatchModal(fid);
  })();
})();
