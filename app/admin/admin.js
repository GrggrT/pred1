(() => {
  'use strict';

  /* ========== Config ========== */
  const STORAGE_KEY = 'pred1_admin_token';
  const PICK_LABELS = {
    HOME_WIN: 'Дома', DRAW: 'Ничья', AWAY_WIN: 'Гости',
    H: 'Дома', D: 'Ничья', A: 'Гости',
    OVER_2_5: 'Тотал Б 2.5', UNDER_2_5: 'Тотал М 2.5',
    OVER_1_5: 'Тотал Б 1.5', UNDER_1_5: 'Тотал М 1.5',
    OVER_3_5: 'Тотал Б 3.5', UNDER_3_5: 'Тотал М 3.5',
    BTTS_YES: 'Обе забьют — Да', BTTS_NO: 'Обе забьют — Нет',
    DC_1X: 'Двойной шанс 1X', DC_X2: 'Двойной шанс X2', DC_12: 'Двойной шанс 12',
  };
  const JOB_LABELS = {
    full: 'Полный пайплайн', sync_data: 'Синхронизация данных',
    compute_indices: 'Расчёт индексов', build_predictions: 'Построение прогнозов',
    evaluate_results: 'Оценка результатов', maintenance: 'Обслуживание',
    rebuild_elo: 'Пересчёт ELO', quality_report: 'Отчёт качества',
    fit_dixon_coles: 'Dixon-Coles', snapshot_autofill: 'Авто-снапшоты',
  };
  const MARKET_LABELS = {
    '1X2': '1X2', 'TOTAL': 'Тотал 2.5', 'TOTAL_1_5': 'Тотал 1.5',
    'TOTAL_3_5': 'Тотал 3.5', 'BTTS': 'Обе забьют', 'DOUBLE_CHANCE': 'Двойной шанс',
    '1x2': '1X2', 'total': 'Тотал 2.5', 'total_1_5': 'Тотал 1.5',
    'total_3_5': 'Тотал 3.5', 'btts': 'Обе забьют', 'double_chance': 'Двойной шанс',
  };
  const PAGE_TITLES = {
    operations: ['Операции', 'Мониторинг и управление'],
    'admin-matches': ['Матчи', 'Полный список прогнозов'],
    publishing: ['Публикации', 'Управление публикациями'],
    quality: ['Качество', 'Метрики и калибровка модели'],
    system: ['Система', 'Задания, БД и модель'],
  };

  let token = '';
  let refreshTimer = null;
  let opsDays = 30;
  let _lastMatchesData = [];
  let _lastQualityLeagues = [];
  let _matchSort = { key: '', dir: '' };
  let _leagueSort = { key: '', dir: '' };

  /* ========== Section Cache ========== */
  const _sectionCache = {};
  const CACHE_TTL = { operations: 15000, 'admin-matches': 30000, publishing: 20000, quality: 60000, system: 60000 };

  function cacheGet(section) {
    const c = _sectionCache[section];
    if (!c) return null;
    if (Date.now() - c.ts > (CACHE_TTL[section] || 30000)) { delete _sectionCache[section]; return null; }
    return c.data;
  }

  function cacheSet(section, data) {
    _sectionCache[section] = { data, ts: Date.now() };
  }

  function cacheInvalidate(section) {
    if (section) delete _sectionCache[section];
    else Object.keys(_sectionCache).forEach(k => delete _sectionCache[k]);
  }

  /* ========== Helpers ========== */
  function el(id) { return document.getElementById(id); }
  function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  function _guardBtn(btn, ms) {
    if (!btn || btn.disabled) return false;
    btn.disabled = true;
    setTimeout(function() { btn.disabled = false; }, ms || 2000);
    return true;
  }

  function formatDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function timeAgo(iso) {
    if (!iso) return '—';
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return 'только что';
    if (diff < 3600) return Math.floor(diff / 60) + ' мин назад';
    if (diff < 86400) return Math.floor(diff / 3600) + ' ч назад';
    return Math.floor(diff / 86400) + ' д назад';
  }

  function _logoPlaceholder(alt, size) {
    const letter = alt ? alt.charAt(0).toUpperCase() : '?';
    return `<span style="display:inline-flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;background:var(--surface-3);border-radius:50%;font-size:${Math.round(size * 0.45)}px;color:var(--text-muted);font-weight:600">${esc(letter)}</span>`;
  }

  function logoImg(url, alt, size) {
    size = size || 28;
    if (!url) return _logoPlaceholder(alt, size);
    return `<img src="${esc(url)}" alt="${esc(alt)}" width="${size}" height="${size}" loading="lazy" onerror="this.outerHTML=this.dataset.fb" data-fb="${esc(_logoPlaceholder(alt, size))}">`;
  }

  function statusBadge(status) {
    const cls = { WIN: 'adm-badge-win', LOSS: 'adm-badge-loss', PENDING: 'adm-badge-pending', VOID: 'adm-badge-void' };
    const label = { WIN: 'Выигрыш', LOSS: 'Проигрыш', PENDING: 'Ожидание', VOID: 'Отмена' };
    return `<span class="adm-badge ${cls[status] || 'adm-badge-pending'}">${label[status] || status || '—'}</span>`;
  }

  /* ========== Skeleton Helper ========== */
  function skeletonHtml(n, height) {
    height = height || 60;
    let html = '';
    for (let i = 0; i < n; i++) html += '<div class="adm-skeleton" style="height:' + height + 'px;margin-bottom:8px"></div>';
    return html;
  }

  function errorHtml(msg, section) {
    return '<div class="adm-loading" style="color:var(--accent-danger)">' + esc(msg) +
      (section ? ' <button class="adm-btn adm-btn-sm adm-btn-secondary" onclick="location.hash=\'#' + section + '\';location.reload()">Повторить</button>' : '') + '</div>';
  }

  function skeletonKpi(n) {
    let html = '<div class="adm-skeleton-kpi">';
    for (let i = 0; i < (n || 6); i++) html += '<div class="adm-skeleton adm-skeleton-kpi-card"></div>';
    return html + '</div>';
  }

  /* ========== Sort Helper ========== */
  function _sortRows(rows, sortState, key, getVal) {
    if (sortState.key === key) {
      sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      sortState.key = key;
      sortState.dir = 'desc';
    }
    const dir = sortState.dir === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      const va = getVal(a), vb = getVal(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'string') return va.localeCompare(vb) * dir;
      return (va - vb) * dir;
    });
  }

  function _sortArrow(sortState, key) {
    if (sortState.key !== key) return '';
    return sortState.dir === 'asc' ? ' &#x25B2;' : ' &#x25BC;';
  }

  /* ========== API ========== */
  const HTTP_ERRORS = {
    400: 'Некорректный запрос',
    403: 'Доступ запрещён',
    404: 'Не найдено',
    429: 'Слишком много запросов, подождите',
    500: 'Ошибка сервера',
    502: 'Сервер недоступен',
    503: 'Сервис временно недоступен',
  };

  let _navController = null;

  function _abortNav() {
    if (_navController) { try { _navController.abort(); } catch (e) {} }
    _navController = new AbortController();
    return _navController.signal;
  }

  function _isAbort(e) { return e && e.name === 'AbortError'; }

  async function api(path, opts) {
    opts = opts || {};
    const url = new URL('/api/v1' + path, location.origin);
    if (opts.params) {
      for (const [k, v] of Object.entries(opts.params)) {
        if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, String(v));
      }
    }
    const headers = { 'X-Admin-Token': token };
    if (opts.method === 'POST') headers['Content-Type'] = 'application/json';
    const signal = opts.signal || (_navController ? _navController.signal : undefined);
    const resp = await fetch(url, {
      method: opts.method || 'GET',
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal,
    });
    if (resp.status === 403) {
      showAuth();
      throw new Error('Сессия истекла');
    }
    if (!resp.ok) {
      const msg = HTTP_ERRORS[resp.status] || ('Ошибка ' + resp.status);
      throw new Error(msg);
    }
    const total = resp.headers.get('X-Total-Count');
    const data = await resp.json();
    return { data, total: total !== null ? parseInt(total, 10) : null };
  }

  /* ========== Notifications ========== */
  function notify(msg, type, duration) {
    type = type || 'info';
    if (!duration) duration = type === 'error' ? 7000 : 3000;
    const region = el('notification-region');
    const toast = document.createElement('div');
    toast.className = `adm-toast ${type}`;
    toast.textContent = msg;
    region.appendChild(toast);
    setTimeout(() => toast.remove(), duration);
  }

  /* ========== Animated Counter ========== */
  const _prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const _numFmt = typeof Intl !== 'undefined' ? new Intl.NumberFormat('ru-RU') : null;

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
      const p = showSign && end > 0.05 ? '+' : '';
      element.textContent = p + _fmtNum(end, decimals, thousands) + suffix;
      return;
    }
    var dur = opts.duration || 500;
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

  /* ========== Confirm Dialog ========== */
  function showConfirm(title, message, onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'adm-modal-overlay adm-confirm-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    const dlg = document.createElement('div');
    dlg.className = 'adm-modal adm-confirm-dialog';
    dlg.innerHTML = '<div class="adm-modal-header"><h2>' + esc(title) + '</h2></div>' +
      '<div class="adm-modal-body"><p style="margin:0;color:var(--text-secondary)">' + esc(message) + '</p></div>' +
      '<div class="adm-confirm-actions">' +
      '<button class="adm-btn adm-btn-secondary" data-confirm="cancel">Отмена</button>' +
      '<button class="adm-btn adm-btn-primary adm-btn-danger" data-confirm="ok">Подтвердить</button></div>';
    overlay.appendChild(dlg);
    document.body.appendChild(overlay);
    const cleanup = () => overlay.remove();
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay || e.target.closest('[data-confirm="cancel"]')) { cleanup(); return; }
      if (e.target.closest('[data-confirm="ok"]')) { cleanup(); onConfirm(); }
    });
    const onEsc = (e) => { if (e.key === 'Escape') { cleanup(); document.removeEventListener('keydown', onEsc); } };
    document.addEventListener('keydown', onEsc);
  }

  /* ========== Auth ========== */
  function showAuth() {
    el('auth-container').classList.remove('hidden');
    el('main-app').classList.add('hidden');
    token = '';
    stopAutoRefresh();
    setTimeout(() => { const inp = el('admin-token'); if (inp) inp.focus(); }, 100);
  }

  function showApp() {
    el('auth-container').classList.add('hidden');
    el('main-app').classList.remove('hidden');
    navigate(location.hash || '#operations');
    startAutoRefresh();
  }

  async function tryAuth(inputToken) {
    try {
      const resp = await fetch('/api/v1/meta', { headers: { 'X-Admin-Token': inputToken } });
      if (resp.ok) {
        token = inputToken;
        localStorage.setItem(STORAGE_KEY, token);
        el('auth-error').classList.add('hidden');
        showApp();
        return true;
      }
      el('auth-error').textContent = 'Неверный токен';
      el('auth-error').classList.remove('hidden');
      return false;
    } catch (e) {
      el('auth-error').textContent = 'Ошибка подключения';
      el('auth-error').classList.remove('hidden');
      return false;
    }
  }

  /* ========== Router ========== */
  const SECTIONS = ['operations', 'admin-matches', 'publishing', 'quality', 'system'];
  const LOADERS = {
    operations: loadOperations,
    'admin-matches': loadMatches,
    publishing: loadPublishing,
    quality: loadQuality,
    system: loadSystem,
  };

  let _currentSection = 'operations';
  const _scrollPositions = {};

  function navigate(hash) {
    _abortNav();
    let target = (hash || '#operations').replace('#', '');
    if (!SECTIONS.includes(target)) target = 'operations';

    // Save scroll position of current section
    _scrollPositions[_currentSection] = window.scrollY;

    SECTIONS.forEach(s => {
      const sec = el(s);
      if (sec) sec.classList.toggle('active', s === target);
    });

    document.querySelectorAll('.adm-nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.section === target);
    });

    const titles = PAGE_TITLES[target] || ['', ''];
    el('page-title').textContent = titles[0];
    el('page-subtitle').textContent = titles[1];

    // Close mobile sidebar
    el('sidebar').classList.remove('open');

    _currentSection = target;

    if (LOADERS[target]) LOADERS[target]();

    // Restore scroll position
    requestAnimationFrame(() => {
      window.scrollTo(0, _scrollPositions[target] || 0);
    });
  }

  window.addEventListener('hashchange', () => navigate(location.hash));

  /* ========== Auto-refresh ========== */
  function startAutoRefresh() {
    stopAutoRefresh();
    refreshTimer = setInterval(() => {
      if (el('fixture-modal') && !el('fixture-modal').classList.contains('hidden')) return;
      const active = SECTIONS.find(s => el(s) && el(s).classList.contains('active'));
      if (active && LOADERS[active]) LOADERS[active]();
    }, 30000);
  }

  function stopAutoRefresh() {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  }

  /* ========== OPERATIONS ========== */
  async function loadOperations(force) {
    if (!force) { const cached = cacheGet('operations'); if (cached) { _renderOperationsFromCache(cached); return; } }
    el('ops-kpis').innerHTML = skeletonKpi(6);
    el('ops-priority-feed').innerHTML = skeletonHtml(3, 80);
    try {
      const [dashRes, freshRes, modelRes, picksRes, totalsRes] = await Promise.all([
        api('/dashboard', { params: { days: opsDays } }),
        api('/freshness'),
        api('/model/status').catch(() => ({ data: {} })),
        api('/picks', { params: { sort: 'ev_desc', limit: 8 } }),
        api('/picks/totals', { params: { sort: 'ev_desc', limit: 8 } }).catch(() => ({ data: [] })),
      ]);
      const allPicks = [...(picksRes.data || []).map(p => Object.assign(p, { _market: '1X2' })),
                         ...(totalsRes.data || []).map(p => Object.assign(p, { _market: MARKET_LABELS[p.market] || p.market || 'Тотал' }))];
      allPicks.sort((a, b) => (b.ev || 0) - (a.ev || 0));
      const payload = { dash: dashRes.data, fresh: freshRes.data, model: modelRes.data, picks: allPicks.slice(0, 10) };
      cacheSet('operations', payload);
      _renderOperationsFromCache(payload);
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadOperations', e);
      notify('Ошибка загрузки операций', 'error');
      el('ops-kpis').innerHTML = errorHtml('Ошибка загрузки', 'operations');
    }
  }

  function _renderOperationsFromCache(c) {
    renderHealth(c.fresh, c.model);
    renderKpis(c.dash);
    renderPriorityFeed(c.picks);
  }

  function renderHealth(data, modelData) {
    const cont = el('ops-health');
    const items = [];

    // Sync freshness
    if (data && data.sync_data) {
      const lastOk = data.sync_data.last_ok;
      const lastAny = data.sync_data.last_any;
      const finishedAt = lastOk ? lastOk.finished_at : (lastAny ? lastAny.finished_at : null);
      const ago = timeAgo(finishedAt);
      const hasError = lastAny && lastAny.status !== 'ok';
      items.push({ cls: hasError ? 'warn' : 'ok', text: 'Синхр: ' + ago });
    }

    // Data freshness from max timestamps
    if (data && data.max) {
      const m = data.max;
      // Predictions
      const predAgo = timeAgo(m.predictions_created_at);
      const predOk = !!m.predictions_created_at;
      items.push({ cls: predOk ? 'ok' : 'warn', text: 'Прогнозы: ' + predAgo });
      // Odds
      const oddsAgo = timeAgo(m.odds_fetched_at);
      items.push({ cls: m.odds_fetched_at ? 'ok' : 'warn', text: 'Кэфы: ' + oddsAgo });
      // Standings
      const standAgo = timeAgo(m.standings_updated_at);
      items.push({ cls: m.standings_updated_at ? 'ok' : 'warn', text: 'Таблицы: ' + standAgo });
    }

    // API Football quota
    if (modelData && modelData.api_football) {
      const af = modelData.api_football;
      const used = af.today ? af.today.requests : 0;
      const limit = af.daily_limit || 75000;
      const pct = Math.round((used / limit) * 100);
      const cls = af.blocked ? 'error' : pct > 80 ? 'warn' : 'ok';
      items.push({ cls: cls, text: 'API: ' + used.toLocaleString() + '/' + limit.toLocaleString() + ' (' + pct + '%)' });
    }

    // ELO status
    if (modelData && modelData.elo) {
      const e = modelData.elo;
      const cls = e.rebuild_needed ? 'warn' : e.unprocessed_total > 0 ? 'warn' : 'ok';
      items.push({ cls: cls, text: 'ELO: ' + (e.teams_with_elo || 0) + ' команд' + (e.unprocessed_total > 0 ? ', ' + e.unprocessed_total + ' необраб.' : '') });
    }

    var html = items.map(i => {
      return '<div class="adm-health-item"><span class="adm-health-dot ' + i.cls + '"></span> ' + esc(i.text) + '</div>';
    }).join('');
    var now = new Date();
    var _opsUpdatedAt = now.toISOString();
    html += '<div class="adm-health-item" style="margin-left:auto;font-size:11px;color:var(--text-muted)" title="' + formatDate(_opsUpdatedAt) + '">' + timeAgo(_opsUpdatedAt) + '</div>';
    cont.innerHTML = html;
  }

  function renderKpis(data) {
    const cont = el('ops-kpis');
    if (!data || !data.kpis) { cont.textContent = 'Нет данных'; return; }
    const kpis = data.kpis;
    const items = [
      { key: 'total_profit', label: 'Прибыль', fmt: v => (v >= 0 ? '+' : '') + v.toFixed(1), cls: v => v > 0 ? '' : v < 0 ? 'negative' : 'neutral' },
      { key: 'roi', label: 'ROI', fmt: v => v.toFixed(1) + '%', cls: v => v > 0 ? '' : v < 0 ? 'negative' : 'neutral' },
      { key: 'win_rate', label: 'Win Rate', fmt: v => v.toFixed(1) + '%', cls: () => '' },
      { key: 'total_bets', label: 'Ставок', fmt: v => String(v), cls: () => 'neutral' },
      { key: 'avg_bet', label: 'Ср. профит', fmt: v => (v >= 0 ? '+' : '') + v.toFixed(2), cls: v => v > 0 ? '' : v < 0 ? 'negative' : 'neutral' },
      { key: 'active_leagues', label: 'Лиги', fmt: v => String(v), cls: () => 'neutral' },
    ];
    const animTargets = [];
    let html = items.map((item, idx) => {
      const kpi = kpis[item.key];
      if (!kpi) return '';
      const val = kpi.value;
      const trend = kpi.trend || 0;
      const trendCls = trend > 0 ? 'up' : trend < 0 ? 'down' : '';
      const trendStr = trend !== 0 ? (trend > 0 ? '+' : '') + trend.toFixed(1) + (kpi.format === 'percentage' ? 'pp' : '%') : '';
      animTargets.push({ id: 'adm-kpi-' + idx, val: val, item: item });
      return '<div class="adm-kpi-card">' +
        '<div class="adm-kpi-label">' + esc(item.label) + '</div>' +
        '<div class="adm-kpi-value ' + item.cls(val) + '" id="adm-kpi-' + idx + '">0</div>' +
        (trendStr ? '<div class="adm-kpi-trend ' + trendCls + '">' + trendStr + '</div>' : '') +
        '</div>';
    }).join('');

    // Risk metrics row
    if (data.risk_metrics) {
      const rm = data.risk_metrics;
      html += '<div class="adm-kpi-card">' +
        '<div class="adm-kpi-label">Макс. выигрыш</div>' +
        '<div class="adm-kpi-value" style="font-size:22px">' + (rm.max_win != null ? '+' + rm.max_win.toFixed(1) : '—') + '</div></div>';
      html += '<div class="adm-kpi-card">' +
        '<div class="adm-kpi-label">Макс. проигрыш</div>' +
        '<div class="adm-kpi-value negative" style="font-size:22px">' + (rm.max_loss != null ? rm.max_loss.toFixed(1) : '—') + '</div></div>';
      html += '<div class="adm-kpi-card">' +
        '<div class="adm-kpi-label">Profit Factor</div>' +
        '<div class="adm-kpi-value ' + (rm.profit_factor >= 1 ? '' : 'negative') + '" style="font-size:22px">' + (rm.profit_factor != null ? rm.profit_factor.toFixed(2) : '—') + '</div></div>';
    }

    // All data from authenticated admin API, values escaped via esc()
    cont.innerHTML = html;
    // Animate KPI values
    animTargets.forEach(t => {
      var elem = el(t.id);
      if (!elem) return;
      var isPercent = t.item.key === 'roi' || t.item.key === 'win_rate';
      var isProfit = t.item.key === 'total_profit' || t.item.key === 'avg_bet';
      var isCount = t.item.key === 'total_bets' || t.item.key === 'active_leagues';
      animateValue(elem, t.val, {
        suffix: isPercent ? '%' : '',
        decimals: isCount ? 0 : isProfit ? 2 : 1,
        sign: isProfit,
        thousands: isCount,
      });
    });
  }

  function renderPriorityFeed(picks) {
    const cont = el('ops-priority-feed');
    if (!picks || !picks.length) { cont.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Нет приоритетных матчей. Запустите «Синхронизация» → «Прогнозы»</div>'; return; }
    cont.innerHTML = picks.map(p => pickCardHtml(p)).join('');
  }

  function pickCardHtml(p) {
    const pickLabel = PICK_LABELS[p.pick || p.selection_code] || p.pick || p.selection_code || '—';
    const ev = p.ev != null ? (p.ev * 100).toFixed(1) : null;
    const signal = p.signal_score != null ? (p.signal_score * 100).toFixed(0) : null;
    const odd = p.odd || p.initial_odd;
    const market = p._market || (p.pick && (p.pick.includes('OVER') || p.pick.includes('UNDER')) ? 'Тотал' : '1X2');
    return '<div class="adm-pick-card" data-fixture-id="' + p.fixture_id + '">' +
      '<div class="adm-pick-league">' + logoImg(p.league_logo_url, p.league, 14) + ' ' + esc(p.league || '') +
      '<span class="adm-badge adm-badge-market">' + esc(market) + '</span></div>' +
      '<div class="adm-pick-teams">' +
      '<div class="adm-pick-team">' + logoImg(p.home_logo_url, p.home || p.home_name, 28) + ' <span class="adm-pick-team-name">' + esc(p.home || p.home_name || '') + '</span></div>' +
      '<span class="adm-pick-vs">vs</span>' +
      '<div class="adm-pick-team">' + logoImg(p.away_logo_url, p.away || p.away_name, 28) + ' <span class="adm-pick-team-name">' + esc(p.away || p.away_name || '') + '</span></div>' +
      '</div>' +
      '<div class="adm-pick-meta">' +
      '<span class="adm-badge adm-badge-pick">' + esc(pickLabel) + '</span>' +
      (odd != null ? '<span class="adm-badge adm-badge-odd">' + Number(odd).toFixed(2) + '</span>' : '') +
      (ev !== null ? '<span class="adm-badge adm-badge-ev">EV ' + ev + '%</span>' : '') +
      (signal !== null ? '<span class="adm-badge adm-badge-signal">Сигнал ' + signal + '%</span>' : '') +
      '<span class="adm-pick-kickoff">' + formatDate(p.kickoff) + '</span>' +
      '</div></div>';
  }

  /* ========== MATCHES ========== */
  const matchState = { market: 'all', league: '', status: '', team: '', limit: 50, offset: 0 };
  let adminLeaguesLoaded = false;

  async function populateAdminLeagueFilter() {
    if (adminLeaguesLoaded) return;
    try {
      const res = await api('/freshness');
      const leagueIds = res.data && res.data.config ? res.data.config.league_ids : [];
      // Use public API to get league names
      const lres = await fetch('/api/public/v1/leagues');
      if (lres.ok) {
        const leagues = await lres.json();
        const sel = el('am-filter-league');
        if (sel && sel.options.length <= 1) {
          leagues.forEach(l => {
            const opt = document.createElement('option');
            opt.value = l.id;
            opt.textContent = l.name;
            sel.appendChild(opt);
          });
        }
        adminLeaguesLoaded = true;
      }
    } catch (_) {}
  }

  async function loadMatches() {
    el('am-matches-list').innerHTML = skeletonHtml(5, 40);
    try {
      populateAdminLeagueFilter();
      const params = {
        sort: 'kickoff_desc',
        limit: matchState.limit,
        offset: matchState.offset,
        market: matchState.market,
        all_time: true,
      };
      if (matchState.league) params.league_id = matchState.league;
      if (matchState.team) params.team = matchState.team;
      if (matchState.status) params.status = matchState.status;
      const res = await api('/bets/history', { params });
      _lastMatchesData = res.data || [];
      renderMatchesTable(_lastMatchesData, res.total);
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadMatches', e);
      el('am-matches-list').textContent = 'Ошибка загрузки';
      notify('Ошибка загрузки матчей', 'error');
    }
  }

  function renderMatchesTable(rows, total) {
    const cont = el('am-matches-list');
    if (!rows || !rows.length) {
      cont.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Нет матчей по текущим фильтрам. Попробуйте сбросить параметры</div>';
      el('am-pagination').innerHTML = '';
      return;
    }
    let html = '<table class="adm-table"><thead><tr>' +
      '<th class="adm-sortable" data-sort-match="kickoff">Дата' + _sortArrow(_matchSort, 'kickoff') + '</th>' +
      '<th>Матч</th><th>Лига</th>' +
      '<th class="adm-sortable" data-sort-match="market">Рынок' + _sortArrow(_matchSort, 'market') + '</th>' +
      '<th>Прогноз</th>' +
      '<th class="adm-sortable" data-sort-match="odd">Кф.' + _sortArrow(_matchSort, 'odd') + '</th>' +
      '<th class="adm-sortable" data-sort-match="ev">EV' + _sortArrow(_matchSort, 'ev') + '</th>' +
      '<th>Сигнал</th>' +
      '<th class="adm-sortable" data-sort-match="status">Статус' + _sortArrow(_matchSort, 'status') + '</th>' +
      '<th class="adm-sortable" data-sort-match="profit">Профит' + _sortArrow(_matchSort, 'profit') + '</th>' +
      '</tr></thead><tbody>';
    rows.forEach(r => {
      const pickLabel = PICK_LABELS[r.pick] || r.pick || '—';
      const ev = r.ev != null ? (r.ev * 100).toFixed(1) + '%' : '—';
      const evStyle = r.ev != null ? (r.ev >= 0.1 ? 'color:var(--accent-primary);font-weight:600' : r.ev >= 0 ? 'color:var(--accent-success)' : 'color:var(--text-muted)') : '';
      const signal = r.signal_score != null ? (r.signal_score * 100).toFixed(0) + '%' : '—';
      const profit = r.profit != null ? (r.profit >= 0 ? '+' : '') + r.profit.toFixed(2) : '—';
      const profitStyle = r.profit != null ? (r.profit >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
      const market = MARKET_LABELS[r.market] || r._market || r.market || '1X2';
      const score = r.score || (r.home_goals != null ? r.home_goals + '-' + r.away_goals : '');
      html += '<tr data-fixture-id="' + r.fixture_id + '">' +
        '<td>' + formatDate(r.kickoff) + '</td>' +
        '<td>' + logoImg(r.home_logo_url, r.home, 18) + ' ' + esc(r.home || '') + ' — ' + esc(r.away || '') + ' ' + logoImg(r.away_logo_url, r.away, 18) +
        (score ? ' <span style="color:var(--text-muted);font-size:var(--font-size-xs)">' + esc(score) + '</span>' : '') + '</td>' +
        '<td>' + esc(r.league || '') + '</td>' +
        '<td>' + esc(market) + '</td>' +
        '<td><span class="adm-badge adm-badge-pick">' + esc(pickLabel) + '</span></td>' +
        '<td>' + (r.odd != null ? Number(r.odd).toFixed(2) : '—') + '</td>' +
        '<td style="' + evStyle + '">' + ev + '</td>' +
        '<td>' + signal + '</td>' +
        '<td>' + statusBadge(r.status) + '</td>' +
        '<td style="' + profitStyle + ';font-weight:600">' + profit + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;

    // Pagination
    const pag = el('am-pagination');
    const totalCount = total || rows.length;
    const pages = Math.ceil(totalCount / matchState.limit);
    const current = Math.floor(matchState.offset / matchState.limit);
    if (pages <= 1) { pag.innerHTML = ''; return; }
    let pagHtml = '<button ' + (current === 0 ? 'disabled' : '') + ' data-page="' + (current - 1) + '">&laquo;</button>';
    for (let i = Math.max(0, current - 4); i < Math.min(pages, current + 6); i++) {
      pagHtml += '<button class="' + (i === current ? 'active' : '') + '" data-page="' + i + '">' + (i + 1) + '</button>';
    }
    pagHtml += '<button ' + (current >= pages - 1 ? 'disabled' : '') + ' data-page="' + (current + 1) + '">&raquo;</button>';
    pag.innerHTML = pagHtml;
  }

  /* ========== PUBLISHING ========== */
  async function loadPublishing(force) {
    if (!force) { const cached = cacheGet('publishing'); if (cached) { _renderPublishingFromCache(cached); return; } }
    el('pub-ready-list').innerHTML = skeletonHtml(3, 60);
    el('pub-metrics').innerHTML = skeletonHtml(1, 40);
    try {
      const histStatus = el('pub-hist-status') ? el('pub-hist-status').value : '';
      const histHours = el('pub-hist-hours') ? el('pub-hist-hours').value : '48';
      const [readyRes, metricsRes, globalHistRes] = await Promise.all([
        api('/picks', { params: { sort: 'kickoff_desc', limit: 20 } }),
        api('/publish/metrics').catch(() => ({ data: {} })),
        api('/publish/history/global', { params: { limit: 50, status: histStatus, hours: histHours } }).catch(() => ({ data: [] })),
      ]);
      const payload = { ready: readyRes.data, metrics: metricsRes.data, globalHistory: globalHistRes.data || [] };
      cacheSet('publishing', payload);
      _renderPublishingFromCache(payload);
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadPublishing', e);
      notify('Ошибка загрузки публикаций', 'error');
    }
  }

  function _renderPublishingFromCache(c) {
    renderPublishReady(c.ready);
    renderPublishMetrics(c.metrics);
    renderGlobalPublishHistory(c.globalHistory);
  }

  function renderPublishReady(picks) {
    const cont = el('pub-ready-list');
    if (!picks || !picks.length) { cont.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Нет матчей для публикации. Все прогнозы уже опубликованы или ожидают данных</div>'; return; }
    // Filter to NS (not started) fixtures only for publishing
    const nsPicks = picks.filter(p => p.fixture_status === 'NS' && p.status === 'PENDING');
    const displayPicks = nsPicks.length ? nsPicks : picks;
    cont.innerHTML = displayPicks.map(p => {
      const pickLabel = PICK_LABELS[p.pick || p.selection_code] || p.pick || p.selection_code || '—';
      const odd = p.odd || p.initial_odd;
      const ev = p.ev != null ? (p.ev * 100).toFixed(1) : null;
      return '<div class="adm-job-row">' +
        '<div style="flex:1">' +
        '<strong>' + esc(p.home || p.home_name) + ' — ' + esc(p.away || p.away_name) + '</strong>' +
        '<span style="color:var(--text-muted);font-size:var(--font-size-xs);margin-left:8px">' + esc(p.league || '') + ' &middot; ' + formatDate(p.kickoff) + '</span>' +
        '</div>' +
        '<span class="adm-badge adm-badge-pick">' + esc(pickLabel) + '</span>' +
        (odd != null ? '<span class="adm-badge adm-badge-odd">' + Number(odd).toFixed(2) + '</span>' : '') +
        (ev !== null ? '<span class="adm-badge adm-badge-ev">EV ' + ev + '%</span>' : '') +
        '<button class="adm-btn-sm adm-btn-secondary" data-action="preview-publish" data-fixture-id="' + p.fixture_id + '">Превью</button>' +
        '<button class="adm-btn-sm adm-btn-secondary" data-action="do-publish-dry" data-fixture-id="' + p.fixture_id + '">Dry Run</button>' +
        '<button class="adm-btn-sm adm-btn adm-btn-primary" data-action="do-publish" data-fixture-id="' + p.fixture_id + '">Опубликовать</button>' +
        '</div>';
    }).join('');
  }

  function renderPublishMetrics(data) {
    const cont = el('pub-metrics');
    if (!data || typeof data !== 'object' || !data.rows_total) { cont.textContent = 'Нет метрик'; return; }

    let html = '<div class="adm-kpi-grid" style="margin-bottom:12px">';
    html += kpiMini('Всего', data.rows_total || 0);
    html += kpiMini('Окно', (data.window_hours || 24) + 'ч');
    if (data.status_counts) {
      html += kpiMini('Успешных', data.status_counts.ok || 0);
      html += kpiMini('Dry run', data.status_counts.dry_run || 0);
      html += kpiMini('Пропущено', data.status_counts.skipped || 0);
    }
    html += '</div>';

    // Alert status
    if (data.alert) {
      const a = data.alert;
      if (a.triggered) {
        html += '<div class="adm-alert adm-alert-danger">Алерт: ' + esc(a.metric || '') + ' превысил порог ' + (a.threshold_pct || 0) + '%</div>';
      } else {
        html += '<div style="font-size:var(--font-size-xs);color:var(--accent-success);margin-top:4px">Алертов нет. Мониторинг: ' + esc(a.metric || '') + ' (порог ' + (a.threshold_pct || 0) + '%)</div>';
      }
    }

    // Rendering stats
    if (data.render_time_ms && data.render_time_ms.samples > 0) {
      html += '<div style="font-size:var(--font-size-xs);color:var(--text-muted);margin-top:8px">Рендеринг: avg ' + (data.render_time_ms.avg || 0).toFixed(0) + 'мс, p95 ' + (data.render_time_ms.p95 || 0).toFixed(0) + 'мс</div>';
    }

    cont.innerHTML = html;
  }

  /* ========== QUALITY ========== */
  async function loadQuality(force) {
    if (!force) { const cached = cacheGet('quality'); if (cached) { _renderQualityFromCache(cached); return; } }
    el('quality-market-perf').innerHTML = skeletonHtml(2, 40);
    el('quality-report').innerHTML = skeletonHtml(3, 40);
    el('quality-leagues').innerHTML = skeletonHtml(3, 40);
    el('quality-stats').innerHTML = skeletonKpi(4);
    try {
      const [qRes, leaguesRes, statsRes, mktRes, histRes] = await Promise.all([
        api('/quality_report'),
        api('/stats/combined/leagues').catch(() => ({ data: [] })),
        api('/stats').catch(() => ({ data: {} })),
        api('/market-stats', { params: { days: 0 } }).catch(() => ({ data: {} })),
        api('/bets/history', { params: { market: 'all', all_time: true, settled_only: true, sort: 'kickoff_desc', limit: 500 } }).catch(() => ({ data: [] })),
      ]);
      const payload = { report: qRes.data, leagues: leaguesRes.data, stats: statsRes.data, marketStats: mktRes.data, chartRows: histRes.data || [] };
      cacheSet('quality', payload);
      _renderQualityFromCache(payload);
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadQuality', e);
      el('quality-report').innerHTML = errorHtml('Ошибка загрузки', 'quality');
      notify('Ошибка загрузки качества', 'error');
    }
  }

  function _renderQualityFromCache(c) {
    renderMarketPerformance(c.marketStats);
    const filtered = _filterChartRows(c.chartRows);
    drawAdmRoiChart(el('adm-roi-chart'), filtered);
    drawAdmProfitChart(el('adm-profit-chart'), filtered);
    const report = c.report && c.report.report ? c.report.report : c.report;
    const mkt1x2 = report && (report['1x2'] || report['1X2']);
    const calBins = mkt1x2 && mkt1x2.calibration ? mkt1x2.calibration.bins : null;
    drawAdmCalibrationChart(el('adm-calibration-chart'), calBins);
    renderQualityReport(c.report);
    renderQualityLeagues(c.leagues);
    renderQualityStats(c.stats);
  }

  /* ========== ADMIN CHARTS ========== */
  // All chart data comes from authenticated admin API endpoints (trusted source)
  const _chartPoints = {};
  let _qualityChartMarket = '';

  function _getOrCreateTooltip() {
    let tip = document.querySelector('.adm-chart-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'adm-chart-tooltip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  function _bindChartTooltip(canvas, canvasId) {
    if (!canvas || canvas.dataset.tipBound) return;
    canvas.dataset.tipBound = '1';
    canvas.addEventListener('mousemove', (e) => {
      const info = _chartPoints[canvasId];
      if (!info || !info.pts.length) return;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      let best = null, bestDist = 20;
      info.pts.forEach((p, i) => {
        const sx = info.px(p, i), sy = info.py(p);
        const dist = Math.sqrt((mx - sx) ** 2 + (my - sy) ** 2);
        if (dist < bestDist) { bestDist = dist; best = { p, i }; }
      });
      const tip = _getOrCreateTooltip();
      if (best) {
        tip.textContent = info.formatTip(best.p, best.i);
        tip.style.display = 'block';
        tip.style.left = (e.clientX + 14) + 'px';
        tip.style.top = (e.clientY - 10) + 'px';
      } else {
        tip.style.display = 'none';
      }
    });
    canvas.addEventListener('mouseleave', () => {
      const tip = document.querySelector('.adm-chart-tooltip');
      if (tip) tip.style.display = 'none';
    });
  }

  function _filterChartRows(rows) {
    if (!_qualityChartMarket || !rows) return rows;
    return rows.filter(r => (r.market || '1X2').toUpperCase() === _qualityChartMarket.toUpperCase());
  }

  function _downloadChart(canvasId) {
    const canvas = el(canvasId);
    if (!canvas) return;
    try {
      const link = document.createElement('a');
      link.download = canvasId + '.png';
      link.href = canvas.toDataURL('image/png');
      link.click();
      notify('PNG сохранён', 'success');
    } catch (e) {
      notify('Ошибка экспорта графика', 'error');
    }
  }

  let _cc = null;
  function _chartColors() {
    if (_cc) return _cc;
    const s = getComputedStyle(document.documentElement);
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

  function _admChartSetup(canvas) {
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 10) return null;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);
    return { ctx, W: rect.width, H: rect.height };
  }

  function drawAdmRoiChart(canvas, rows) {
    const setup = _admChartSetup(canvas);
    if (!setup || !rows || rows.length < 2) {
      if (canvas) canvas.parentElement.textContent = 'Недостаточно данных (минимум 2 ставки). Попробуйте сбросить фильтр рынка.';
      delete _chartPoints['adm-roi-chart'];
      return;
    }
    const { ctx, W, H } = setup;
    const sorted = [...rows].sort((a, b) => new Date(a.kickoff) - new Date(b.kickoff));
    let cum = 0;
    const pts = sorted.map((r, i) => { cum += (r.profit || 0); return { x: i, roi: (cum / (i + 1)) * 100, date: r.kickoff }; });
    const pad = { t: 25, b: 25, l: 50, r: 15 };
    const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
    const minR = Math.min(0, ...pts.map(p => p.roi)), maxR = Math.max(0, ...pts.map(p => p.roi));
    const range = (maxR - minR) || 1;
    const px = i => pad.l + (i / Math.max(pts.length - 1, 1)) * cW;
    const py = v => pad.t + (1 - (v - minR) / range) * cH;
    ctx.strokeStyle = _chartColors().grid; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const v = minR + (range / 4) * i, y = py(v);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillStyle = _chartColors().muted; ctx.font = _chartColors().font; ctx.textAlign = 'right';
      ctx.fillText(v.toFixed(1) + '%', pad.l - 6, y + 4);
    }
    if (minR < 0 && maxR > 0) {
      ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(138,151,173,0.4)'; ctx.beginPath();
      ctx.moveTo(pad.l, py(0)); ctx.lineTo(W - pad.r, py(0)); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.beginPath(); ctx.strokeStyle = _chartColors().accent; ctx.lineWidth = 2.5; ctx.lineJoin = 'round';
    pts.forEach((p, i) => { if (i === 0) ctx.moveTo(px(i), py(p.roi)); else ctx.lineTo(px(i), py(p.roi)); });
    ctx.stroke();
    const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
    grad.addColorStop(0, 'rgba(182,243,61,0.15)'); grad.addColorStop(1, 'rgba(182,243,61,0)');
    ctx.lineTo(px(pts.length - 1), H - pad.b); ctx.lineTo(px(0), H - pad.b); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();
    const last = pts[pts.length - 1];
    ctx.beginPath(); ctx.arc(px(pts.length - 1), py(last.roi), 4, 0, Math.PI * 2);
    ctx.fillStyle = _chartColors().accent; ctx.fill();
    _chartPoints['adm-roi-chart'] = {
      pts, px: (p, i) => px(i), py: p => py(p.roi),
      formatTip: (p, i) => 'Ставка #' + (i + 1) + ' \u00b7 ' + formatDate(p.date) + ' \u00b7 ROI: ' + p.roi.toFixed(2) + '%',
    };
    _bindChartTooltip(canvas, 'adm-roi-chart');
  }

  function drawAdmProfitChart(canvas, rows) {
    const setup = _admChartSetup(canvas);
    if (!setup || !rows || rows.length < 2) {
      if (canvas) canvas.parentElement.textContent = 'Недостаточно данных (минимум 2 ставки). Попробуйте сбросить фильтр рынка.';
      delete _chartPoints['adm-profit-chart'];
      return;
    }
    const { ctx, W, H } = setup;
    const sorted = [...rows].sort((a, b) => new Date(a.kickoff) - new Date(b.kickoff));
    let cum = 0;
    const pts = sorted.map((r, i) => { cum += (r.profit || 0); return { x: i, val: cum, date: r.kickoff }; });
    const pad = { t: 25, b: 25, l: 50, r: 15 };
    const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
    const minV = Math.min(0, ...pts.map(p => p.val)), maxV = Math.max(0, ...pts.map(p => p.val));
    const range = (maxV - minV) || 1;
    const px = i => pad.l + (i / Math.max(pts.length - 1, 1)) * cW;
    const py = v => pad.t + (1 - (v - minV) / range) * cH;
    ctx.strokeStyle = _chartColors().grid; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const v = minV + (range / 4) * i, y = py(v);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillStyle = _chartColors().muted; ctx.font = _chartColors().font; ctx.textAlign = 'right';
      ctx.fillText(v.toFixed(1), pad.l - 6, y + 4);
    }
    if (minV < 0 && maxV > 0) {
      ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(138,151,173,0.4)'; ctx.beginPath();
      ctx.moveTo(pad.l, py(0)); ctx.lineTo(W - pad.r, py(0)); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.beginPath(); ctx.strokeStyle = _chartColors().secondary; ctx.lineWidth = 2.5; ctx.lineJoin = 'round';
    pts.forEach((p, i) => { if (i === 0) ctx.moveTo(px(i), py(p.val)); else ctx.lineTo(px(i), py(p.val)); });
    ctx.stroke();
    const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
    grad.addColorStop(0, 'rgba(56,189,248,0.15)'); grad.addColorStop(1, 'rgba(56,189,248,0)');
    ctx.lineTo(px(pts.length - 1), H - pad.b); ctx.lineTo(px(0), H - pad.b); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();
    const last = pts[pts.length - 1];
    ctx.beginPath(); ctx.arc(px(pts.length - 1), py(last.val), 4, 0, Math.PI * 2);
    ctx.fillStyle = _chartColors().secondary; ctx.fill();
    _chartPoints['adm-profit-chart'] = {
      pts, px: (p, i) => px(i), py: p => py(p.val),
      formatTip: (p, i) => 'Ставка #' + (i + 1) + ' \u00b7 ' + formatDate(p.date) + ' \u00b7 Профит: ' + (p.val >= 0 ? '+' : '') + p.val.toFixed(2),
    };
    _bindChartTooltip(canvas, 'adm-profit-chart');
  }

  function drawAdmCalibrationChart(canvas, bins) {
    const setup = _admChartSetup(canvas);
    if (!setup || !bins || !bins.length) {
      if (canvas) canvas.parentElement.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Нет данных калибровки. Запустите «Отчёт качества»</div>';
      delete _chartPoints['adm-calibration-chart'];
      return;
    }
    const { ctx, W, H } = setup;
    const pad = { t: 25, b: 35, l: 50, r: 15 };
    const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
    const px = v => pad.l + v * cW;
    const py = v => pad.t + (1 - v) * cH;
    ctx.setLineDash([6, 4]); ctx.strokeStyle = 'rgba(138,151,173,0.4)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(px(0), py(0)); ctx.lineTo(px(1), py(1)); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = _chartColors().grid; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const v = i / 4, y = py(v);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.fillStyle = _chartColors().muted; ctx.font = _chartColors().font; ctx.textAlign = 'right';
      ctx.fillText((v * 100).toFixed(0) + '%', pad.l - 6, y + 4);
    }
    ctx.fillStyle = _chartColors().muted; ctx.font = _chartColors().font; ctx.textAlign = 'center';
    ctx.fillText('Предсказанная вер.', pad.l + cW / 2, H - 4);
    ctx.save(); ctx.translate(12, pad.t + cH / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText('Факт. win rate', 0, 0); ctx.restore();
    const maxBets = Math.max(...bins.map(b => b.bets || 1));
    ctx.beginPath(); ctx.strokeStyle = _chartColors().accent; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    const validBins = bins.filter(b => b.avg_prob != null && b.win_rate != null);
    validBins.forEach((b, i) => {
      const x = px(b.avg_prob), y = py(b.win_rate);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    validBins.forEach(b => {
      const x = px(b.avg_prob), y = py(b.win_rate);
      const r = 3 + Math.min(5, ((b.bets || 1) / maxBets) * 5);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = _chartColors().accent; ctx.fill();
    });
    _chartPoints['adm-calibration-chart'] = {
      pts: validBins, px: b => px(b.avg_prob), py: b => py(b.win_rate),
      formatTip: b => 'Предск.: ' + (b.avg_prob * 100).toFixed(1) + '% \u00b7 Факт: ' + (b.win_rate * 100).toFixed(1) + '% \u00b7 Ставок: ' + (b.bets || 0),
    };
    _bindChartTooltip(canvas, 'adm-calibration-chart');
  }

  let _admChartResizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(_admChartResizeTimer);
    _admChartResizeTimer = setTimeout(() => {
      if (el('quality') && el('quality').classList.contains('active')) {
        const cached = cacheGet('quality');
        if (cached) {
          const filtered = _filterChartRows(cached.chartRows);
          drawAdmRoiChart(el('adm-roi-chart'), filtered);
          drawAdmProfitChart(el('adm-profit-chart'), filtered);
          const report = cached.report && cached.report.report ? cached.report.report : cached.report;
          const mkt = report && (report['1x2'] || report['1X2']);
          const calBins = mkt && mkt.calibration ? mkt.calibration.bins : null;
          drawAdmCalibrationChart(el('adm-calibration-chart'), calBins);
        }
      }
    }, 300);
  });

  function renderMarketPerformance(data) {
    const cont = el('quality-market-perf');
    if (!cont) return;
    if (!data || typeof data !== 'object' || !Object.keys(data).length) {
      cont.textContent = 'Нет данных';
      return;
    }
    const rows = Object.entries(data).sort((a, b) => a[0].localeCompare(b[0]));
    let totals = { settled: 0, wins: 0, losses: 0, profit: 0 };
    rows.forEach(([, v]) => { totals.settled += (v.settled || 0); totals.wins += (v.wins || 0); totals.losses += (v.losses || 0); totals.profit += (v.total_profit || 0); });
    // All data comes from admin API (authenticated), values are numeric — safe to render via esc()
    let html = '<table class="adm-table"><thead><tr><th>Рынок</th><th>Расчёт</th><th>W</th><th>L</th><th>Win%</th><th>ROI</th><th>Профит</th></tr></thead><tbody>';
    rows.forEach(([mkt, v]) => {
      const label = MARKET_LABELS[mkt] || mkt;
      const roi = v.roi || 0;
      const roiStyle = roi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)';
      const profitStyle = (v.total_profit || 0) >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)';
      html += '<tr><td><strong>' + esc(label) + '</strong></td><td>' + (v.settled || 0) + '</td><td>' + (v.wins || 0) + '</td><td>' + (v.losses || 0) + '</td><td>' + (v.win_rate || 0).toFixed(1) + '%</td><td style="' + roiStyle + ';font-weight:600">' + roi.toFixed(1) + '%</td><td style="' + profitStyle + ';font-weight:600">' + (v.total_profit >= 0 ? '+' : '') + (v.total_profit || 0).toFixed(2) + '</td></tr>';
    });
    const allRoi = totals.settled > 0 ? (totals.profit / totals.settled) * 100 : 0;
    const allWr = totals.settled > 0 ? (totals.wins / totals.settled) * 100 : 0;
    const allRoiStyle = allRoi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)';
    const allProfitStyle = totals.profit >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)';
    html += '<tr style="border-top:2px solid var(--border-color);font-weight:700"><td>ИТОГО</td><td>' + totals.settled + '</td><td>' + totals.wins + '</td><td>' + totals.losses + '</td><td>' + allWr.toFixed(1) + '%</td><td style="' + allRoiStyle + '">' + allRoi.toFixed(1) + '%</td><td style="' + allProfitStyle + '">' + (totals.profit >= 0 ? '+' : '') + totals.profit.toFixed(2) + '</td></tr>';
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderQualityReport(data) {
    const cont = el('quality-report');
    if (!data || typeof data !== 'object') { cont.textContent = 'Нет данных'; return; }

    // Real API: { cached, report: { "1x2": { summary, calibration, by_league, ... }, "total": {...} }, cache_ttl_seconds, cron }
    const report = data.report || data;
    const markets = Object.keys(report).filter(k =>
      typeof report[k] === 'object' && report[k] !== null && report[k].summary
    );
    const parts = [];

    markets.forEach(market => {
      const mdata = report[market];
      if (!mdata || !mdata.summary) return;
      const s = mdata.summary;
      const cal = mdata.calibration || {};

      let html = '<div style="margin-bottom:20px">';
      html += '<h4 style="margin:0 0 12px;color:var(--text-primary);font-size:var(--font-size-md)">' + esc(MARKET_LABELS[market.toUpperCase()] || MARKET_LABELS[market] || market.toUpperCase()) + '</h4>';
      html += '<div class="adm-kpi-grid" style="margin-bottom:12px">';
      html += kpiMini('Ставок', s.bets || 0);
      html += kpiMini('Win Rate', s.win_rate != null ? s.win_rate.toFixed(1) + '%' : '—');
      html += kpiMini('ROI', s.roi != null ? s.roi.toFixed(1) + '%' : '—', s.roi);
      html += kpiMini('Ср. кф.', s.avg_odd != null ? s.avg_odd.toFixed(2) : '—');
      html += kpiMini('Brier', cal.brier != null ? cal.brier.toFixed(4) : '—', null, 'Brier Score: чем ниже, тем точнее вероятности (0 = идеал)');
      html += kpiMini('LogLoss', cal.logloss != null ? cal.logloss.toFixed(4) : '—', null, 'Log Loss: штраф за уверенность в неверном исходе (чем ниже, тем лучше)');
      html += kpiMini('RPS', cal.rps != null ? cal.rps.toFixed(4) : '—', null, 'Ranked Probability Score: чем ниже, тем точнее распределение вероятностей');
      html += kpiMini('CLV охват', s.clv_cov_pct != null ? s.clv_cov_pct.toFixed(0) + '%' : '—', null, 'Closing Line Value: % ставок, где наш кф. был лучше закрывающей линии');
      html += kpiMini('CLV сред.', s.clv_avg_pct != null ? s.clv_avg_pct.toFixed(2) + '%' : '—', s.clv_avg_pct, 'Средний CLV: на сколько % наш кф. был лучше закрывающей линии. >0% = опережение рынка');
      html += '</div>';

      // Calibration bins
      if (cal.bins && cal.bins.length) {
        html += '<details style="margin-bottom:8px"><summary style="cursor:pointer;font-weight:600;font-size:var(--font-size-sm)">Калибровка</summary>';
        html += '<table class="adm-table" style="margin-top:8px"><thead><tr><th>Бин</th><th>Ставок</th><th>Ср. вер.</th><th>Win Rate</th><th title="Отклонение факт. win rate от предсказанной вероятности. Красный = > 10%">Откл.</th></tr></thead><tbody>';
        cal.bins.forEach(b => {
          const dev = b.avg_prob != null && b.win_rate != null ? (b.win_rate - b.avg_prob) : null;
          const devCls = dev != null ? (Math.abs(dev) > 0.1 ? 'color:var(--accent-danger)' : 'color:var(--accent-success)') : '';
          html += '<tr><td>' + esc(b.bin) + '</td><td>' + (b.bets || 0) + '</td><td>' + (b.avg_prob != null ? b.avg_prob.toFixed(3) : '—') + '</td><td>' + (b.win_rate != null ? b.win_rate.toFixed(3) : '—') + '</td><td style="' + devCls + '">' + (dev != null ? (dev >= 0 ? '+' : '') + dev.toFixed(3) : '—') + '</td></tr>';
        });
        html += '</tbody></table></details>';
      }

      // By league
      if (mdata.by_league && mdata.by_league.length) {
        html += '<details style="margin-bottom:8px"><summary style="cursor:pointer;font-weight:600;font-size:var(--font-size-sm)">По лигам</summary>';
        html += '<table class="adm-table" style="margin-top:8px"><thead><tr><th>Лига</th><th>Ставок</th><th>Win Rate</th><th>ROI</th><th>Ср. кф.</th></tr></thead><tbody>';
        mdata.by_league.forEach(l => {
          const roiStyle = l.roi != null ? (l.roi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
          html += '<tr><td>' + esc(l.league_name || 'ID ' + l.league_id) + '</td><td>' + (l.bets || 0) + '</td><td>' + (l.win_rate != null ? l.win_rate.toFixed(1) + '%' : '—') + '</td><td style="' + roiStyle + ';font-weight:600">' + (l.roi != null ? l.roi.toFixed(1) + '%' : '—') + '</td><td>' + (l.avg_odd != null ? l.avg_odd.toFixed(2) : '—') + '</td></tr>';
        });
        html += '</tbody></table></details>';
      }

      // By odds bucket
      if (mdata.by_odds_bucket && mdata.by_odds_bucket.length) {
        html += '<details style="margin-bottom:8px"><summary style="cursor:pointer;font-weight:600;font-size:var(--font-size-sm)">По коэффициентам</summary>';
        html += '<table class="adm-table" style="margin-top:8px"><thead><tr><th>Диапазон</th><th>Ставок</th><th>Win Rate</th><th>ROI</th></tr></thead><tbody>';
        mdata.by_odds_bucket.forEach(b => {
          const roiStyle = b.roi != null ? (b.roi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
          html += '<tr><td>' + esc(b.key) + '</td><td>' + (b.bets || 0) + '</td><td>' + (b.win_rate != null ? b.win_rate.toFixed(1) + '%' : '—') + '</td><td style="' + roiStyle + ';font-weight:600">' + (b.roi != null ? b.roi.toFixed(1) + '%' : '—') + '</td></tr>';
        });
        html += '</tbody></table></details>';
      }

      // By time to match
      if (mdata.by_time_to_match && Object.keys(mdata.by_time_to_match).length) {
        html += '<details style="margin-bottom:8px"><summary style="cursor:pointer;font-weight:600;font-size:var(--font-size-sm)">По времени до матча</summary>';
        html += '<table class="adm-table" style="margin-top:8px"><thead><tr><th>Период</th><th>Ставок</th><th>Win Rate</th><th>ROI</th><th>Ср. кф.</th><th>CLV%</th></tr></thead><tbody>';
        Object.entries(mdata.by_time_to_match).forEach(([key, b]) => {
          const roiStyle = b.roi != null ? (b.roi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
          const clvStyle = b.clv_avg_pct != null ? (b.clv_avg_pct >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
          html += '<tr><td>' + esc(key) + '</td><td>' + (b.bets || 0) + '</td><td>' + (b.win_rate != null ? b.win_rate.toFixed(1) + '%' : '—') + '</td><td style="' + roiStyle + ';font-weight:600">' + (b.roi != null ? b.roi.toFixed(1) + '%' : '—') + '</td><td>' + (b.avg_odd != null ? b.avg_odd.toFixed(2) : '—') + '</td><td style="' + clvStyle + '">' + (b.clv_avg_pct != null ? b.clv_avg_pct.toFixed(2) + '%' : '—') + '</td></tr>';
        });
        html += '</tbody></table></details>';
      }

      // Shadow filters (what-if scenarios)
      if (mdata.shadow_filters && Object.keys(mdata.shadow_filters).length) {
        html += '<details style="margin-bottom:8px"><summary style="cursor:pointer;font-weight:600;font-size:var(--font-size-sm)" title="Альтернативные фильтры: как изменились бы метрики при других порогах EV/сигнала">What-If сценарии</summary>';
        html += '<div style="display:grid;gap:8px;margin-top:8px">';
        Object.entries(mdata.shadow_filters).forEach(([filterName, f]) => {
          const label = esc(f.label || filterName);
          html += '<div style="background:var(--surface-2);border:1px solid var(--border-color);border-radius:var(--radius-sm);padding:12px">';
          html += '<div style="font-weight:600;font-size:var(--font-size-sm);margin-bottom:6px">' + label + '</div>';
          html += '<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:var(--font-size-xs)">';
          if (f.bets != null) html += '<span>Ставок: ' + f.bets + '</span>';
          if (f.roi != null) {
            const c = f.roi >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
            html += '<span style="color:' + c + ';font-weight:600">ROI: ' + (f.roi >= 0 ? '+' : '') + f.roi.toFixed(1) + '%</span>';
          }
          if (f.win_rate != null) html += '<span>WR: ' + f.win_rate.toFixed(1) + '%</span>';
          // Delta metrics
          if (f.delta_roi != null) {
            const dc = f.delta_roi >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
            html += '<span style="color:' + dc + '">\u0394ROI: ' + (f.delta_roi >= 0 ? '+' : '') + f.delta_roi.toFixed(1) + 'pp</span>';
          }
          if (f.delta_brier != null) {
            const dc = f.delta_brier <= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
            html += '<span style="color:' + dc + '">\u0394Brier: ' + (f.delta_brier >= 0 ? '+' : '') + f.delta_brier.toFixed(4) + '</span>';
          }
          if (f.delta_logloss != null) {
            const dc = f.delta_logloss <= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
            html += '<span style="color:' + dc + '">\u0394LogLoss: ' + (f.delta_logloss >= 0 ? '+' : '') + f.delta_logloss.toFixed(4) + '</span>';
          }
          if (f.delta_rps != null) {
            const dc = f.delta_rps <= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
            html += '<span style="color:' + dc + '">\u0394RPS: ' + (f.delta_rps >= 0 ? '+' : '') + f.delta_rps.toFixed(4) + '</span>';
          }
          if (f.delta_clv_avg_pct != null) {
            const dc = f.delta_clv_avg_pct >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)';
            html += '<span style="color:' + dc + '">\u0394CLV: ' + (f.delta_clv_avg_pct >= 0 ? '+' : '') + f.delta_clv_avg_pct.toFixed(2) + '%</span>';
          }
          html += '</div></div>';
        });
        html += '</div></details>';
      }

      html += '</div>';
      parts.push(html);
    });

    if (parts.length) {
      if (data.report && data.report.generated_at) {
        parts.unshift('<div style="font-size:var(--font-size-xs);color:var(--text-muted);margin-bottom:12px">Сгенерировано: ' + formatDate(data.report.generated_at) + (data.cached ? ' (кэш)' : '') + '</div>');
      }
      cont.innerHTML = parts.join('');
    } else {
      // Fallback for unexpected structure
      const pre = document.createElement('pre');
      pre.style.cssText = 'white-space:pre-wrap;font-size:var(--font-size-xs);color:var(--text-secondary);max-height:400px;overflow:auto';
      pre.textContent = JSON.stringify(data, null, 2);
      cont.textContent = '';
      cont.appendChild(pre);
    }
  }

  function kpiMini(label, value, numericVal, tooltip) {
    let cls = '';
    if (numericVal !== undefined && numericVal !== null) {
      cls = numericVal > 0 ? '' : numericVal < 0 ? ' negative' : ' neutral';
    }
    const titleAttr = tooltip ? ' title="' + esc(tooltip) + '"' : '';
    return `<div class="adm-kpi-card"${titleAttr}><div class="adm-kpi-label">${esc(label)}</div><div class="adm-kpi-value${cls}" style="font-size:22px">${value}</div></div>`;
  }

  function renderQualityLeagues(data) {
    const cont = el('quality-leagues');
    if (!data || !Array.isArray(data) || !data.length) { cont.textContent = 'Нет данных по лигам'; _lastQualityLeagues = []; return; }
    _lastQualityLeagues = data;
    let html = '<table class="adm-table"><thead><tr>' +
      '<th class="adm-sortable" data-sort-league="league">Лига' + _sortArrow(_leagueSort, 'league') + '</th>' +
      '<th>Период</th>' +
      '<th class="adm-sortable" data-sort-league="bets">Ставок' + _sortArrow(_leagueSort, 'bets') + '</th>' +
      '<th>W/L</th>' +
      '<th class="adm-sortable" data-sort-league="win_rate">Win Rate' + _sortArrow(_leagueSort, 'win_rate') + '</th>' +
      '<th class="adm-sortable" data-sort-league="roi">ROI' + _sortArrow(_leagueSort, 'roi') + '</th>' +
      '<th class="adm-sortable" data-sort-league="profit">Профит' + _sortArrow(_leagueSort, 'profit') + '</th>' +
      '</tr></thead><tbody>';
    data.forEach(r => {
      const roi = r.roi != null ? r.roi.toFixed(1) + '%' : '—';
      const roiStyle = r.roi != null ? (r.roi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
      const profit = r.total_profit != null ? r.total_profit : r.profit;
      const profitStr = profit != null ? (profit >= 0 ? '+' : '') + profit.toFixed(2) : '—';
      const profitStyle = profit != null ? (profit >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
      html += '<tr>' +
        '<td>' + esc(r.league || r.league_name || 'ID ' + (r.league_id || '')) + '</td>' +
        '<td>' + (r.days || 90) + 'д</td>' +
        '<td>' + (r.total_bets || r.bets || '—') + '</td>' +
        '<td>' + (r.wins || 0) + '/' + (r.losses || 0) + '</td>' +
        '<td>' + (r.win_rate != null ? r.win_rate.toFixed(1) + '%' : '—') + '</td>' +
        '<td style="' + roiStyle + ';font-weight:600">' + roi + '</td>' +
        '<td style="' + profitStyle + ';font-weight:600">' + profitStr + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderQualityStats(data) {
    const cont = el('quality-stats');
    if (!cont) return;
    if (!data || typeof data !== 'object') { cont.textContent = 'Нет данных'; return; }
    let html = '<div class="adm-kpi-grid">';
    html += kpiMini('Всего ставок', data.total_bets || 0);
    html += kpiMini('Побед', data.wins || 0);
    html += kpiMini('Поражений', data.losses || 0);
    html += kpiMini('Ожидание', data.pending || 0);
    html += kpiMini('Win Rate', data.win_rate != null ? data.win_rate.toFixed(1) + '%' : '—');
    html += kpiMini('ROI', data.roi != null ? data.roi.toFixed(1) + '%' : '—', data.roi);
    html += kpiMini('Профит', data.total_profit != null ? (data.total_profit >= 0 ? '+' : '') + data.total_profit.toFixed(2) : '—', data.total_profit);
    html += kpiMini('Avg Brier', data.avg_brier != null ? data.avg_brier.toFixed(4) : '—');
    html += kpiMini('Avg LogLoss', data.avg_log_loss != null ? data.avg_log_loss.toFixed(4) : '—');
    html += kpiMini('Ср. сигнал', data.avg_signal_score != null ? (data.avg_signal_score * 100).toFixed(0) + '%' : '—');
    html += kpiMini('Сильных', data.strong_signals || 0);
    html += '</div>';

    // Prob source metrics
    if (data.prob_source_metrics && data.prob_source_metrics.length) {
      html += '<div style="margin-top:12px"><h4 style="margin:0 0 8px;font-size:var(--font-size-sm)">По prob_source</h4>';
      html += '<table class="adm-table"><thead><tr><th>Source</th><th>Brier</th><th>LogLoss</th><th>N</th></tr></thead><tbody>';
      data.prob_source_metrics.forEach(m => {
        html += '<tr><td>' + esc(m.prob_source) + '</td><td>' + (m.brier != null ? m.brier.toFixed(4) : '—') + '</td><td>' + (m.logloss != null ? m.logloss.toFixed(4) : '—') + '</td><td>' + (m.n || 0) + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }

    // Signal score bins
    if (data.bins && data.bins.length) {
      html += '<div style="margin-top:12px"><h4 style="margin:0 0 8px;font-size:var(--font-size-sm)">По диапазонам сигнала</h4>';
      html += '<table class="adm-table"><thead><tr><th>Диапазон</th><th>ROI</th><th>Ставок</th></tr></thead><tbody>';
      data.bins.forEach(b => {
        const roiStyle = b.roi != null ? (b.roi >= 0 ? 'color:var(--accent-success)' : 'color:var(--accent-danger)') : '';
        html += '<tr><td>' + esc(b.bin) + '</td><td style="' + roiStyle + ';font-weight:600">' + (b.roi != null ? b.roi.toFixed(1) + '%' : '—') + '</td><td>' + (b.bets || 0) + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }

    cont.innerHTML = html;
  }

  /* ========== SYSTEM ========== */
  async function loadSystem(force) {
    if (!force) { const cached = cacheGet('system'); if (cached) { _renderSystemFromCache(cached); return; } }
    el('sys-jobs').innerHTML = skeletonHtml(4, 44);
    el('sys-runs').innerHTML = skeletonHtml(5, 36);
    el('sys-model').innerHTML = skeletonHtml(2, 50);
    try {
      const [statusRes, runsRes, modelRes] = await Promise.all([
        api('/jobs/status'),
        api('/jobs/runs', { params: { limit: 50 } }),
        api('/model/status'),
      ]);
      const payload = { status: statusRes.data, runs: runsRes.data, model: modelRes.data };
      cacheSet('system', payload);
      _renderSystemFromCache(payload);
    } catch (e) {
      if (_isAbort(e)) return;
      console.error('loadSystem', e);
      notify('Ошибка загрузки системы', 'error');
      const errMsg = errorHtml('Ошибка загрузки', 'system');
      el('sys-jobs').innerHTML = errMsg;
      el('sys-runs').innerHTML = errMsg;
      el('sys-model').innerHTML = errMsg;
    }
  }

  function _renderSystemFromCache(c) {
    renderJobs(c.status, c.runs);
    renderRecentRuns(c.runs);
    renderModel(c.model);
    renderAuditLog(c.runs);
  }

  function renderRecentRuns(runs) {
    const cont = el('sys-runs');
    if (!cont) return;
    const arr = Array.isArray(runs) ? runs : [];
    if (!arr.length) { cont.textContent = 'Нет запусков'; return; }
    let html = '<table class="adm-table"><thead><tr><th>Задание</th><th>Статус</th><th>Начало</th><th>Длительность</th><th>Триггер</th></tr></thead><tbody>';
    arr.slice(0, 15).forEach(r => {
      const stCls = r.status === 'ok' ? 'adm-badge-win' : r.status === 'failed' ? 'adm-badge-loss' : 'adm-badge-pending';
      const stLabel = r.status === 'ok' ? 'OK' : r.status === 'failed' ? 'Ошибка' : esc(r.status || '—');
      const dur = r.duration_seconds != null ? (r.duration_seconds < 1 ? '<1с' : Math.round(r.duration_seconds) + 'с') : '—';
      const trigger = r.triggered_by || '—';
      html += '<tr><td>' + esc(JOB_LABELS[r.job_name] || r.job_name) + '</td>' +
        '<td><span class="adm-badge ' + stCls + '">' + stLabel + '</span></td>' +
        '<td>' + formatDate(r.started_at) + '</td>' +
        '<td>' + dur + '</td>' +
        '<td>' + esc(trigger) + '</td></tr>';
      if (r.error) {
        html += '<tr><td colspan="5" style="color:var(--accent-danger);font-size:var(--font-size-xs);padding:4px 12px">' + esc(String(r.error).substring(0, 200)) + '</td></tr>';
      }
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderJobs(status, runs) {
    const cont = el('sys-jobs');
    const allJobs = ['sync_data', 'compute_indices', 'build_predictions', 'evaluate_results', 'maintenance', 'quality_report', 'rebuild_elo', 'fit_dixon_coles', 'snapshot_autofill'];
    const runsArr = Array.isArray(runs) ? runs : [];

    let html = '';
    allJobs.forEach(job => {
      const label = JOB_LABELS[job] || job;
      const run = runsArr.find(r => r.job_name === job);
      const st = run ? run.status : '—';
      const stCls = st === 'ok' ? 'adm-badge-win' : st === 'failed' ? 'adm-badge-loss' : st === 'running' ? 'adm-badge-signal' : 'adm-badge-pending';
      const stLabel = st === 'ok' ? 'OK' : st === 'failed' ? 'Ошибка' : st === 'running' ? 'В работе' : st === '—' ? '—' : esc(st);
      const time = run ? timeAgo(run.finished_at || run.started_at) : '—';
      const dur = run && run.duration_seconds != null ? (run.duration_seconds < 1 ? '<1с' : Math.round(run.duration_seconds) + 'с') : '';
      html += '<div class="adm-job-row">' +
        '<span class="adm-job-name">' + esc(label) + '</span>' +
        '<span class="adm-badge ' + stCls + ' adm-job-status">' + stLabel + '</span>' +
        '<span class="adm-job-time">' + esc(time) + (dur ? ' (' + dur + ')' : '') + '</span>' +
        '<button class="adm-btn-sm adm-btn-secondary" data-action="run-job" data-job="' + esc(job) + '">Запустить</button>' +
        '</div>';
    });

    // Running jobs from status
    if (status && status.jobs) {
      const running = Object.entries(status.jobs);
      if (running.length) {
        html = '<div style="margin-bottom:12px;padding:8px 12px;background:rgba(56,189,248,0.1);border-radius:var(--radius-sm);font-size:var(--font-size-sm);color:var(--accent-secondary)">Выполняются: ' + running.map(([k]) => esc(JOB_LABELS[k] || k)).join(', ') + '</div>' + html;
      }
    }

    cont.innerHTML = html;
  }

  function renderModel(data) {
    const cont = el('sys-model');
    if (!data || typeof data !== 'object') { cont.textContent = 'Нет данных'; return; }
    let html = '<div style="display:grid;gap:12px">';

    // Config
    if (data.config) {
      html += '<div class="adm-kpi-grid">';
      html += kpiMini('Сезон', data.config.season || '—');
      html += kpiMini('Prob Source', esc(data.config.prob_source || '—'));
      html += kpiMini('Лиг', data.config.league_ids ? data.config.league_ids.length : '—');
      html += '</div>';
    }

    // ELO
    if (data.elo) {
      const e = data.elo;
      html += '<div class="adm-kpi-grid">';
      html += kpiMini('ELO команд', e.teams_with_elo || '—');
      html += kpiMini('Обработано', e.processed_total || '—');
      html += kpiMini('Не обработ.', e.unprocessed_total || 0, e.unprocessed_total > 0 ? -1 : 0);
      html += kpiMini('ELO обновл.', timeAgo(e.last_processed_at));
      html += '</div>';
      if (e.rebuild_needed) {
        html += '<div class="adm-alert adm-alert-danger" style="margin-bottom:12px">Требуется пересчёт ELO!</div>';
      }
    }

    // API Football
    if (data.api_football) {
      const af = data.api_football;
      const used = af.today ? af.today.requests : 0;
      const limit = af.daily_limit || 75000;
      const pct = Math.round((used / limit) * 100);
      html += '<div class="adm-kpi-grid">';
      html += kpiMini('API сегодня', used.toLocaleString() + ' / ' + limit.toLocaleString());
      html += kpiMini('Ошибок', af.today ? af.today.errors : 0, af.today && af.today.errors > 0 ? -1 : 0);
      html += kpiMini('За 24ч', af.last_24h ? af.last_24h.requests.toLocaleString() : '—');
      html += kpiMini('Квота %', pct + '%', pct > 80 ? -1 : 0);
      html += '</div>';
      if (af.blocked) {
        html += '<div class="adm-alert adm-alert-danger" style="margin-bottom:12px">API заблокирован: ' + esc(af.blocked_reason || 'неизвестно') + '</div>';
      }
    }

    // Leagues
    if (data.leagues && Array.isArray(data.leagues) && data.leagues.length) {
      html += '<details><summary style="cursor:pointer;font-weight:600;margin:8px 0">Лиги и параметры (' + data.leagues.length + ')</summary>';
      html += '<table class="adm-table" style="margin-top:8px"><thead><tr><th>Лига</th><th>Сезон</th><th>Матчей</th><th>DC rho</th><th>Calib alpha</th><th>Avg Goals</th></tr></thead><tbody>';
      data.leagues.forEach(l => {
        html += '<tr><td>' + esc(l.league_name || 'ID ' + l.league_id) + '</td><td>' + (l.season || '—') + '</td><td>' + (l.finished_total || '—') + '</td><td>' + (l.dc_rho != null ? l.dc_rho.toFixed(4) : '—') + '</td><td>' + (l.calib_alpha != null ? l.calib_alpha.toFixed(3) : '—') + '</td><td>' + (l.avg_goals != null ? l.avg_goals.toFixed(2) : '—') + '</td></tr>';
      });
      html += '</tbody></table></details>';
    }

    html += '</div>';
    if (data.generated_at) {
      html += '<div style="font-size:var(--font-size-xs);color:var(--text-muted);margin-top:8px">Обновлено: ' + formatDate(data.generated_at) + '</div>';
    }
    cont.innerHTML = html;
  }

  /* ========== AUDIT LOG ========== */
  function renderAuditLog(runs) {
    const cont = el('sys-audit');
    if (!cont) return;
    const arr = Array.isArray(runs) ? runs : [];
    // Show manual triggers and errors prominently
    const manualRuns = arr.filter(r => r.triggered_by && r.triggered_by.startsWith('manual'));
    const failedRuns = arr.filter(r => r.status === 'failed');
    const auditItems = [];

    manualRuns.forEach(r => {
      const actor = r.triggered_by ? r.triggered_by.replace('manual:', '') : 'unknown';
      const meta = r.meta || {};
      auditItems.push({
        ts: r.started_at,
        type: 'manual',
        icon: '&#x1F464;',
        text: (JOB_LABELS[r.job_name] || r.job_name) + ' — запущен вручную',
        detail: 'Оператор: ' + actor + (meta.client_ip ? ' (' + meta.client_ip + ')' : ''),
        status: r.status,
      });
    });

    failedRuns.forEach(r => {
      if (manualRuns.includes(r)) return; // already shown
      auditItems.push({
        ts: r.started_at,
        type: 'error',
        icon: '&#x26A0;',
        text: (JOB_LABELS[r.job_name] || r.job_name) + ' — ошибка',
        detail: r.error ? String(r.error).substring(0, 300) : 'Неизвестная ошибка',
        status: r.status,
      });
    });

    // Sort by time descending
    auditItems.sort((a, b) => new Date(b.ts || 0) - new Date(a.ts || 0));

    if (!auditItems.length) {
      cont.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Нет событий аудита. Ручные запуски и ошибки появятся здесь</div>';
      return;
    }

    let html = '<div class="adm-audit-list">';
    auditItems.slice(0, 20).forEach(item => {
      const stCls = item.status === 'ok' ? 'adm-badge-win' : item.status === 'failed' ? 'adm-badge-loss' : 'adm-badge-pending';
      const stLabel = item.status === 'ok' ? 'OK' : item.status === 'failed' ? 'Ошибка' : esc(item.status || '—');
      html += '<div class="adm-audit-item adm-audit-' + esc(item.type) + '">' +
        '<span class="adm-audit-icon">' + item.icon + '</span>' +
        '<div class="adm-audit-content">' +
        '<div class="adm-audit-text">' + esc(item.text) + ' <span class="adm-badge ' + stCls + '" style="font-size:10px">' + stLabel + '</span></div>' +
        '<div class="adm-audit-detail">' + esc(item.detail) + '</div>' +
        '<div class="adm-audit-time">' + formatDate(item.ts) + '</div>' +
        '</div></div>';
    });
    html += '</div>';
    cont.innerHTML = html;
  }

  /* ========== DB BROWSER ========== */
  async function browseDb() {
    const table = el('db-table').value;
    const cont = el('sys-db');
    cont.innerHTML = '<div class="adm-loading">Загрузка...</div>';
    try {
      const res = await api('/db/browse', { params: { table, limit: 30 } });
      const data = res.data;
      if (!data || !data.rows || !data.rows.length) {
        cont.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Таблица пуста. Загрузите данные через «Синхронизация»</div>';
        return;
      }
      const cols = Object.keys(data.rows[0]);
      let html = `<table class="adm-table"><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>`;
      data.rows.forEach(row => {
        html += '<tr>' + cols.map(c => {
          let val = row[c];
          if (val === null) return '<td style="color:var(--text-muted)">null</td>';
          if (typeof val === 'object') val = JSON.stringify(val);
          const str = String(val);
          return `<td title="${esc(str)}">${esc(str.length > 50 ? str.substring(0, 50) + '...' : str)}</td>`;
        }).join('') + '</tr>';
      });
      html += '</tbody></table>';
      cont.innerHTML = html;
    } catch (e) {
      cont.innerHTML = `<div class="adm-alert adm-alert-danger">${esc(e.message)}</div>`;
    }
  }

  /* ========== FIXTURE MODAL ========== */
  async function openFixtureModal(fixtureId) {
    const modal = el('fixture-modal');
    const body = el('modal-body');
    modal.classList.remove('hidden');
    body.innerHTML = '<div class="adm-loading">Загрузка...</div>';

    try {
      const res = await api(`/fixtures/${fixtureId}/details`);
      const d = res.data;
      let html = '';

      html += `<div class="adm-modal-row"><span class="adm-modal-label">Fixture ID</span><span>${esc(String(fixtureId))} <button class="adm-btn-text adm-copy-btn" data-copy="${esc(String(fixtureId))}" title="Копировать" style="font-size:14px;margin-left:6px;cursor:pointer">&#x1F4CB;</button></span></div>`;

      if (d.fixture) {
        const f = d.fixture;
        html += `<h3 style="margin-bottom:16px">${esc(f.home || '')} — ${esc(f.away || '')}</h3>`;
        html += `<div class="adm-modal-row"><span class="adm-modal-label">Лига</span><span>${esc(f.league || '')}</span></div>`;
        html += `<div class="adm-modal-row"><span class="adm-modal-label">Время</span><span>${formatDate(f.kickoff)}</span></div>`;
        html += `<div class="adm-modal-row"><span class="adm-modal-label">Статус</span><span>${esc(f.status || '')}</span></div>`;
        if (f.home_goals != null) {
          html += `<div class="adm-modal-row"><span class="adm-modal-label">Счёт</span><span>${f.home_goals} — ${f.away_goals}</span></div>`;
        }
      }

      if (d.prediction_1x2) {
        const p = d.prediction_1x2;
        html += `<h4 style="margin:20px 0 10px">Прогноз 1X2</h4>`;
        html += `<div class="adm-modal-row"><span class="adm-modal-label">Выбор</span><span class="adm-badge adm-badge-pick">${esc(PICK_LABELS[p.pick] || p.pick || p.selection || '')}</span></div>`;
        if (p.odd) html += `<div class="adm-modal-row"><span class="adm-modal-label">Коэффициент</span><span>${Number(p.odd).toFixed(2)}</span></div>`;
        if (p.confidence != null) html += `<div class="adm-modal-row"><span class="adm-modal-label">Уверенность</span><span>${(p.confidence * 100).toFixed(1)}%</span></div>`;
        if (p.ev != null) html += `<div class="adm-modal-row"><span class="adm-modal-label">EV</span><span>${(p.ev * 100).toFixed(1)}%</span></div>`;
        if (p.status) html += `<div class="adm-modal-row"><span class="adm-modal-label">Статус</span><span>${statusBadge(p.status)}</span></div>`;
        if (p.signal_score != null) html += `<div class="adm-modal-row"><span class="adm-modal-label">Сигнал</span><span>${(p.signal_score * 100).toFixed(0)}%</span></div>`;
      }

      if (d.odds) {
        html += `<h4 style="margin:20px 0 10px">Коэффициенты</h4>`;
        html += `<div class="adm-modal-row"><span class="adm-modal-label">П1 / Н / П2</span><span>${[d.odds.home_win, d.odds.draw, d.odds.away_win].map(v => v ? Number(v).toFixed(2) : '—').join(' / ')}</span></div>`;
        if (d.odds.over_2_5) html += `<div class="adm-modal-row"><span class="adm-modal-label">Б/М 2.5</span><span>${Number(d.odds.over_2_5).toFixed(2)} / ${d.odds.under_2_5 ? Number(d.odds.under_2_5).toFixed(2) : '—'}</span></div>`;
      }

      if (d.match_indices) {
        html += `<details style="margin-top:16px"><summary style="cursor:pointer;font-weight:600">Индексы матча</summary>`;
        html += '<div style="margin-top:8px">';
        Object.entries(d.match_indices).forEach(([k, v]) => {
          if (v != null && k !== 'updated_at') {
            html += `<div class="adm-modal-row"><span class="adm-modal-label">${esc(k)}</span><span>${typeof v === 'number' ? v.toFixed(3) : esc(String(v))}</span></div>`;
          }
        });
        html += '</div></details>';
      }

      if (d.decisions) {
        html += `<details style="margin-top:12px"><summary style="cursor:pointer;font-weight:600">Решения модели</summary>`;
        html += `<pre style="white-space:pre-wrap;font-size:var(--font-size-xs);color:var(--text-secondary);margin-top:8px;max-height:300px;overflow:auto">${esc(JSON.stringify(d.decisions, null, 2))}</pre></details>`;
      }

      body.innerHTML = html;
      el('modal-title').textContent = d.fixture ? `${d.fixture.home || ''} — ${d.fixture.away || ''}` : 'Детали матча';
    } catch (e) {
      body.innerHTML = `<div class="adm-alert adm-alert-danger">${esc(e.message)}</div>`;
    }
  }

  function closeModal() {
    el('fixture-modal').classList.add('hidden');
    el('modal-body').innerHTML = '';
  }

  /* ========== RUN JOB ========== */
  const DANGEROUS_JOBS = ['full', 'rebuild_elo', 'maintenance'];

  function runJob(jobName) {
    const label = JOB_LABELS[jobName] || jobName;
    if (DANGEROUS_JOBS.includes(jobName)) {
      showConfirm('Запуск задания', 'Вы уверены, что хотите запустить «' + label + '»? Это может занять несколько минут.', () => _doRunJob(jobName));
    } else {
      _doRunJob(jobName);
    }
  }

  async function _doRunJob(jobName) {
    const label = JOB_LABELS[jobName] || jobName;
    try {
      notify('Запуск: ' + label + '...', 'info');
      await api('/run-now', { method: 'POST', params: { job: jobName } });
      notify(label + ' запущен', 'success');
      // Invalidate related caches
      cacheInvalidate('system');
      cacheInvalidate('operations');
      if (['build_predictions', 'full'].includes(jobName)) cacheInvalidate('publishing');
      if (['quality_report', 'evaluate_results', 'full'].includes(jobName)) cacheInvalidate('quality');
    } catch (e) {
      notify('Ошибка запуска: ' + e.message, 'error');
    }
  }

  /* ========== PUBLISH ========== */
  async function previewPublish(fixtureId) {
    try {
      const [previewRes, histRes] = await Promise.all([
        api('/publish/post_preview', { params: { fixture_id: fixtureId } }).catch(() => api('/publish/preview', { params: { fixture_id: fixtureId } })),
        api('/publish/history', { params: { fixture_id: fixtureId } }).catch(() => ({ data: [] })),
      ]);
      const modal = el('fixture-modal');
      const body = el('modal-body');
      modal.classList.remove('hidden');
      el('modal-title').textContent = 'Превью публикации #' + fixtureId;

      let html = '<div style="background:var(--surface-2);padding:16px;border-radius:var(--radius-sm);font-size:var(--font-size-sm);margin-bottom:16px">';
      if (previewRes.data && previewRes.data.html) {
        html += previewRes.data.html;
      } else if (previewRes.data && previewRes.data.text) {
        html += '<pre style="white-space:pre-wrap">' + esc(previewRes.data.text) + '</pre>';
      } else {
        const pre = document.createElement('pre');
        pre.textContent = JSON.stringify(previewRes.data, null, 2);
        html += '<pre style="white-space:pre-wrap">' + esc(pre.textContent) + '</pre>';
      }
      html += '</div>';

      // Publication history for this fixture
      const hist = Array.isArray(histRes.data) ? histRes.data : [];
      if (hist.length) {
        html += '<h4 style="margin:12px 0 8px">История публикаций</h4>';
        html += '<table class="adm-table"><thead><tr><th>Дата</th><th>Рынок</th><th>Статус</th><th>Канал</th></tr></thead><tbody>';
        hist.forEach(h => {
          const stCls = h.status === 'ok' ? 'adm-badge-win' : h.status === 'skipped' ? 'adm-badge-pending' : h.status === 'dry_run' ? 'adm-badge-signal' : 'adm-badge-loss';
          const stLabel = h.status === 'ok' ? 'OK' : h.status === 'skipped' ? 'Пропущено' : h.status === 'dry_run' ? 'Dry Run' : esc(h.status || '—');
          html += '<tr><td>' + formatDate(h.published_at || h.created_at) + '</td><td>' + esc(h.market || '—') + '</td><td><span class="adm-badge ' + stCls + '">' + stLabel + '</span></td><td>' + (h.channel_id || '—') + '</td></tr>';
        });
        html += '</tbody></table>';
      }

      // Also update the history panel in main view
      renderPublishHistoryPanel(hist, fixtureId);

      body.innerHTML = html;
    } catch (e) {
      notify('Ошибка превью: ' + e.message, 'error');
    }
  }

  function renderPublishHistoryPanel(hist, fixtureId) {
    const cont = el('pub-history-list');
    if (!hist || !hist.length) {
      cont.textContent = 'Нет публикаций для матча #' + fixtureId;
      return;
    }
    let html = '<div style="font-size:var(--font-size-xs);color:var(--text-muted);margin-bottom:8px">Матч #' + fixtureId + '</div>';
    html += '<table class="adm-table"><thead><tr><th>Дата</th><th>Рынок</th><th>Статус</th><th>Язык</th></tr></thead><tbody>';
    hist.forEach(h => {
      const stCls = h.status === 'ok' ? 'adm-badge-win' : h.status === 'skipped' ? 'adm-badge-pending' : h.status === 'dry_run' ? 'adm-badge-signal' : 'adm-badge-loss';
      const stLabel = h.status === 'ok' ? 'OK' : h.status === 'skipped' ? 'Пропущено' : h.status === 'dry_run' ? 'Dry Run' : esc(h.status || '—');
      html += '<tr><td>' + formatDate(h.published_at || h.created_at) + '</td><td>' + esc(h.market || '—') + '</td><td><span class="adm-badge ' + stCls + '">' + stLabel + '</span></td><td>' + esc(h.language || '—') + '</td></tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function renderGlobalPublishHistory(data) {
    const cont = el('pub-history-list');
    if (!data || !data.length) {
      cont.innerHTML = '<div class="adm-loading" style="color:var(--text-muted)">Нет публикаций за выбранный период. Попробуйте увеличить диапазон</div>';
      return;
    }
    // All data comes from authenticated admin API (trusted source)
    let html = '<table class="adm-table"><thead><tr><th>Дата</th><th>Матч</th><th>Рынок</th><th>Язык</th><th>Статус</th><th>Ошибка</th></tr></thead><tbody>';
    data.forEach(h => {
      const stCls = h.status === 'ok' ? 'adm-badge-win' : h.status === 'skipped' ? 'adm-badge-pending' : h.status === 'dry_run' ? 'adm-badge-signal' : 'adm-badge-loss';
      const stLabel = h.status === 'ok' ? 'OK' : h.status === 'skipped' ? 'Пропущено' : h.status === 'dry_run' ? 'Dry Run' : h.status === 'failed' ? 'Ошибка' : esc(h.status || '—');
      const match = (h.home && h.away) ? esc(h.home) + ' — ' + esc(h.away) : '#' + h.fixture_id;
      const errText = h.error ? esc(String(h.error).substring(0, 100)) : '';
      html += '<tr><td>' + formatDate(h.created_at) + '</td><td>' + match + '</td><td>' + esc(h.market || '—') + '</td><td>' + esc(h.language || '—') + '</td><td><span class="adm-badge ' + stCls + '">' + stLabel + '</span></td><td style="font-size:var(--font-size-xs);color:var(--text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(h.error || '') + '">' + errText + '</td></tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
  }

  function doPublish(fixtureId, dryRun) {
    const isDry = dryRun !== false;
    if (!isDry) {
      showConfirm('Публикация в Telegram', 'Опубликовать матч #' + fixtureId + ' в Telegram? Это действие необратимо.', () => _doPublish(fixtureId, false));
    } else {
      _doPublish(fixtureId, true);
    }
  }

  async function _doPublish(fixtureId, isDry) {
    const label = isDry ? 'Dry Run' : 'Публикация';
    try {
      notify(label + ' #' + fixtureId + '...', 'info');
      await api('/publish', { method: 'POST', body: { fixture_id: fixtureId, dry_run: isDry } });
      notify(label + ' #' + fixtureId + ' — готово!', 'success');
      cacheInvalidate('publishing');
      loadPublishing(true);
    } catch (e) {
      notify('Ошибка: ' + e.message, 'error');
    }
  }

  /* ========== CSV Export ========== */
  function exportCsv(rows, filename) {
    const BOM = '\ufeff';
    const csv = rows.map(r => r.map(c => '"' + String(c == null ? '' : c).replace(/"/g, '""') + '"').join(',')).join('\n');
    const blob = new Blob([BOM + csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function exportMatchesCsv() {
    if (!_lastMatchesData || !_lastMatchesData.length) {
      notify('Нет данных для экспорта', 'error');
      return;
    }
    const header = ['Дата', 'Дома', 'Гости', 'Лига', 'Рынок', 'Прогноз', 'Кф.', 'EV', 'Сигнал', 'Статус', 'Профит'];
    const csvRows = [header];
    _lastMatchesData.forEach(r => {
      csvRows.push([
        r.kickoff || '', r.home || '', r.away || '', r.league || '',
        MARKET_LABELS[r.market] || r.market || '',
        PICK_LABELS[r.pick] || r.pick || '',
        r.odd != null ? Number(r.odd).toFixed(2) : '',
        r.ev != null ? (r.ev * 100).toFixed(1) + '%' : '',
        r.signal_score != null ? (r.signal_score * 100).toFixed(0) + '%' : '',
        r.status || '',
        r.profit != null ? r.profit.toFixed(2) : '',
      ]);
    });
    exportCsv(csvRows, 'matches_export_' + new Date().toISOString().slice(0, 10) + '.csv');
    notify('CSV экспортирован', 'success');
  }

  function exportQualityLeaguesCsv() {
    if (!_lastQualityLeagues || !_lastQualityLeagues.length) {
      notify('Нет данных для экспорта', 'error');
      return;
    }
    const header = ['Лига', 'Период', 'Ставок', 'Побед', 'Поражений', 'Win Rate', 'ROI', 'Профит'];
    const csvRows = [header];
    _lastQualityLeagues.forEach(r => {
      const profit = r.total_profit != null ? r.total_profit : r.profit;
      csvRows.push([
        r.league || r.league_name || '',
        (r.days || 90) + 'д',
        r.total_bets || r.bets || 0,
        r.wins || 0,
        r.losses || 0,
        r.win_rate != null ? r.win_rate.toFixed(1) + '%' : '',
        r.roi != null ? r.roi.toFixed(1) + '%' : '',
        profit != null ? profit.toFixed(2) : '',
      ]);
    });
    exportCsv(csvRows, 'quality_leagues_' + new Date().toISOString().slice(0, 10) + '.csv');
    notify('CSV экспортирован', 'success');
  }

  /* ========== Global Events ========== */
  document.addEventListener('click', (e) => {
    const target = e.target;

    // Auth submit
    if (target.id === 'auth-submit' || target.closest('#auth-submit')) {
      const input = el('admin-token');
      if (input.value.trim()) tryAuth(input.value.trim());
      return;
    }

    // Sidebar toggle
    if (target.closest('#sidebar-toggle')) {
      const sb = el('sidebar');
      sb.classList.toggle('open');
      const btn = el('sidebar-toggle');
      if (btn) btn.setAttribute('aria-label', sb.classList.contains('open') ? 'Закрыть меню' : 'Меню');
      return;
    }

    // Copy button
    const copyBtn = target.closest('[data-copy]');
    if (copyBtn) {
      const text = copyBtn.dataset.copy;
      navigator.clipboard.writeText(text).then(() => notify('Скопировано: ' + text, 'success')).catch(() => notify('Не удалось скопировать', 'error'));
      return;
    }

    // Shortcuts help
    if (target.id === 'btn-shortcuts' || target.closest('#btn-shortcuts')) {
      showShortcutsHelp();
      return;
    }

    // Logout
    if (target.id === 'btn-logout' || target.closest('#btn-logout')) {
      localStorage.removeItem(STORAGE_KEY);
      showAuth();
      return;
    }

    // Close modal
    if (target.dataset.action === 'close-modal' || target.closest('[data-action="close-modal"]')) {
      closeModal();
      return;
    }
    if (target.classList.contains('adm-modal-overlay')) {
      closeModal();
      return;
    }

    // Run job
    const jobBtn = target.closest('[data-action="run-job"]');
    if (jobBtn) {
      if (!_guardBtn(jobBtn, 3000)) return;
      runJob(jobBtn.dataset.job);
      return;
    }

    // Refresh operations (force bypass cache)
    if (target.closest('[data-action="refresh-ops"]')) {
      cacheInvalidate('operations');
      loadOperations(true);
      return;
    }

    // Chart PNG download
    const dlBtn = target.closest('[data-download-chart]');
    if (dlBtn) {
      _downloadChart(dlBtn.dataset.downloadChart);
      return;
    }

    // Toggle all quality details
    if (target.closest('[data-action="toggle-quality-details"]')) {
      const cont = el('quality-report');
      if (cont) {
        const details = cont.querySelectorAll('details');
        const allOpen = Array.from(details).every(d => d.open);
        details.forEach(d => { d.open = !allOpen; });
        const btn = target.closest('[data-action="toggle-quality-details"]');
        if (btn) btn.innerHTML = allOpen ? '&#x25BC; Все' : '&#x25B2; Все';
      }
      return;
    }

    // Refresh quality (force bypass cache)
    if (target.closest('[data-action="refresh-quality"]')) {
      cacheInvalidate('quality');
      loadQuality(true);
      return;
    }

    // Refresh system (force bypass cache)
    if (target.closest('[data-action="refresh-system"]')) {
      cacheInvalidate('system');
      loadSystem(true);
      return;
    }

    // Period selector (operations KPI)
    const periodBtn = target.closest('[data-ops-days]');
    if (periodBtn) {
      opsDays = parseInt(periodBtn.dataset.opsDays, 10) || 30;
      document.querySelectorAll('.adm-period-btn[data-ops-days]').forEach(b => b.classList.toggle('active', b === periodBtn));
      cacheInvalidate('operations');
      loadOperations(true);
      return;
    }

    // Apply match filters
    if (target.closest('[data-action="apply-match-filters"]')) {
      matchState.league = el('am-filter-league').value;
      matchState.market = el('am-filter-market').value;
      matchState.status = el('am-filter-status').value;
      matchState.team = (el('am-filter-team').value || '').trim();
      matchState.offset = 0;
      loadMatches();
      return;
    }

    // CSV export (matches)
    if (target.closest('[data-action="export-matches-csv"]')) {
      exportMatchesCsv();
      return;
    }

    // CSV export (quality leagues)
    if (target.closest('[data-action="export-quality-csv"]')) {
      exportQualityLeaguesCsv();
      return;
    }

    // Browse DB
    if (target.closest('[data-action="browse-db"]')) {
      browseDb();
      return;
    }

    // Refresh global publish history
    if (target.closest('[data-action="refresh-pub-history"]')) {
      cacheInvalidate('publishing');
      loadPublishing(true);
      return;
    }

    // Publish actions
    const previewBtn = target.closest('[data-action="preview-publish"]');
    if (previewBtn) {
      previewPublish(previewBtn.dataset.fixtureId);
      return;
    }
    const dryBtn = target.closest('[data-action="do-publish-dry"]');
    if (dryBtn) {
      doPublish(dryBtn.dataset.fixtureId, true);
      return;
    }
    const pubBtn = target.closest('[data-action="do-publish"]');
    if (pubBtn) {
      doPublish(pubBtn.dataset.fixtureId, false);
      return;
    }

    // Sort matches table
    const sortMatchTh = target.closest('[data-sort-match]');
    if (sortMatchTh && _lastMatchesData.length) {
      const key = sortMatchTh.dataset.sortMatch;
      const getters = {
        kickoff: r => r.kickoff ? new Date(r.kickoff).getTime() : null,
        market: r => r.market || '',
        odd: r => r.odd != null ? Number(r.odd) : null,
        ev: r => r.ev != null ? Number(r.ev) : null,
        status: r => r.status || '',
        profit: r => r.profit != null ? Number(r.profit) : null,
      };
      if (getters[key]) {
        _sortRows(_lastMatchesData, _matchSort, key, getters[key]);
        renderMatchesTable(_lastMatchesData, _lastMatchesData.length);
      }
      return;
    }

    // Sort quality leagues table
    const sortLeagueTh = target.closest('[data-sort-league]');
    if (sortLeagueTh && _lastQualityLeagues.length) {
      const key = sortLeagueTh.dataset.sortLeague;
      const getters = {
        league: r => (r.league || r.league_name || '').toLowerCase(),
        bets: r => r.total_bets || r.bets || 0,
        win_rate: r => r.win_rate != null ? Number(r.win_rate) : null,
        roi: r => r.roi != null ? Number(r.roi) : null,
        profit: r => { const p = r.total_profit != null ? r.total_profit : r.profit; return p != null ? Number(p) : null; },
      };
      if (getters[key]) {
        _sortRows(_lastQualityLeagues, _leagueSort, key, getters[key]);
        renderQualityLeagues(_lastQualityLeagues);
      }
      return;
    }

    // Pagination (matches)
    const pageBtn = target.closest('.adm-pagination button');
    if (pageBtn && !pageBtn.disabled) {
      matchState.offset = parseInt(pageBtn.dataset.page, 10) * matchState.limit;
      loadMatches();
      return;
    }

    // Fixture card click → modal + active row
    const pickCard = target.closest('[data-fixture-id]');
    if (pickCard && !target.closest('button')) {
      const fid = pickCard.dataset.fixtureId;
      // Highlight active row in table
      if (pickCard.tagName === 'TR') {
        pickCard.closest('tbody').querySelectorAll('tr.adm-row-active').forEach(r => r.classList.remove('adm-row-active'));
        pickCard.classList.add('adm-row-active');
      }
      if (fid) openFixtureModal(fid);
      return;
    }
  });

  // Enter on auth input
  el('admin-token').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const val = el('admin-token').value.trim();
      if (val) tryAuth(val);
    }
  });

  // Escape closes modal
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el('fixture-modal').classList.contains('hidden')) {
      closeModal();
    }
  });

  /* ========== Chart Market Filter ========== */
  const _chartMarketSel = el('adm-chart-market-filter');
  if (_chartMarketSel) {
    _chartMarketSel.addEventListener('change', () => {
      _qualityChartMarket = _chartMarketSel.value;
      const cached = cacheGet('quality');
      if (cached) {
        const filtered = _filterChartRows(cached.chartRows);
        drawAdmRoiChart(el('adm-roi-chart'), filtered);
        drawAdmProfitChart(el('adm-profit-chart'), filtered);
      }
    });
  }

  /* ========== Keyboard Shortcuts ========== */
  document.addEventListener('keydown', (e) => {
    // Skip when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (el('fixture-modal') && !el('fixture-modal').classList.contains('hidden')) return;
    if (document.querySelector('.adm-confirm-overlay')) return;

    const key = e.key;
    const sectionMap = { '1': 'operations', '2': 'admin-matches', '3': 'publishing', '4': 'quality', '5': 'system' };
    if (sectionMap[key]) { e.preventDefault(); location.hash = '#' + sectionMap[key]; return; }
    // 'r' to refresh current section
    if (key === '?' || (key === '/' && e.shiftKey)) { e.preventDefault(); showShortcutsHelp(); return; }
    if (key === 'r' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const active = SECTIONS.find(s => el(s) && el(s).classList.contains('active'));
      if (active && LOADERS[active]) { cacheInvalidate(active); LOADERS[active](true); }
    }
  });

  /* ========== Shortcuts Help ========== */
  function showShortcutsHelp() {
    const overlay = document.createElement('div');
    overlay.className = 'adm-modal-overlay';
    overlay.setAttribute('role', 'dialog');
    const dlg = document.createElement('div');
    dlg.className = 'adm-modal';
    dlg.style.maxWidth = '400px';
    dlg.innerHTML = '<div class="adm-modal-header"><h2>Горячие клавиши</h2>' +
      '<button class="adm-modal-close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="adm-modal-body">' +
      '<table class="adm-table" style="font-size:var(--font-size-sm)">' +
      '<tbody>' +
      '<tr><td><kbd>1</kbd></td><td>Операции</td></tr>' +
      '<tr><td><kbd>2</kbd></td><td>Матчи</td></tr>' +
      '<tr><td><kbd>3</kbd></td><td>Публикации</td></tr>' +
      '<tr><td><kbd>4</kbd></td><td>Качество</td></tr>' +
      '<tr><td><kbd>5</kbd></td><td>Система</td></tr>' +
      '<tr><td><kbd>R</kbd></td><td>Обновить текущую секцию</td></tr>' +
      '<tr><td><kbd>Esc</kbd></td><td>Закрыть модальное окно</td></tr>' +
      '<tr><td><kbd>?</kbd></td><td>Показать эту справку</td></tr>' +
      '</tbody></table></div>';
    overlay.appendChild(dlg);
    document.body.appendChild(overlay);
    const cleanup = () => overlay.remove();
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay || e.target.closest('.adm-modal-close')) cleanup();
    });
    const onEsc = (e) => { if (e.key === 'Escape') { cleanup(); document.removeEventListener('keydown', onEsc); } };
    document.addEventListener('keydown', onEsc);
  }

  /* ========== Connection Status ========== */
  let _lastPingOk = true;
  async function checkConnection() {
    try {
      const resp = await fetch('/health', { signal: AbortSignal.timeout(5000) });
      _lastPingOk = resp.ok;
    } catch (_) {
      _lastPingOk = false;
    }
    updateConnectionIndicator();
  }

  function updateConnectionIndicator() {
    let dot = el('conn-status');
    if (!dot) {
      dot = document.createElement('span');
      dot.id = 'conn-status';
      dot.className = 'adm-conn-dot';
      const header = document.querySelector('.adm-header-titles');
      if (header) header.appendChild(dot);
    }
    dot.className = 'adm-conn-dot ' + (_lastPingOk ? 'ok' : 'offline');
    dot.title = _lastPingOk ? 'Подключено' : 'Нет связи с сервером';
  }

  let _connInterval = setInterval(checkConnection, 30000);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearInterval(_connInterval);
      stopAutoRefresh();
    } else {
      checkConnection();
      _connInterval = setInterval(checkConnection, 30000);
      startAutoRefresh();
    }
  });

  /* ========== Instant team search ========== */
  (function() {
    var timer = null;
    var inp = el('am-filter-team');
    if (!inp) return;
    inp.addEventListener('input', function() {
      clearTimeout(timer);
      timer = setTimeout(function() {
        matchState.team = (inp.value || '').trim();
        matchState.offset = 0;
        loadMatches();
      }, 400);
    });
  })();

  /* ========== Init ========== */
  token = localStorage.getItem(STORAGE_KEY) || '';
  if (token) {
    fetch('/api/v1/meta', { headers: { 'X-Admin-Token': token } })
      .then(resp => {
        if (resp.ok) showApp();
        else showAuth();
      })
      .catch(() => showAuth());
  } else {
    showAuth();
  }
})();
