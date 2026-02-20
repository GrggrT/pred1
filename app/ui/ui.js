      (() => {
        'use strict';

        const STORAGE_KEY = 'pred1_admin_token';
        const UI_STATE_KEY = 'pred1_ui_state_v1';
        const JOB_NAMES = [
          'sync_data',
          'compute_indices',
          'build_predictions',
          'evaluate_results',
          'quality_report',
          'maintenance',
          'rebuild_elo',
          'snapshot_autofill',
        ];

        let tokenState = '';
        let dashboardRefreshTimer = null;
        let uiStateSaveTimer = null;
        const dbBrowseState = {
          table: 'fixtures',
          limit: 20,
          offset: 0,
          fixtureId: '',
          leagueId: '',
          status: '',
          tableSearch: '',
        };
        let dbLastResult = null;
        const liveState = {
          market: 'all',
          league: '',
        };
        const infoState = {
          tab: 'picks',
          dateFrom: '',
          dateTo: '',
          search: '',
          onlyUpcoming: false,
          limit: 80,
        };
        const metaState = {
          loaded: false,
          appStartedAt: null,
          uiSha256: null,
          uiMtime: null,
          pythonVersion: null,
          pid: null,
        };
        let lastRefreshAt = null;
        const betsHistoryState = {
          expanded: false,
          market: 'all',
          status: '',
          settledOnly: true,
          team: '',
          sort: 'kickoff_desc',
          limit: 50,
          offset: 0,
          total: null,
          lastRecentTotal: null,
          lastRecentShown: 0,
          allTime: false,
          viewMode: 'page',
          cacheKey: null,
          cacheRows: null,
          cacheTotal: null,
          cacheTruncated: false,
        };
        const fixtureModalState = {
          fixtureId: null,
          cache: new Map(),
        };

        const ESCAPE_TABLE = {
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        };

        function escapeHtml(value) {
          return String(value ?? '').replace(/[&<>"']/g, (ch) => ESCAPE_TABLE[ch]);
        }

        function initialsFor(name) {
          const raw = String(name ?? '').trim();
          if (!raw) return '';
          const parts = raw.split(/\s+/).filter(Boolean);
          if (!parts.length) return '';
          const first = parts[0]?.[0] || '';
          const second = parts.length > 1 ? parts[1]?.[0] || '' : parts[0]?.[1] || '';
          return `${first}${second}`.toUpperCase();
        }

        function splitTeamsText(teamsText) {
          const raw = String(teamsText ?? '').trim();
          if (!raw) return { home: '', away: '' };
          const parts = raw.split(' vs ');
          if (parts.length >= 2) {
            return { home: parts[0].trim(), away: parts.slice(1).join(' vs ').trim() };
          }
          return { home: raw, away: '' };
        }

        function teamNamesFromPick(pick) {
          const home = String(pick?.home ?? '').trim();
          const away = String(pick?.away ?? '').trim();
          if (home || away) return { home, away };
          return splitTeamsText(pick?.teams);
        }

        function logoHtml(url, name, kind = 'team', size = 'md') {
          const cleanUrl = String(url ?? '').trim();
          const label = String(name ?? '').trim() || kind;
          const initials = initialsFor(label) || '•';
          const shellClass = `logo-shell logo-${kind} logo-${size}${cleanUrl ? '' : ' is-fallback'}`;
          if (!cleanUrl) {
            return `<span class="${shellClass}" aria-label="${escapeHtml(label)}"><span class="logo-fallback">${escapeHtml(initials)}</span></span>`;
          }
          return `
            <span class="${shellClass}" aria-label="${escapeHtml(label)}">
              <img class="logo-img" src="${escapeHtml(cleanUrl)}" alt="${escapeHtml(label)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">
              <span class="logo-fallback">${escapeHtml(initials)}</span>
            </span>
          `;
        }

        function applyLogoFallbacks(scope) {
          const root = scope || document;
          root.querySelectorAll('.logo-img').forEach((img) => {
            if (img.dataset.logoBound === '1') return;
            img.dataset.logoBound = '1';
            img.addEventListener('error', () => {
              const shell = img.closest('.logo-shell');
              if (shell) shell.classList.add('is-fallback');
            }, { once: true });
          });
        }

        function compactError(raw, maxLen = 160) {
          const msg = String(raw ?? '').trim();
          if (!msg) return '';
          const lines = msg.split('\n').map((line) => line.trim()).filter(Boolean);
          const last = lines.length ? lines[lines.length - 1] : msg;
          if (last.length <= maxLen) return last;
          return `${last.slice(0, Math.max(0, maxLen - 3))}...`;
        }

        function csvEscape(value) {
          const s = String(value ?? '');
          const needsQuotes = /[",\n\r]/.test(s);
          const escaped = s.replaceAll('"', '""');
          return needsQuotes ? `"${escaped}"` : escaped;
        }

        function toCsv(rows, columns) {
          const header = columns.map(csvEscape).join(',');
          const lines = rows.map((row) => columns.map((c) => csvEscape(row?.[c] ?? '')).join(','));
          return `\ufeff${header}\n${lines.join('\n')}\n`;
        }

        function downloadTextFile(filename, text, mimeType = 'text/plain') {
          const blob = new Blob([text], { type: `${mimeType};charset=utf-8` });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        }

        async function copyToClipboard(text) {
          const payload = String(text ?? '');
          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(payload);
              return true;
            }
          } catch (e) {
            // ignore
          }

          try {
            const ta = document.createElement('textarea');
            ta.value = payload;
            ta.setAttribute('readonly', 'true');
            ta.style.position = 'fixed';
            ta.style.top = '0';
            ta.style.left = '0';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            const ok = document.execCommand('copy');
            ta.remove();
            return ok;
          } catch (e) {
            return false;
          }
        }

        function el(id) {
          return document.getElementById(id);
        }

        function setHidden(element, hidden) {
          if (!element) return;
          element.classList.toggle('is-hidden', hidden);
        }

        function showNotification(message, type = 'info') {
          const notification = document.createElement('div');
          notification.className = `notification notification-${type}`;
          notification.innerHTML = `
            <div class="notification-content">
              <span class="notification-message"></span>
              <button type="button" class="notification-close" aria-label="Close">×</button>
            </div>
          `;
          const msgEl = notification.querySelector('.notification-message');
          if (msgEl) msgEl.textContent = String(message);
          const closeEl = notification.querySelector('.notification-close');
          if (closeEl) closeEl.addEventListener('click', () => notification.remove());
          document.body.appendChild(notification);
          window.setTimeout(() => notification.remove(), 4500);
        }

        function notify(message, type = 'info') {
          try {
            showNotification(message, type);
          } catch (e) {
            console.log(message);
          }
        }

        function setAuthError(message) {
          const box = el('auth-error');
          if (!box) return;
          if (!message) {
            box.textContent = '';
            setHidden(box, true);
            return;
          }
          box.textContent = message;
          setHidden(box, false);
        }

        function formatDateTime(value) {
          if (!value) return '—';
          try {
            return new Date(value).toLocaleString('ru-RU', {
              year: 'numeric',
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            });
          } catch (e) {
            return '—';
          }
        }

        function formatDate(value) {
          if (!value) return '—';
          try {
            return new Date(value).toLocaleDateString('ru-RU', {
              year: 'numeric',
              month: '2-digit',
              day: '2-digit',
            });
          } catch (e) {
            return '—';
          }
        }

        function toInputDate(value) {
          if (!value) return '';
          const d = value instanceof Date ? value : new Date(value);
          if (!Number.isFinite(d.getTime())) return '';
          const year = d.getFullYear();
          const month = String(d.getMonth() + 1).padStart(2, '0');
          const day = String(d.getDate()).padStart(2, '0');
          return `${year}-${month}-${day}`;
        }

        function formatTime(value) {
          if (!value) return '—';
          try {
            return value.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          } catch (e) {
            return '—';
          }
        }

        function renderPageMeta() {
          const metaEl = el('page-meta');
          if (!metaEl) return;

          const parts = [];
          const titleParts = [];

          if (metaState.loaded) {
            const sha = metaState.uiSha256 ? String(metaState.uiSha256) : '';
            const shortSha = sha ? sha.slice(0, 10) : '';
            if (shortSha) parts.push(`UI ${shortSha}`);
            if (metaState.uiMtime) parts.push(`UI mtime ${formatDateTime(metaState.uiMtime)}`);
            if (metaState.appStartedAt) parts.push(`Started ${formatDateTime(metaState.appStartedAt)}`);
            if (metaState.pythonVersion) parts.push(`Py ${metaState.pythonVersion}`);
            if (metaState.pid) parts.push(`PID ${metaState.pid}`);

            if (sha) titleParts.push(`UI sha256: ${sha}`);
            if (metaState.uiMtime) titleParts.push(`UI mtime: ${metaState.uiMtime}`);
            if (metaState.appStartedAt) titleParts.push(`Started: ${metaState.appStartedAt}`);
            if (metaState.pythonVersion) titleParts.push(`Python: ${metaState.pythonVersion}`);
            if (metaState.pid) titleParts.push(`PID: ${metaState.pid}`);
          }

          if (lastRefreshAt) {
            parts.push(`Refreshed ${formatTime(lastRefreshAt)}`);
            titleParts.push(`Last refresh: ${lastRefreshAt.toISOString()}`);
          }

          metaEl.textContent = parts.join(' • ');
          metaEl.title = titleParts.join('\n');
        }

        async function loadMeta() {
          try {
            const meta = await apiFetchJson('/api/v1/meta');
            metaState.loaded = true;
            metaState.appStartedAt = meta?.app_started_at || null;
            metaState.uiSha256 = meta?.ui_index?.sha256 || null;
            metaState.uiMtime = meta?.ui_index?.mtime || null;
            metaState.pythonVersion = meta?.python_version || null;
            metaState.pid = meta?.pid || null;
            renderPageMeta();
          } catch (e) {
            // non-fatal
          }
        }

        function setConnectionStatus(text, isOk) {
          const badge = el('connection-status');
          if (!badge) return;
          badge.textContent = text;
          badge.className = isOk ? 'nav-badge' : 'nav-badge status-offline';
        }

        function loadStoredToken() {
          try {
            return (localStorage.getItem(STORAGE_KEY) || '').trim();
          } catch (e) {
            return '';
          }
        }

        function storeToken(token) {
          try {
            localStorage.setItem(STORAGE_KEY, token);
          } catch (e) {
            // ignore
          }
        }

        function clearStoredToken() {
          try {
            localStorage.removeItem(STORAGE_KEY);
          } catch (e) {
            // ignore
          }
        }

        function loadStoredUiState() {
          try {
            const raw = localStorage.getItem(UI_STATE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : null;
          } catch (e) {
            return null;
          }
        }

        function snapshotUiState() {
          const activeSection = document.querySelector('.section.active')?.id || 'dashboard';
          const statsPeriod = el('stats-period')?.value || '30';

          return {
            section: activeSection,
            statsPeriod,
            betsHistory: {
              expanded: Boolean(betsHistoryState.expanded),
              market: betsHistoryState.market,
              status: betsHistoryState.status,
              settledOnly: Boolean(betsHistoryState.settledOnly),
              team: betsHistoryState.team,
              sort: betsHistoryState.sort,
              limit: betsHistoryState.limit,
              offset: betsHistoryState.offset,
              allTime: Boolean(betsHistoryState.allTime),
            },
            live: { ...liveState },
            info: {
              tab: infoState.tab,
              dateFrom: infoState.dateFrom,
              dateTo: infoState.dateTo,
              search: infoState.search,
              onlyUpcoming: infoState.onlyUpcoming,
              limit: infoState.limit,
            },
            db: { ...dbBrowseState },
          };
        }

        function persistUiState() {
          try {
            localStorage.setItem(UI_STATE_KEY, JSON.stringify(snapshotUiState()));
          } catch (e) {
            // ignore
          }
        }

        function scheduleUiStateSave() {
          try {
            if (uiStateSaveTimer) window.clearTimeout(uiStateSaveTimer);
            uiStateSaveTimer = window.setTimeout(() => {
              uiStateSaveTimer = null;
              persistUiState();
            }, 200);
          } catch (e) {
            // ignore
          }
        }

        function clampInt(value, min, max, fallback) {
          const n = Number.parseInt(String(value), 10);
          if (!Number.isFinite(n)) return fallback;
          if (n < min) return min;
          if (n > max) return max;
          return n;
        }

        function applyUiStateFromStorage(state) {
          if (!state || typeof state !== 'object') return { initialSection: 'dashboard', openBetsHistory: false };

          const initialSection = typeof state.section === 'string' ? state.section : 'dashboard';
          const openBetsHistory = Boolean(state?.betsHistory?.expanded);

          const period = typeof state.statsPeriod === 'string' ? state.statsPeriod : null;
          const periodSelect = el('stats-period');
          if (periodSelect && period && Array.from(periodSelect.options).some((o) => o.value === period)) {
            periodSelect.value = period;
          }

          const bh = state.betsHistory || {};
          const market = typeof bh.market === 'string' ? bh.market : null;
          if (market && ['all', '1x2', 'totals'].includes(market)) betsHistoryState.market = market;
          const status = typeof bh.status === 'string' ? bh.status : null;
          if (status !== null && ['', 'WIN', 'LOSS', 'PENDING', 'VOID'].includes(status)) betsHistoryState.status = status;
          betsHistoryState.settledOnly = bh.settledOnly === undefined ? betsHistoryState.settledOnly : Boolean(bh.settledOnly);
          const team = typeof bh.team === 'string' ? bh.team : null;
          if (team !== null) betsHistoryState.team = team;
          const sort = typeof bh.sort === 'string' ? bh.sort : null;
          if (sort && ['kickoff_desc', 'ev_desc', 'profit_desc', 'signal_desc'].includes(sort)) betsHistoryState.sort = sort;
          const limit = typeof bh.limit === 'number' || typeof bh.limit === 'string' ? String(bh.limit) : null;
          if (limit && ['25', '50', '100', '250', '500'].includes(limit)) betsHistoryState.limit = Number.parseInt(limit, 10);
          betsHistoryState.offset = clampInt(bh.offset ?? betsHistoryState.offset, 0, 1_000_000, betsHistoryState.offset);
          betsHistoryState.allTime = Boolean(bh.allTime);

          const db = state.db || {};
          if (typeof db.table === 'string' && db.table.trim()) dbBrowseState.table = db.table.trim();
          dbBrowseState.limit = clampInt(db.limit ?? dbBrowseState.limit, 1, 200, dbBrowseState.limit);
          dbBrowseState.offset = clampInt(db.offset ?? dbBrowseState.offset, 0, 1_000_000, dbBrowseState.offset);
          if (typeof db.fixtureId === 'string') dbBrowseState.fixtureId = db.fixtureId;
          if (typeof db.leagueId === 'string') dbBrowseState.leagueId = db.leagueId;
          if (typeof db.status === 'string') dbBrowseState.status = db.status;
          if (typeof db.tableSearch === 'string') dbBrowseState.tableSearch = db.tableSearch;

          const live = state.live || {};
          const liveMarket = typeof live.market === 'string' ? live.market : null;
          if (liveMarket && ['all', '1x2', 'totals'].includes(liveMarket)) liveState.market = liveMarket;
          if (typeof live.league === 'string') liveState.league = live.league;

          const info = state.info || {};
          const infoTab = typeof info.tab === 'string' ? info.tab : null;
          if (infoTab && ['picks', 'stats'].includes(infoTab)) infoState.tab = infoTab;
          const infoDateFrom = typeof info.dateFrom === 'string' ? info.dateFrom.trim() : '';
          if (/^\d{4}-\d{2}-\d{2}$/.test(infoDateFrom)) infoState.dateFrom = infoDateFrom;
          const infoDateTo = typeof info.dateTo === 'string' ? info.dateTo.trim() : '';
          if (/^\d{4}-\d{2}-\d{2}$/.test(infoDateTo)) infoState.dateTo = infoDateTo;
          if (typeof info.search === 'string') infoState.search = info.search;
          if (info.onlyUpcoming !== undefined) infoState.onlyUpcoming = Boolean(info.onlyUpcoming);
          const infoLimit = clampInt(info.limit ?? infoState.limit, 1, 500, infoState.limit);
          infoState.limit = infoLimit;

          return { initialSection, openBetsHistory };
        }

        function getToken() {
          return (tokenState || loadStoredToken() || '').trim();
        }

        async function validateToken(token) {
          const res = await fetch('/health/debug', { headers: { 'X-Admin-Token': token } });
          if (res.ok) return true;
          if (res.status === 403) return false;
          throw new Error(`health/debug failed: ${res.status}`);
        }

        async function apiFetch(path, options = {}) {
          const token = getToken();
          if (!token) throw new Error('AUTH_REQUIRED');

          const headers = new Headers(options.headers || {});
          headers.set('X-Admin-Token', token);
          if (!headers.has('Accept')) headers.set('Accept', 'application/json');

          const res = await fetch(path, { ...options, headers });
          if (res.status === 403) throw new Error('FORBIDDEN');
          return res;
        }

        async function apiFetchJson(path, options = {}) {
          const res = await apiFetch(path, options);
          if (!res.ok) {
            let details = '';
            try {
              details = await res.text();
            } catch (e) {
              details = '';
            }
            throw new Error(`Request failed: ${res.status}${details ? ` ${details}` : ''}`);
          }
          return await res.json();
        }

        async function apiFetchJsonWithTotal(path, options = {}) {
          const res = await apiFetch(path, options);
          const totalHeader = res.headers.get('X-Total-Count');
          const totalCount = totalHeader !== null ? Number.parseInt(totalHeader, 10) : null;
          if (!res.ok) {
            let details = '';
            try {
              details = await res.text();
            } catch (e) {
              details = '';
            }
            throw new Error(`Request failed: ${res.status}${details ? ` ${details}` : ''}`);
          }
          const data = await res.json();
          return { data, totalCount: Number.isFinite(totalCount) ? totalCount : null };
        }

        function showAuth() {
          setHidden(el('auth-container'), false);
          setHidden(el('main-app'), true);
          setConnectionStatus('Auth', false);
        }

        function showApp() {
          setHidden(el('auth-container'), true);
          setHidden(el('main-app'), false);
        }

        function logout() {
          tokenState = '';
          clearStoredToken();
          setAuthError('');
          const tokenInput = el('admin-token');
          if (tokenInput) tokenInput.value = '';
          if (dashboardRefreshTimer) clearInterval(dashboardRefreshTimer);
          dashboardRefreshTimer = null;
          closeFixtureModal();
          showAuth();
        }

        function handleApiError(error) {
          console.error(error);
          if (error && (error.message === 'AUTH_REQUIRED' || error.message === 'FORBIDDEN')) {
            notify('🔒 Требуется ADMIN_TOKEN', 'warning');
            logout();
            return;
          }
          setConnectionStatus('Error', false);
          notify('❌ Ошибка загрузки данных', 'error');
        }

        function formatStatusLabel(status) {
          const s = String(status || '').toLowerCase();
          if (s === 'running') return { text: '🟡 running', cls: 'status-active' };
          if (s === 'ok') return { text: '🟢 ok', cls: 'status-active' };
          if (s === 'failed') return { text: '🔴 failed', cls: 'text-danger' };
          return { text: '⚪ idle', cls: 'status-idle' };
        }

        function jobRunStatusClass(status) {
          const s = String(status || '').toLowerCase();
          if (s === 'running') return 'job-status-running';
          if (s === 'ok' || s === 'completed') return 'job-status-completed';
          if (s === 'failed') return 'job-status-failed';
          return 'job-status-running';
        }

        function updateKPITrend(kpiId, trendValue) {
          if (trendValue === undefined || trendValue === null) return;
          const kpiElement = el(kpiId);
          if (!kpiElement) return;
          const parentCard = kpiElement.closest('.card');
          if (!parentCard) return;

          let trendElement = parentCard.querySelector('.kpi-trend');
          if (!trendElement) {
            trendElement = document.createElement('div');
            trendElement.className = 'kpi-trend small mt-1';
            parentCard.appendChild(trendElement);
          }

          const trendIcon = trendValue > 0 ? '📈' : trendValue < 0 ? '📉' : '➖';
          const trendColor = trendValue > 0 ? 'text-success' : trendValue < 0 ? 'text-danger' : 'text-muted';
          trendElement.className = `kpi-trend small mt-1 ${trendColor}`;
          trendElement.textContent = `${trendIcon} ${trendValue > 0 ? '+' : ''}${Number(trendValue).toFixed(1)}`;
        }

        function getDashboardDays() {
          const days = Number.parseInt(el('stats-period')?.value || '30', 10);
          return Number.isFinite(days) && days > 0 ? days : 30;
        }

        function getDateRangeForDays(days) {
          const now = Date.now();
          const dateTo = new Date(now).toISOString();
          const dateFrom = new Date(now - days * 24 * 60 * 60 * 1000).toISOString();
          return { dateFrom, dateTo };
        }

        function statusToUi(statusRaw) {
          const status = String(statusRaw || '').toUpperCase();
          if (status === 'WIN') return { icon: '🟢', badge: 'success' };
          if (status === 'LOSS') return { icon: '🔴', badge: 'danger' };
          if (status === 'VOID') return { icon: '⚫', badge: 'secondary' };
          return { icon: '🟡', badge: 'warning' };
        }

        function clampProb01(value) {
          const n = value === null || value === undefined ? null : Number(value);
          if (n === null || !Number.isFinite(n)) return null;
          const eps = 1e-15;
          return Math.max(eps, Math.min(1 - eps, n));
        }

        function calcBrier(prob01, outcome01) {
          const p = clampProb01(prob01);
          const y = Number(outcome01);
          if (p === null || !Number.isFinite(y)) return null;
          const d = p - y;
          return d * d;
        }

        function calcLogLoss(prob01, outcome01) {
          const p = clampProb01(prob01);
          const y = Number(outcome01);
          if (p === null || !Number.isFinite(y)) return null;
          return -(y * Math.log(p) + (1 - y) * Math.log(1 - p));
        }

        function formatEuro(value) {
          const n = value === null || value === undefined ? null : Number(value);
          if (n === null || !Number.isFinite(n)) return '—';
          return `€${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
        }

        function prettyJson(value) {
          try {
            return JSON.stringify(value ?? null, null, 2);
          } catch (e) {
            return String(value ?? '');
          }
        }

        function isFixtureModalOpen() {
          const overlay = el('fixture-modal');
          return Boolean(overlay && !overlay.classList.contains('is-hidden'));
        }

        function closeFixtureModal() {
          fixtureModalState.fixtureId = null;
          const overlay = el('fixture-modal');
          if (overlay) setHidden(overlay, true);
        }

        function renderDecisionBlock(decision, marketKey) {
          if (!decision || typeof decision !== 'object') return `<p class="text-muted">Нет decision payload (${escapeHtml(String(marketKey))})</p>`;
          const action = decision.action ? String(decision.action) : '—';
          const reason = decision.reason ? String(decision.reason) : '—';
          const selection = decision.selection ? String(decision.selection) : '—';
          const probSource = decision.prob_source ? String(decision.prob_source) : '—';
          const thr = decision.effective_threshold === null || decision.effective_threshold === undefined ? null : Number(decision.effective_threshold);
          const ev = decision.ev === null || decision.ev === undefined ? null : Number(decision.ev);

          const candidates = Array.isArray(decision.candidates) ? decision.candidates : [];
          const hasInRange = candidates.some((c) => c && Object.prototype.hasOwnProperty.call(c, 'in_range'));

          const reasonText = (() => {
            const r = String(reason || '').toLowerCase();
            if (r === 'ev_above_threshold') return 'EV выше порога';
            if (r === 'ev_below_threshold') return 'EV ниже порога';
            if (r === 'ev_below_threshold_or_out_of_range') return 'EV ниже порога или odd вне диапазона';
            if (r === 'no_candidate_in_range') return 'Нет кандидатов в диапазоне odds';
            if (r === 'no_odds') return 'Нет odds';
            return reason;
          })();

          const bullets = [];
          bullets.push(`Действие: ${action}`);
          bullets.push(`Причина: ${reasonText}`);
          if (selection && selection !== '—') bullets.push(`Выбор: ${selection}`);
          if (probSource && probSource !== '—') bullets.push(`Источник вероятностей: ${probSource}`);
          const lamTotal = decision.lam_total === null || decision.lam_total === undefined ? null : Number(decision.lam_total);
          if (lamTotal !== null && Number.isFinite(lamTotal)) bullets.push(`λ_total: ${lamTotal.toFixed(2)}`);
          if (ev !== null && Number.isFinite(ev) && thr !== null && Number.isFinite(thr)) {
            bullets.push(`EV: ${(ev * 100).toFixed(1)}% (порог ${(thr * 100).toFixed(1)}%)`);
          }

          const candidatesTable = candidates.length
            ? `
              <div class="table-responsive mt-2">
                <table class="table table-sm table-striped">
                  <thead class="table-dark">
                    <tr>
                      <th>Selection</th>
                      <th class="text-end">Prob</th>
                      <th class="text-end">Odd</th>
                      <th class="text-end">EV</th>
                      ${hasInRange ? '<th>In range</th>' : ''}
                    </tr>
                  </thead>
                  <tbody>
                    ${candidates.map((c) => {
                      const sel = c?.selection ? String(c.selection) : '—';
                      const prob = c?.prob === null || c?.prob === undefined ? null : Number(c.prob);
                      const odd = c?.odd === null || c?.odd === undefined ? null : Number(c.odd);
                      const evv = c?.ev === null || c?.ev === undefined ? null : Number(c.ev);
                      const inRange = Object.prototype.hasOwnProperty.call(c || {}, 'in_range') ? Boolean(c.in_range) : null;
                      const highlight = selection && sel === selection ? 'fw-bold' : '';
                      return `
                        <tr class="${highlight}">
                          <td>${escapeHtml(sel)}</td>
                          <td class="text-end">${escapeHtml(prob === null || !Number.isFinite(prob) ? '—' : formatPercent01(prob, 1))}</td>
                          <td class="text-end">${escapeHtml(odd === null || !Number.isFinite(odd) ? '—' : odd.toFixed(2))}</td>
                          <td class="text-end">${escapeHtml(evv === null || !Number.isFinite(evv) ? '—' : `${(evv * 100).toFixed(1)}%`)}</td>
                          ${hasInRange ? `<td>${inRange === null ? '—' : (inRange ? 'yes' : 'no')}</td>` : ''}
                        </tr>
                      `;
                    }).join('')}
                  </tbody>
                </table>
              </div>
            `
            : '';

          return `
            <div>
              <div class="fw-bold">${escapeHtml(String(marketKey))} — Почему так</div>
              <ul class="mt-2">
                ${bullets.map((b) => `<li>${escapeHtml(b)}</li>`).join('')}
              </ul>
              ${candidatesTable}
            </div>
          `;
        }

        function renderPostMatchBlock(pred, label) {
          if (!pred || typeof pred !== 'object') return '';
          const status = pred.status ? String(pred.status).toUpperCase() : '—';
          if (status !== 'WIN' && status !== 'LOSS') return '';
          const conf = pred.confidence === null || pred.confidence === undefined ? null : Number(pred.confidence);
          const odd = pred.odd === null || pred.odd === undefined ? null : Number(pred.odd);
          const ev = pred.ev === null || pred.ev === undefined ? null : Number(pred.ev);

          const outcome = status === 'WIN' ? 1 : 0;
          const brier = conf !== null ? calcBrier(conf, outcome) : null;
          const logloss = conf !== null ? calcLogLoss(conf, outcome) : null;
          const implied = odd !== null && Number.isFinite(odd) && odd > 0 ? 1 / odd : null;

          const items = [
            ['Status', status],
            ['Profit', pred.profit === null || pred.profit === undefined ? '—' : formatEuro(pred.profit)],
            ['Prob', conf === null ? '—' : formatPercent01(conf, 1)],
            ['Implied', implied === null ? '—' : formatPercent01(implied, 1)],
            ['Odd', odd === null ? '—' : odd.toFixed(2)],
            ['EV', ev === null ? '—' : `${(ev * 100).toFixed(1)}%`],
            ['Brier', brier === null ? '—' : brier.toFixed(3)],
            ['LogLoss', logloss === null ? '—' : logloss.toFixed(3)],
          ];

          return `
            <div class="card mt-3">
              <div class="card-title mb-0">${escapeHtml(label)} — Post‑match</div>
              <div class="table-responsive mt-2">
                <table class="table table-sm">
                  <tbody>
                    ${items.map(([k, v]) => `<tr><td class="text-muted">${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`).join('')}
                  </tbody>
                </table>
              </div>
            </div>
          `;
        }

        function renderTelegramHtml(text) {
          return String(text || '').replace(/\n/g, '<br>');
        }

        function getPublishDryRun() {
          const checkbox = el('publish-dry-run');
          return Boolean(checkbox && checkbox.checked);
        }

        function setPublishLog(message, level = 'info') {
          const logEl = el('publish-log');
          if (!logEl) return;
          if (!message) {
            logEl.textContent = '';
            logEl.className = 'small text-muted';
            return;
          }
          const ts = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          const cls = level === 'error' ? 'text-danger' : level === 'success' ? 'text-success' : 'text-muted';
          logEl.className = `small ${cls}`;
          logEl.textContent = `[${ts}] ${message}`;
        }

        function renderPublishPreview(data) {
          const markets = Array.isArray(data?.markets) ? data.markets : [];
          if (!markets.length) return '<p class="text-muted">Нет данных для публикации</p>';
          const mode = data?.mode ? String(data.mode) : 'manual';
          return `
            <div class="text-muted small mb-2">Mode: ${escapeHtml(mode)} • Preview (RU)</div>
            ${markets.map((m) => {
              if (!m?.headline || !m?.analysis) {
                const reason = Array.isArray(m?.reasons) ? m.reasons.join('; ') : 'нет данных';
                return `<div class="alert alert-warning">${escapeHtml(m?.market || 'market')}: ${escapeHtml(reason)}</div>`;
              }
              const tag = m.experimental ? '⚠️ EXPERIMENTAL' : 'OK';
              return `
                <div class="border rounded p-3 mb-3">
                  <div class="d-flex justify-content-between align-items-center mb-2">
                    <div class="fw-bold">${escapeHtml(m.market || 'market')}</div>
                    <span class="badge ${m.experimental ? 'bg-warning' : 'bg-success'}">${escapeHtml(tag)}</span>
                  </div>
                  <div class="telegram-preview mb-2">${renderTelegramHtml(m.headline)}</div>
                  <div class="telegram-preview">${renderTelegramHtml(m.analysis)}</div>
                </div>
              `;
            }).join('')}
          `;
        }

        async function loadPublishPreview(fixtureId) {
          const container = el('publish-preview');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';
          setPublishLog('Preview: загрузка...');
          try {
            const data = await apiFetchJson(`/api/v1/publish/preview?fixture_id=${encodeURIComponent(String(fixtureId))}`);
            if (container) container.innerHTML = renderPublishPreview(data);
            setPublishLog('Preview обновлен', 'success');
          } catch (e) {
            handleApiError(e);
            if (container) container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка загрузки')}</div>`;
            setPublishLog(`Preview ошибка: ${e?.message || 'ошибка'}`, 'error');
          }
        }

        async function loadPublishHistory(fixtureId) {
          const container = el('publish-history');
          if (container) container.textContent = 'История: загрузка...';
          try {
            const rows = await apiFetchJson(`/api/v1/publish/history?fixture_id=${encodeURIComponent(String(fixtureId))}`);
            if (!container) return;
            if (!Array.isArray(rows) || rows.length === 0) {
              container.textContent = 'История: нет данных';
              return;
            }
            container.innerHTML = `
              <div class="table-responsive">
                <table class="table table-sm table-striped">
                  <thead class="table-dark">
                    <tr>
                      <th>Time</th>
                      <th>Market</th>
                      <th>Lang</th>
                      <th>Status</th>
                      <th>Msg</th>
                      <th>Exp</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${rows.slice(0, 10).map((row) => `
                      <tr>
                        <td>${escapeHtml(formatDateTime(row?.created_at))}</td>
                        <td>${escapeHtml(row?.market || '—')}</td>
                        <td>${escapeHtml(row?.language || '—')}</td>
                        <td>${escapeHtml(row?.status || '—')}</td>
                        <td>${escapeHtml(String(row?.headline_message_id || row?.analysis_message_id || '—'))}</td>
                        <td>${row?.experimental ? 'yes' : 'no'}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            `;
          } catch (e) {
            handleApiError(e);
            if (container) container.textContent = 'История: ошибка';
            setPublishLog(`History ошибка: ${e?.message || 'ошибка'}`, 'error');
          }
        }

        async function publishNow(fixtureId, force = false) {
          const payload = { fixture_id: Number(fixtureId), force: Boolean(force), dry_run: getPublishDryRun() };
          setPublishLog(payload.dry_run ? 'Send: dry-run...' : 'Send: отправка...');
          try {
            const res = await apiFetchJson('/api/v1/publish', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
            await loadPublishHistory(fixtureId);
            await loadPublishPreview(fixtureId);
            const okCount = Array.isArray(res?.results) ? res.results.filter((r) => r.status === 'ok').length : 0;
            const dryCount = Array.isArray(res?.results) ? res.results.filter((r) => r.status === 'dry_run').length : 0;
            if (res?.dry_run) {
              setPublishLog(`Dry-run: ${dryCount} публикаций`, 'success');
              notify(`Dry-run: ${dryCount}`, 'info');
            } else {
              setPublishLog(`Send OK: ${okCount}`, okCount ? 'success' : 'error');
              notify(`Publish: ok ${okCount}`, okCount ? 'success' : 'warning');
            }
          } catch (e) {
            handleApiError(e);
            setPublishLog(`Send ошибка: ${e?.message || 'ошибка'}`, 'error');
            notify('Publish failed', 'error');
          }
        }

        function renderFixtureModalContent(data) {
          const f = data?.fixture || {};
          const teams = f.home && f.away ? `${String(f.home)} vs ${String(f.away)}` : `Fixture ${String(f.id ?? '—')}`;
          const league = f.league ? String(f.league) : '—';
          const kickoff = f.kickoff ? formatDateTime(f.kickoff) : '—';
          const status = f.status ? String(f.status) : '—';
          const score = f.home_goals !== null && f.home_goals !== undefined && f.away_goals !== null && f.away_goals !== undefined
            ? `${f.home_goals}-${f.away_goals}`
            : '—';
          const homeName = f.home ? String(f.home) : 'Home';
          const awayName = f.away ? String(f.away) : 'Away';
          const homeLogo = logoHtml(f.home_logo_url, homeName, 'team', 'lg');
          const awayLogo = logoHtml(f.away_logo_url, awayName, 'team', 'lg');
          const leagueLogo = logoHtml(f.league_logo_url, league, 'league', 'sm');

          const p1 = data?.prediction_1x2 || null;
          const pt = data?.prediction_totals || null;
          const d1 = data?.decisions?.['1X2'] || null;
          const dt = data?.decisions?.['TOTAL'] || null;
          const infoBlocks = INFO_MARKETS.map((m) => renderInfoMarketBlock(m, data?.decisions?.[m.id] || null)).join('');
          const ff = p1?.feature_flags && typeof p1.feature_flags === 'object' ? p1.feature_flags : {};

          const md = ff?.market_diff === null || ff?.market_diff === undefined ? null : Number(ff.market_diff);
          const thr = ff?.effective_threshold === null || ff?.effective_threshold === undefined ? null : Number(ff.effective_threshold);

          const keyFacts = [
            ['League', league],
            ['Kickoff', kickoff],
            ['Fixture status', status],
            ['Score', score],
            ['Odds fetched', data?.odds?.fetched_at ? formatDateTime(data.odds.fetched_at) : '—'],
            ['Pre‑kickoff snapshot', data?.odds_pre_kickoff?.fetched_at ? formatDateTime(data.odds_pre_kickoff.fetched_at) : '—'],
            ['Prob source', ff?.prob_source ? String(ff.prob_source) : d1?.prob_source ? String(d1.prob_source) : '—'],
            ['λ home / away / total', `${formatFixed(ff?.lam_home, 2)} / ${formatFixed(ff?.lam_away, 2)} / ${formatFixed(ff?.lam_total, 2)}`],
            ['Elo home / away / diff', `${formatFixed(ff?.elo_home, 1)} / ${formatFixed(ff?.elo_away, 1)} / ${formatFixed(ff?.elo_diff, 1)}`],
            ['Adj factor', formatFixed(ff?.adj_factor, 3)],
            ['Signal', p1?.signal_score === null || p1?.signal_score === undefined ? '—' : formatPercent01(p1.signal_score, 1)],
            ['Signal raw', formatFixed(ff?.signal_score_raw, 3)],
            ['Signal parts (samples/vol/elo)', `${formatFixed(ff?.samples_score, 3)} / ${formatFixed(ff?.volatility_score, 3)} / ${formatFixed(ff?.elo_gap_score, 3)}`],
            ['xPts diff', ff?.xpts_diff === null || ff?.xpts_diff === undefined ? '—' : String(ff.xpts_diff)],
            ['Draw freq', ff?.league_draw_freq === null || ff?.league_draw_freq === undefined ? '—' : formatPercent01(ff.league_draw_freq, 1)],
            ['Dixon‑Coles rho', ff?.dc_rho === null || ff?.dc_rho === undefined ? '—' : String(ff.dc_rho)],
            ['Calib alpha', ff?.calib_alpha === null || ff?.calib_alpha === undefined ? '—' : String(ff.calib_alpha)],
            ['Standings delta', ff?.standings_delta === null || ff?.standings_delta === undefined ? '—' : String(ff.standings_delta)],
            ['Injuries (home/away)', (ff?.injuries_home !== undefined || ff?.injuries_away !== undefined) ? `${String(ff.injuries_home ?? 0)} / ${String(ff.injuries_away ?? 0)}` : '—'],
            ['Injury penalty (home/away)', `${formatFixed(ff?.injury_penalty_home, 3)} / ${formatFixed(ff?.injury_penalty_away, 3)}`],
            ['Injury uncertainty', formatFixed(ff?.injury_uncertainty, 3)],
            ['Goal variance', ff?.goal_variance === null || ff?.goal_variance === undefined ? '—' : String(ff.goal_variance)],
            ['Market diff', md === null || !Number.isFinite(md) ? '—' : `${(md * 100).toFixed(1)}%`],
            ['Threshold', thr === null || !Number.isFinite(thr) ? '—' : `${(thr * 100).toFixed(1)}%`],
            ['Backtest', ff?.backtest ? 'true' : 'false'],
            ['BT kind', ff?.bt_kind ? String(ff.bt_kind) : '—'],
          ];

          const predCard = (pred, label) => {
            if (!pred) return `<div class="card"><div class="card-title mb-0">${escapeHtml(label)}</div><p class="text-muted mt-2">Нет данных</p></div>`;
            const statusRaw = pred.status ? String(pred.status) : '—';
            const { badge } = statusToUi(statusRaw);
            const odd = pred.odd === null || pred.odd === undefined ? '—' : Number(pred.odd).toFixed(2);
            const ev = pred.ev === null || pred.ev === undefined ? '—' : `${(Number(pred.ev) * 100).toFixed(1)}%`;
            const prob = pred.confidence === null || pred.confidence === undefined ? '—' : formatPercent01(pred.confidence, 1);
            const pick = pred.pick ? String(pred.pick).replaceAll('_', ' ') : '—';
            const profit = pred.profit === null || pred.profit === undefined ? '—' : formatEuro(pred.profit);
            return `
              <div class="card">
                <div class="card-title mb-0">${escapeHtml(label)}</div>
                <div class="mt-2">
                  <div class="fw-bold">${escapeHtml(pick)}</div>
                  <div class="text-muted small">Prob ${escapeHtml(prob)} • Odd ${escapeHtml(odd)} • EV ${escapeHtml(ev)}</div>
                  <div class="mt-2"><span class="badge bg-${escapeHtml(badge)}">${escapeHtml(statusRaw)}</span> <span class="ms-2">${escapeHtml(profit)}</span></div>
                </div>
              </div>
            `;
          };

          const rawBlocks = `
            <details class="mt-3">
              <summary class="fw-bold">Raw: decisions</summary>
              <pre class="bg-light p-3 border rounded pre-scroll mt-2">${escapeHtml(prettyJson(data?.decisions || {}))}</pre>
            </details>
            <details class="mt-2">
              <summary class="fw-bold">Raw: match_indices</summary>
              <pre class="bg-light p-3 border rounded pre-scroll mt-2">${escapeHtml(prettyJson(data?.match_indices || null))}</pre>
            </details>
            <details class="mt-2">
              <summary class="fw-bold">Raw: odds</summary>
              <pre class="bg-light p-3 border rounded pre-scroll mt-2">${escapeHtml(prettyJson({ odds: data?.odds || null, odds_pre_kickoff: data?.odds_pre_kickoff || null }))}</pre>
            </details>
          `;

          return `
            <div class="card fixture-hero">
              <div class="fixture-hero-top">
                <div class="fixture-league">
                  ${leagueLogo}
                  <span class="league-name">${escapeHtml(league)}</span>
                </div>
                <div class="fixture-meta">
                  <span class="meta-pill">${escapeHtml(kickoff)}</span>
                  <span class="meta-pill">${escapeHtml(status)}</span>
                </div>
              </div>
              <div class="fixture-hero-teams">
                <div class="team-hero">
                  ${homeLogo}
                  <span class="team-name">${escapeHtml(homeName)}</span>
                </div>
                <div class="hero-score">${escapeHtml(score)}</div>
                <div class="team-hero">
                  ${awayLogo}
                  <span class="team-name">${escapeHtml(awayName)}</span>
                </div>
              </div>
            </div>

            <div class="row mt-3">
              <div class="col-md-6">
                ${predCard(p1, '1X2')}
                ${renderPostMatchBlock(p1, '1X2')}
              </div>
              <div class="col-md-6">
                ${predCard(pt, 'TOTAL')}
                ${renderPostMatchBlock(pt, 'TOTAL')}
              </div>
            </div>

            <div class="row mt-3">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-title mb-0">Key facts</div>
                  <div class="table-responsive mt-2">
                    <table class="table table-sm">
                      <tbody>
                        ${keyFacts.map(([k, v]) => `<tr><td class="text-muted">${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div class="col-md-6">
                <div class="card">
                  ${renderDecisionBlock(d1, '1X2')}
                  <div class="mt-3"></div>
                  ${renderDecisionBlock(dt, 'TOTAL')}
                </div>
              </div>
            </div>

            <div class="card mt-3">
              <div class="card-title mb-0">Info markets</div>
              <div class="info-markets">
                ${infoBlocks}
              </div>
            </div>

            <div class="card mt-3">
              <div class="card-header">
                <h3 class="card-title mb-0">📣 Publish (Telegram)</h3>
                <div class="d-flex align-items-center gap-md">
                  <label class="small text-muted">
                    <input type="checkbox" id="publish-dry-run"> dry-run
                  </label>
                  <div class="btn-group">
                    <button type="button" class="btn-secondary btn-sm" data-action="publish-refresh" data-fixture-id="${escapeHtml(String(f.id ?? ''))}">🔄 Preview</button>
                    <button type="button" class="btn btn-success btn-sm" data-action="publish-now" data-fixture-id="${escapeHtml(String(f.id ?? ''))}">Send</button>
                    <button type="button" class="btn btn-danger btn-sm" data-action="publish-now" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" data-force="1">Force</button>
                  </div>
                </div>
              </div>
              <div id="publish-log" class="small text-muted p-3"></div>
              <div id="publish-preview" class="p-3" data-fixture-id="${escapeHtml(String(f.id ?? ''))}">Загрузка...</div>
              <div id="publish-history" class="p-3 text-muted">История: —</div>
            </div>

            ${rawBlocks}
          `;
        }

        async function openFixtureModal(fixtureId) {
          const overlay = el('fixture-modal');
          const bodyEl = el('fixture-modal-body');
          const titleEl = el('fixture-modal-title');
          if (!overlay || !bodyEl || !titleEl) return;

          const fid = String(fixtureId || '').trim();
          if (!fid) return;

          fixtureModalState.fixtureId = fid;
          setHidden(overlay, false);
          titleEl.textContent = `Match ${fid}`;
          bodyEl.innerHTML = '<p class="text-muted">Загрузка...</p>';

          try {
            const cached = fixtureModalState.cache.get(fid);
            const data = cached || await apiFetchJson(`/api/v1/fixtures/${encodeURIComponent(fid)}/details`);
            fixtureModalState.cache.set(fid, data);
            const f = data?.fixture || {};
            const teams = f.home && f.away ? `${String(f.home)} vs ${String(f.away)}` : `Match ${fid}`;
            const league = f.league ? String(f.league) : '';
            titleEl.textContent = league ? `${teams} • ${league}` : teams;
            bodyEl.innerHTML = renderFixtureModalContent(data);
            applyLogoFallbacks(bodyEl);
            await loadPublishPreview(fid);
            await loadPublishHistory(fid);
          } catch (e) {
            console.error(e);
            bodyEl.innerHTML = `<div class="alert alert-danger">Не удалось загрузить match details (${escapeHtml(String(e?.message || e))})</div>`;
          }
        }

        function renderBetsHistoryControls(shownCount, totalCount) {
          const controls = el('bets-history-controls');
          if (!controls) return;
          const total = Number.isFinite(totalCount) ? totalCount : null;
          const shown = Number.isFinite(shownCount) ? shownCount : 0;
          const label = betsHistoryState.expanded ? 'Свернуть' : 'Все ставки';
          const summary = total !== null ? `Показано ${shown} / ${total}` : `Показано ${shown}`;
          controls.innerHTML = `
            <div class="d-flex justify-content-between align-items-center">
              <div class="text-muted small">${escapeHtml(summary)}</div>
              <div class="btn-group">
                <button type="button" class="btn-secondary btn-sm" data-action="toggle-bets-history">
                  ${escapeHtml(label)}${total !== null ? ` (${total})` : ''}
                </button>
                <button type="button" class="btn-secondary btn-sm" data-action="bets-open-all-time" title="Показать историю ставок за всё время">
                  Всё время
                </button>
              </div>
            </div>
          `;
        }

        function renderBetsHistoryPanel() {
          const panel = el('bets-history-panel');
          if (!panel) return;

          const periodText = betsHistoryState.allTime ? 'Период: всё время' : `Период: последние ${getDashboardDays()} дней`;
          panel.innerHTML = `
            <div class="border rounded p-3 bg-light">
              <div class="d-flex justify-content-between align-items-center">
                <div>
                  <div class="fw-bold">📋 История ставок</div>
                  <div id="bets-period-hint" class="text-muted small">${escapeHtml(periodText)}</div>
                </div>
                <div class="btn-group">
                  <button type="button" class="btn-secondary btn-sm" data-action="bets-refresh">🔄</button>
                  <button type="button" class="btn-secondary btn-sm" data-action="toggle-bets-history">✕</button>
                </div>
              </div>

              <div class="row mt-3">
                <div class="col-md-3">
                  <label class="form-label">Market</label>
                  <select id="bets-market" class="form-select">
                    <option value="all">all</option>
                    <option value="1x2">1x2</option>
                    <option value="totals">totals</option>
                  </select>
                </div>
                <div class="col-md-3">
                  <label class="form-label">Status</label>
                  <select id="bets-status" class="form-select">
                    <option value="">all</option>
                    <option value="WIN">WIN</option>
                    <option value="LOSS">LOSS</option>
                    <option value="PENDING">PENDING</option>
                    <option value="VOID">VOID</option>
                  </select>
                </div>
                <div class="col-md-3">
                  <label class="form-label">Sort</label>
                  <select id="bets-sort" class="form-select">
                    <option value="kickoff_desc">kickoff ↓</option>
                    <option value="ev_desc">ev ↓</option>
                    <option value="profit_desc">profit ↓</option>
                    <option value="signal_desc">signal ↓</option>
                  </select>
                </div>
                <div class="col-md-3">
                  <label class="form-label">Page size</label>
                  <select id="bets-limit" class="form-select">
                    <option value="25">25</option>
                    <option value="50">50</option>
                    <option value="100">100</option>
                    <option value="250">250</option>
                    <option value="500">500</option>
                  </select>
                </div>
              </div>

              <div class="row mt-2">
                <div class="col-md-6">
                  <label class="form-label">Team (optional)</label>
                  <input id="bets-team" class="form-input" placeholder="e.g. Arsenal">
                </div>
                <div class="col-md-3">
                  <label class="form-label">Options</label>
                  <div class="d-flex align-items-center gap-md">
                    <label class="small text-muted"><input id="bets-settled-only" type="checkbox"> Завершенные</label>
                    <label class="small text-muted"><input id="bets-all-time" type="checkbox"> All time</label>
                  </div>
                </div>
                <div class="col-md-3">
                  <label class="form-label">&nbsp;</label>
                  <button type="button" class="btn btn-primary" data-action="bets-apply">Apply</button>
                </div>
              </div>

              <div class="d-flex justify-content-between align-items-center mt-2">
                <div id="bets-history-task" class="text-muted small"></div>
                <div class="btn-group">
                  <button type="button" class="btn-secondary btn-sm" data-action="bets-load-all">Load all (max 5000)</button>
                  <button type="button" class="btn-secondary btn-sm" data-action="bets-export-csv">Export CSV</button>
                </div>
              </div>

              <div id="bets-history-summary" class="text-muted small mt-3"></div>
              <div id="bets-history-result" class="mt-3"></div>

              <div class="d-flex justify-content-between align-items-center mt-3">
                <button type="button" class="btn-secondary btn-sm" data-action="bets-prev">← Prev</button>
                <button type="button" class="btn-secondary btn-sm" data-action="bets-next">Next →</button>
              </div>
            </div>
          `;

          const marketEl = el('bets-market');
          if (marketEl) marketEl.value = betsHistoryState.market;
          const statusEl = el('bets-status');
          if (statusEl) statusEl.value = betsHistoryState.status;
          const settledEl = el('bets-settled-only');
          if (settledEl) settledEl.checked = Boolean(betsHistoryState.settledOnly);
          const sortEl = el('bets-sort');
          if (sortEl) sortEl.value = betsHistoryState.sort;
          const limitEl = el('bets-limit');
          if (limitEl) limitEl.value = String(betsHistoryState.limit);
          const teamEl = el('bets-team');
          if (teamEl) teamEl.value = betsHistoryState.team;
          const allTimeEl = el('bets-all-time');
          if (allTimeEl) allTimeEl.checked = Boolean(betsHistoryState.allTime);
        }

        function readBetsHistoryFiltersFromDom() {
          betsHistoryState.market = el('bets-market')?.value || 'all';
          betsHistoryState.status = (el('bets-status')?.value || '').trim();
          betsHistoryState.settledOnly = Boolean(el('bets-settled-only')?.checked);
          betsHistoryState.sort = el('bets-sort')?.value || 'kickoff_desc';
          betsHistoryState.team = (el('bets-team')?.value || '').trim();
          const limit = Number.parseInt(el('bets-limit')?.value || String(betsHistoryState.limit), 10);
          betsHistoryState.limit = Number.isFinite(limit) && limit > 0 ? limit : 50;
          betsHistoryState.allTime = Boolean(el('bets-all-time')?.checked);
        }

        function buildBetsHistorySearchParams({ limit, offset }) {
          const sp = new URLSearchParams();
          sp.set('market', betsHistoryState.market);
          sp.set('sort', betsHistoryState.sort);
          sp.set('limit', String(limit));
          sp.set('offset', String(offset));
          if (betsHistoryState.status) sp.set('status', betsHistoryState.status);
          if (betsHistoryState.team) sp.set('team', betsHistoryState.team);
          if (!betsHistoryState.status && betsHistoryState.settledOnly) sp.set('completed_only', '1');

          if (betsHistoryState.allTime) {
            sp.set('all_time', '1');
          } else {
            const { dateFrom, dateTo } = getDateRangeForDays(getDashboardDays());
            sp.set('date_from', dateFrom);
            sp.set('date_to', dateTo);
          }
          return sp;
        }

        function betsHistoryCacheKey() {
          const sp = buildBetsHistorySearchParams({ limit: 1, offset: 0 });
          sp.delete('limit');
          sp.delete('offset');
          return sp.toString();
        }

        function renderBetsHistoryRows(rows) {
          const resultEl = el('bets-history-result');
          if (!resultEl) return;
          if (!Array.isArray(rows) || rows.length === 0) {
            resultEl.innerHTML = '<p class="text-muted">Нет данных</p>';
            return;
          }

          resultEl.innerHTML = `
            <div class="table-responsive">
              <table class="table table-sm table-striped">
                <thead class="table-dark">
                  <tr>
                    <th>Date</th>
                    <th>Match</th>
                    <th>Pick</th>
                    <th>Odd</th>
                    <th>Status</th>
                    <th class="text-end">Profit</th>
                    <th>League</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows.map((bet) => {
                    const kickoffRaw = bet.kickoff || bet.created_at || '';
                    const kickoffText = kickoffRaw ? new Date(kickoffRaw).toLocaleString('ru-RU', { month: '2-digit', day: '2-digit', year: 'numeric' }) : '—';
                    const homeName = String(bet.home || '').trim();
                    const awayName = String(bet.away || '').trim();
                    const matchText = homeName && awayName ? `${homeName} vs ${awayName}` : (bet.teams || '—');
                    const marketText = bet.market ? String(bet.market).toUpperCase() : '';
                    const pickRaw = bet.pick ? String(bet.pick).replaceAll('_', ' ') : '—';
                    const pickText = marketText ? `${marketText}: ${pickRaw}` : pickRaw;
                    const oddText = bet.odd === null || bet.odd === undefined ? '—' : String(bet.odd);
                    const statusRaw = String(bet.status || '—');
                    const { badge } = statusToUi(statusRaw);
                    const profit = bet.profit === null || bet.profit === undefined ? null : Number(bet.profit);
                    const profitText = profit === null ? '—' : `€${profit >= 0 ? '+' : ''}${profit.toFixed(2)}`;
                    const profitCls = profit === null ? 'text-muted' : profit >= 0 ? 'text-success' : 'text-danger';
                    const leagueText = bet.league || '—';
                    const scoreText = bet.score ? String(bet.score) : '';
                    const fixtureStatus = bet.fixture_status ? String(bet.fixture_status) : '';
                    const homeLogo = logoHtml(bet.home_logo_url, homeName, 'team', 'sm');
                    const awayLogo = logoHtml(bet.away_logo_url, awayName, 'team', 'sm');
                    const leagueLogo = logoHtml(bet.league_logo_url, leagueText, 'league', 'xs');
                    const fid = bet.fixture_id === null || bet.fixture_id === undefined ? '' : String(bet.fixture_id);

                    return `
                      <tr class="cursor-pointer" data-action="fixture-details" data-fixture-id="${escapeHtml(fid)}" title="Открыть match details">
                        <td>${escapeHtml(kickoffText)}</td>
                        <td class="match-cell" title="${escapeHtml(matchText)}">
                          <div class="match-row">
                            <div class="team-chip">
                              ${homeLogo}
                              <span class="team-name">${escapeHtml(homeName || '—')}</span>
                            </div>
                            <span class="vs">vs</span>
                            <div class="team-chip">
                              ${awayLogo}
                              <span class="team-name">${escapeHtml(awayName || '—')}</span>
                            </div>
                          </div>
                          <div class="match-meta">
                            ${scoreText ? `<span class="meta-pill meta-score">${escapeHtml(scoreText)}</span>` : ''}
                            ${fixtureStatus ? `<span class="meta-pill">${escapeHtml(fixtureStatus)}</span>` : ''}
                          </div>
                        </td>
                        <td class="text-truncate table-cell-truncate" title="${escapeHtml(pickText)}">${escapeHtml(pickText)}</td>
                        <td>${escapeHtml(oddText)}</td>
                        <td><span class="badge bg-${escapeHtml(badge)}">${escapeHtml(statusRaw)}</span></td>
                        <td class="text-end ${escapeHtml(profitCls)} fw-bold">${escapeHtml(profitText)}</td>
                        <td class="league-cell" title="${escapeHtml(leagueText)}">
                          <div class="league-chip">
                            ${leagueLogo}
                            <span class="league-name">${escapeHtml(leagueText)}</span>
                          </div>
                        </td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          `;
          applyLogoFallbacks(resultEl);
        }

        async function fetchBetsHistoryAll({ maxRows = 5000, onProgress } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return { rows: [], totalCount: null, truncated: false };

          const cap = clampInt(maxRows, 1, 20000, 5000);
          const batchLimit = 500;
          const all = [];
          let offset = 0;
          let total = null;

          while (all.length < cap) {
            const limit = Math.min(batchLimit, cap - all.length);
            const sp = buildBetsHistorySearchParams({ limit, offset });
            const { data, totalCount } = await apiFetchJsonWithTotal(`/api/v1/bets/history?${sp.toString()}`);
            const rows = Array.isArray(data) ? data : [];
            if (total === null) total = totalCount;

            all.push(...rows);
            offset += rows.length;

            if (typeof onProgress === 'function') {
              try {
                onProgress({ loaded: all.length, total });
              } catch (e) {
                // ignore
              }
            }

            if (rows.length === 0) break;
            if (total !== null && offset >= total) break;
            if (rows.length < limit) break;
          }

          const truncated = total !== null ? all.length < total : all.length >= cap;
          return { rows: all, totalCount: total, truncated };
        }

        async function loadBetsHistoryPage({ resetOffset = false } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return;

          readBetsHistoryFiltersFromDom();
          if (resetOffset) betsHistoryState.offset = 0;
          scheduleUiStateSave();
          betsHistoryState.viewMode = 'page';

          const periodEl = el('bets-period-hint');
          if (periodEl) {
            periodEl.textContent = betsHistoryState.allTime ? 'Период: всё время' : `Период: последние ${getDashboardDays()} дней`;
          }

          const resultEl = el('bets-history-result');
          const summaryEl = el('bets-history-summary');
          const taskEl = el('bets-history-task');
          if (taskEl) taskEl.textContent = '';
          if (resultEl) resultEl.innerHTML = '<p class="text-muted">Загрузка...</p>';
          if (summaryEl) summaryEl.textContent = '';

          const sp = buildBetsHistorySearchParams({ limit: betsHistoryState.limit, offset: betsHistoryState.offset });
          const { data, totalCount } = await apiFetchJsonWithTotal(`/api/v1/bets/history?${sp.toString()}`);
          const rows = Array.isArray(data) ? data : [];
          betsHistoryState.total = totalCount;

          renderBetsHistoryRows(rows);

          const total = Number.isFinite(totalCount) ? totalCount : null;
          const from = rows.length ? betsHistoryState.offset + 1 : 0;
          const to = betsHistoryState.offset + rows.length;
          if (summaryEl) {
            summaryEl.textContent = total !== null ? `Показано ${from}-${to} из ${total}` : `Показано ${rows.length}`;
          }

          const prevBtn = panel.querySelector('[data-action="bets-prev"]');
          const nextBtn = panel.querySelector('[data-action="bets-next"]');
          if (prevBtn) prevBtn.disabled = betsHistoryState.offset <= 0;
          if (nextBtn && total !== null) nextBtn.disabled = betsHistoryState.offset + betsHistoryState.limit >= total;
        }

        async function loadBetsHistoryAll({ maxRows = 5000 } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return;

          readBetsHistoryFiltersFromDom();
          betsHistoryState.viewMode = 'all';
          scheduleUiStateSave();

          const periodEl = el('bets-period-hint');
          if (periodEl) {
            periodEl.textContent = betsHistoryState.allTime ? 'Период: всё время' : `Период: последние ${getDashboardDays()} дней`;
          }

          const resultEl = el('bets-history-result');
          const summaryEl = el('bets-history-summary');
          const taskEl = el('bets-history-task');
          if (resultEl) resultEl.innerHTML = '<p class="text-muted">Загрузка (batch)...</p>';
          if (summaryEl) summaryEl.textContent = '';

          const key = betsHistoryCacheKey();
          if (betsHistoryState.cacheKey === key && Array.isArray(betsHistoryState.cacheRows) && betsHistoryState.cacheRows.length) {
            renderBetsHistoryRows(betsHistoryState.cacheRows);
            const total = betsHistoryState.cacheTotal;
            const shown = betsHistoryState.cacheRows.length;
            if (summaryEl) summaryEl.textContent = total !== null ? `Показано ${shown} из ${total}` : `Показано ${shown}`;
          } else {
            const { rows, totalCount, truncated } = await fetchBetsHistoryAll({
              maxRows,
              onProgress: ({ loaded, total }) => {
                if (taskEl) {
                  taskEl.textContent = total !== null ? `Загружено ${loaded} из ${total}...` : `Загружено ${loaded}...`;
                }
              },
            });
            betsHistoryState.cacheKey = key;
            betsHistoryState.cacheRows = rows;
            betsHistoryState.cacheTotal = totalCount;
            betsHistoryState.cacheTruncated = truncated;

            renderBetsHistoryRows(rows);
            if (summaryEl) {
              const total = Number.isFinite(totalCount) ? totalCount : null;
              const note = truncated ? ` (cap ${clampInt(maxRows, 1, 20000, 5000)})` : '';
              summaryEl.textContent = total !== null ? `Показано ${rows.length} из ${total}${note}` : `Показано ${rows.length}${note}`;
            }
          }

          if (taskEl) taskEl.textContent = betsHistoryState.cacheTruncated ? '⚠️ Ограничено по cap' : '';
          const prevBtn = panel.querySelector('[data-action="bets-prev"]');
          const nextBtn = panel.querySelector('[data-action="bets-next"]');
          if (prevBtn) prevBtn.disabled = true;
          if (nextBtn) nextBtn.disabled = true;
        }

        async function exportBetsHistoryCsv({ maxRows = 5000 } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return;

          readBetsHistoryFiltersFromDom();
          scheduleUiStateSave();

          const taskEl = el('bets-history-task');
          const key = betsHistoryCacheKey();
          let rows = betsHistoryState.cacheKey === key && Array.isArray(betsHistoryState.cacheRows) ? betsHistoryState.cacheRows : null;
          let totalCount = betsHistoryState.cacheKey === key ? betsHistoryState.cacheTotal : null;

          if (!rows) {
            if (taskEl) taskEl.textContent = 'Готовлю CSV (batch fetch)...';
            const res = await fetchBetsHistoryAll({
              maxRows,
              onProgress: ({ loaded, total }) => {
                if (taskEl) taskEl.textContent = total !== null ? `CSV: загружено ${loaded} из ${total}...` : `CSV: загружено ${loaded}...`;
              },
            });
            rows = res.rows;
            totalCount = res.totalCount;
            betsHistoryState.cacheKey = key;
            betsHistoryState.cacheRows = rows;
            betsHistoryState.cacheTotal = totalCount;
            betsHistoryState.cacheTruncated = res.truncated;
          }

          const columns = ['kickoff', 'market', 'league', 'home', 'away', 'pick', 'odd', 'confidence', 'ev', 'status', 'profit', 'fixture_status', 'fixture_id'];
          const csv = toCsv(rows, columns);
          const stamp = new Date().toISOString().slice(0, 19).replaceAll(':', '-');
          const note = betsHistoryState.cacheTruncated ? `_cap-${clampInt(maxRows, 1, 20000, 5000)}` : '';
          const totalSuffix = Number.isFinite(totalCount) ? `_total-${totalCount}` : '';
          downloadTextFile(`bets_history_${stamp}${totalSuffix}${note}.csv`, csv, 'text/csv');
          if (taskEl) taskEl.textContent = '✅ CSV готов';
        }

        async function toggleBetsHistory() {
          betsHistoryState.expanded = !betsHistoryState.expanded;
          const panel = el('bets-history-panel');
          if (!panel) return;
          renderBetsHistoryControls(betsHistoryState.lastRecentShown, betsHistoryState.lastRecentTotal);
          scheduleUiStateSave();

          if (!betsHistoryState.expanded) {
            setHidden(panel, true);
            panel.innerHTML = '';
            return;
          }

          setHidden(panel, false);
          renderBetsHistoryPanel();
          await loadBetsHistoryPage();
        }

        async function loadDashboardData() {
          const days = getDashboardDays();
          const { dateFrom, dateTo } = getDateRangeForDays(days);
          const recentQuery = new URLSearchParams({
            market: 'all',
            sort: 'kickoff_desc',
            limit: '10',
            offset: '0',
            date_from: dateFrom,
            date_to: dateTo,
            completed_only: '1',
          });
          const [dashboardData, freshnessData, recentBetsRes] = await Promise.all([
            apiFetchJson(`/api/v1/dashboard?days=${days}`),
            apiFetchJson('/api/v1/freshness'),
            apiFetchJsonWithTotal(`/api/v1/bets/history?${recentQuery.toString()}`),
          ]);
          const recentBets = Array.isArray(recentBetsRes?.data) ? recentBetsRes.data : [];
          const recentTotal = recentBetsRes?.totalCount;
          betsHistoryState.lastRecentTotal = recentTotal;
          betsHistoryState.lastRecentShown = recentBets.length;

          const kpis = dashboardData?.kpis || {};
          const riskMetrics = dashboardData?.risk_metrics || {};
          const totalProfit = Number(kpis.total_profit?.value ?? 0);
          const roi = Number(kpis.roi?.value ?? 0);
          const totalBets = Number(kpis.total_bets?.value ?? 0);

          const profitEl = el('total-profit');
          if (profitEl) profitEl.textContent = `€${totalProfit.toFixed(2)}`;
          const roiEl = el('roi');
          if (roiEl) roiEl.textContent = `${roi.toFixed(1)}%`;
          const profitFactorEl = el('profit-factor');
          const profitFactorNoteEl = el('profit-factor-note');
          if (profitFactorEl) {
            const rawPf = riskMetrics?.profit_factor;
            const pf = Number.isFinite(rawPf) ? Number(rawPf) : null;
            profitFactorEl.textContent = pf === null ? '—' : pf.toFixed(2);
            if (profitFactorNoteEl) {
              let note = 'влияние худшего убытка';
              const rawNote = String(riskMetrics?.profit_factor_note || '');
              let detail = 'Отношение total_profit к (total_profit − |max_loss|) за период. Ближе к 1 — влияние меньше; чем выше — тем сильнее влияние.';
              if (pf === null) {
                if (rawNote === 'no_losses') note = 'нет убыточных ставок';
                else if (rawNote === 'zero_denominator') note = 'не рассчитан (деление на 0)';
                else if (rawNote) note = rawNote;
                if (rawNote === 'no_losses') {
                  detail = 'Нет убыточных ставок за период — метрика не рассчитывается.';
                } else if (rawNote === 'zero_denominator') {
                  detail = 'Невозможно рассчитать из-за деления на 0.';
                } else if (rawNote) {
                  detail = rawNote;
                }
              }
              profitFactorNoteEl.textContent = note;
              profitFactorNoteEl.title = detail;
              profitFactorEl.title = detail;
            }
          }
          const betsEl = el('total-bets');
          if (betsEl) betsEl.textContent = String(totalBets);
          const periodEl = el('period-days');
          if (periodEl) periodEl.textContent = String(dashboardData?.period_days ?? 0);

          updateKPITrend('total-profit', kpis.total_profit?.trend);
          updateKPITrend('roi', kpis.roi?.trend);
          updateKPITrend('total-bets', kpis.total_bets?.trend);

          // Data freshness (sync_data + key tables)
          const freshnessEl = el('data-freshness');
          if (freshnessEl) {
            const serverTime = freshnessData?.server_time ? new Date(freshnessData.server_time) : new Date();
            const lastOk = freshnessData?.sync_data?.last_ok || null;
            const lastAny = freshnessData?.sync_data?.last_any || null;
            const lastOkTs = lastOk?.finished_at || lastOk?.started_at || null;
            const lastAnyTs = lastAny?.finished_at || lastAny?.started_at || null;
            const lastError = compactError(lastAny?.error || '', 160);

            function ageLabel(ts) {
              if (!ts) return null;
              const dt = new Date(ts);
              if (!Number.isFinite(dt.getTime())) return null;
              const sec = Math.max(0, Math.floor((serverTime - dt) / 1000));
              const min = Math.floor(sec / 60);
              const hr = Math.floor(min / 60);
              const day = Math.floor(hr / 24);
              if (day > 0) return `${day}д ${hr % 24}ч`;
              if (hr > 0) return `${hr}ч ${min % 60}м`;
              if (min > 0) return `${min}м`;
              return `${sec}с`;
            }

            let text = 'Обновление: —';
            const age = ageLabel(lastOkTs);
            if (lastOkTs && age) {
              const dt = new Date(lastOkTs);
              text = `Обновление: ${dt.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })} • ${age} назад`;
            }
            if (lastAny && lastAny.status && String(lastAny.status).toLowerCase() !== 'ok' && lastAnyTs) {
              const anyAge = ageLabel(lastAnyTs);
              text = anyAge ? `Обновление: ⚠️ last ${String(lastAny.status).toUpperCase()} • ${anyAge} назад` : 'Обновление: ⚠️ last failed';
              if (lastError) text += ` • err ${lastError}`;
            }

            const max = freshnessData?.max || {};
            const titleLines = [];
            if (lastOkTs) titleLines.push(`sync_data ok: ${String(lastOkTs)} (${ageLabel(lastOkTs) || '—'} назад)`);
            if (lastAny && lastAnyTs) titleLines.push(`sync_data last: ${String(lastAny.status || '—')} at ${String(lastAnyTs)} (${ageLabel(lastAnyTs) || '—'} назад)`);
            if (lastError) titleLines.push(`sync_data error: ${lastError}`);
            const maxPairs = [
              ['fixtures_updated_at', 'fixtures'],
              ['odds_fetched_at', 'odds'],
              ['standings_updated_at', 'standings'],
              ['injuries_created_at', 'injuries'],
              ['match_indices_updated_at', 'indices'],
              ['predictions_created_at', 'predictions'],
              ['predictions_totals_created_at', 'totals'],
            ];
            maxPairs.forEach(([key, label]) => {
              const ts = max?.[key] || null;
              if (!ts) return;
              titleLines.push(`${label}: ${String(ts)} (${ageLabel(ts) || '—'} назад)`);
            });
            if (freshnessData?.config?.sync_data_cron) titleLines.push(`cron sync_data: ${String(freshnessData.config.sync_data_cron)}`);

            freshnessEl.textContent = text;
            freshnessEl.title = titleLines.join('\n');
          }

          await loadQualityReportData();

          const container = el('recent-bets');
          if (!container) return;
          if (recentBets.length === 0) {
            container.innerHTML = '<p class="text-muted">Нет завершенных ставок за период. Увеличьте период или используйте “Всё время”.</p>';
            renderBetsHistoryControls(0, recentTotal ?? totalBets);
            if (betsHistoryState.expanded) await loadBetsHistoryPage({ resetOffset: true });
            return;
          }
          const recentNote = `<div class="small text-muted mb-2">Период: последние ${days} дней.</div>`;

          container.innerHTML = `
            ${recentNote}
            <div class="activity-list">
              ${recentBets.map((bet) => {
                const homeName = String(bet.home || '').trim();
                const awayName = String(bet.away || '').trim();
                const matchDisplayRaw = homeName && awayName ? `${homeName} vs ${awayName}` : 'Unknown Match';
                const matchDisplay = escapeHtml(matchDisplayRaw);
                const dateDisplay = escapeHtml(new Date(bet.kickoff || bet.created_at).toLocaleDateString('ru-RU'));
                const statusRaw = String(bet.status || '—');
                const statusText = escapeHtml(statusRaw);
                const statusUi = statusToUi(statusRaw);
                const statusIcon = statusUi.icon;
                const statusColor = statusUi.badge;
                const profit = bet.profit === null || bet.profit === undefined ? null : Number(bet.profit);
                const pickText = escapeHtml(bet.pick ? String(bet.pick).replaceAll('_', ' ') : '—');
                const oddText = escapeHtml(bet.odd ?? '—');
                const leagueLabel = bet.league ? String(bet.league) : '';
                const leagueText = leagueLabel ? escapeHtml(leagueLabel) : '';
                const homeLogo = logoHtml(bet.home_logo_url, homeName, 'team', 'sm');
                const awayLogo = logoHtml(bet.away_logo_url, awayName, 'team', 'sm');
                const leagueLogo = logoHtml(bet.league_logo_url, leagueLabel, 'league', 'xs');
                const fid = bet.fixture_id === null || bet.fixture_id === undefined ? '' : String(bet.fixture_id);

                return `
                  <div class="activity-item d-flex justify-content-between align-items-center py-2 border-bottom cursor-pointer" data-action="fixture-details" data-fixture-id="${escapeHtml(fid)}" title="Открыть match details">
                    <div class="flex-grow-1">
                      <div class="activity-title-line" title="${matchDisplay}">
                        <span class="status-dot">${statusIcon}</span>
                        <div class="team-chip">
                          ${homeLogo}
                          <span class="team-name">${escapeHtml(homeName || '—')}</span>
                        </div>
                        <span class="vs">vs</span>
                        <div class="team-chip">
                          ${awayLogo}
                          <span class="team-name">${escapeHtml(awayName || '—')}</span>
                        </div>
                      </div>
                      <div class="activity-meta text-muted">
                        ${pickText} @${oddText} • ${dateDisplay}
                        ${leagueText ? `<span class="league-chip">${leagueLogo}<span class="league-name">${leagueText}</span></span>` : ''}
                      </div>
                    </div>
                    <div class="text-end">
                      <span class="badge bg-${statusColor} mb-1">${statusText}</span>
                      ${profit !== null
                        ? `<div class="text-${profit >= 0 ? 'success' : 'danger'} fw-bold">€${profit >= 0 ? '+' : ''}${profit.toFixed(2)}</div>`
                        : '<div class="text-muted small">Pending</div>'}
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          `;
          applyLogoFallbacks(container);

          renderBetsHistoryControls(recentBets.length, recentTotal ?? totalBets);
          if (betsHistoryState.expanded) await loadBetsHistoryPage();
        }

        function renderQualityTable(title, rows, columns) {
          if (!rows || rows.length === 0) {
            return `<div class="text-muted mt-2">${escapeHtml(title)}: нет данных</div>`;
          }
          return `
            <div class="table-responsive mt-2">
              <div class="small text-muted mb-1">${escapeHtml(title)}</div>
              <table class="table table-sm table-striped">
                <thead class="table-dark">
                  <tr>${columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join('')}</tr>
                </thead>
                <tbody>
                  ${rows.map((row) => `
                    <tr>
                      ${columns.map((col) => `<td>${escapeHtml(col.format(row))}</td>`).join('')}
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          `;
        }

        function renderQualityMarket(label, data, badgeClass) {
          const summary = data?.summary || {};
          const calib = data?.calibration || {};
          const bets = Number(summary.bets || 0);
          const hasBets = bets > 0;
          const clvCov = Number(summary.clv_cov || 0);
          const clvCovText = `${clvCov} (${formatPercent100(summary.clv_cov_pct, 1)})`;
          const clvAvgText = clvCov > 0 ? formatPercent100(summary.clv_avg_pct, 1) : '—';

          const byLeagueRaw = Array.isArray(data?.by_league) ? data.by_league : [];
          const byLeague = [...byLeagueRaw].sort((a, b) => Number(b?.bets || 0) - Number(a?.bets || 0)).slice(0, 8);
          const byOdds = Array.isArray(data?.by_odds_bucket) ? data.by_odds_bucket : [];
          const byTime = Array.isArray(data?.by_time_to_match) ? data.by_time_to_match : [];
          const bins = Array.isArray(calib?.bins) ? calib.bins : [];
          const shadowFilters = Array.isArray(data?.shadow_filters) ? data.shadow_filters : [];

          const leagueColumns = [
            {
              label: 'League',
              format: (row) => row?.league_name ? String(row.league_name) : `league ${row?.league_id ?? '—'}`,
            },
            { label: 'Bets', format: (row) => String(row?.bets ?? 0) },
            { label: 'ROI', format: (row) => formatPercent100(row?.roi, 1) },
            { label: 'Win', format: (row) => formatPercent100(row?.win_rate, 1) },
            { label: 'CLV', format: (row) => (Number(row?.clv_cov || 0) > 0 ? formatPercent100(row?.clv_avg_pct, 1) : '—') },
            { label: 'CLV cov', format: (row) => `${row?.clv_cov ?? 0} (${formatPercent100(row?.clv_cov_pct, 1)})` },
          ];

          const bucketColumns = [
            { label: 'Bucket', format: (row) => String(row?.key ?? '—') },
            { label: 'Bets', format: (row) => String(row?.bets ?? 0) },
            { label: 'ROI', format: (row) => formatPercent100(row?.roi, 1) },
            { label: 'Win', format: (row) => formatPercent100(row?.win_rate, 1) },
            { label: 'CLV', format: (row) => (Number(row?.clv_cov || 0) > 0 ? formatPercent100(row?.clv_avg_pct, 1) : '—') },
          ];

          const binColumns = [
            { label: 'Bin', format: (row) => String(row?.bin ?? '—') },
            { label: 'Bets', format: (row) => String(row?.bets ?? 0) },
            { label: 'Avg prob', format: (row) => formatPercent01(row?.avg_prob, 1) },
            { label: 'Win rate', format: (row) => formatPercent01(row?.win_rate, 1) },
          ];

          const shadowColumns = [
            { label: 'Scenario', format: (row) => String(row?.label || row?.id || '—') },
            { label: 'Bets', format: (row) => String(row?.summary?.bets ?? 0) },
            { label: 'ROI', format: (row) => formatPercent100(row?.summary?.roi, 1) },
            { label: 'CLV', format: (row) => (Number(row?.summary?.clv_cov || 0) > 0 ? formatPercent100(row?.summary?.clv_avg_pct, 1) : '—') },
            { label: 'CLV cov', format: (row) => `${row?.summary?.clv_cov ?? 0} (${formatPercent100(row?.summary?.clv_cov_pct, 1)})` },
            { label: 'ΔROI', format: (row) => formatSignedPercent100(row?.delta?.roi, 1) },
            { label: 'ΔCLV', format: (row) => formatSignedPercent100(row?.delta?.clv_avg_pct, 1) },
          ];

          const shadowBlock = shadowFilters.length
            ? renderQualityTable('Shadow filters (what-if)', shadowFilters, shadowColumns)
            : '';

          const detailsBlock = (byOdds.length || byTime.length || bins.length || shadowFilters.length) ? `
            <details class="mt-2">
              <summary class="small">Детали (odds/time/калибровка)</summary>
              ${renderQualityTable('Odds buckets', byOdds, bucketColumns)}
              ${renderQualityTable('Time to match', byTime, bucketColumns)}
              ${renderQualityTable('Calibration bins', bins, binColumns)}
              ${shadowBlock}
            </details>
          ` : '';

          return `
            <div class="border rounded p-3">
              <div class="d-flex justify-content-between align-items-center mb-1">
                <h4 class="mb-0">${escapeHtml(label)}</h4>
                <span class="badge bg-${escapeHtml(badgeClass)}">${bets} bets</span>
              </div>
              <div class="table-responsive">
                <table class="table table-sm table-striped">
                  <tbody>
                    <tr><td>ROI</td><td>${hasBets ? formatPercent100(summary.roi, 1) : '—'}</td></tr>
                    <tr><td>Win rate</td><td>${hasBets ? formatPercent100(summary.win_rate, 1) : '—'}</td></tr>
                    <tr><td>Avg odd</td><td>${hasBets ? formatFixed(summary.avg_odd, 2) : '—'}</td></tr>
                    <tr><td>CLV avg</td><td>${clvAvgText}</td></tr>
                    <tr><td>CLV coverage</td><td>${clvCovText}</td></tr>
                    <tr><td>Brier / LogLoss</td><td>${hasBets ? `${formatFixed(calib.brier, 3)} / ${formatFixed(calib.logloss, 3)}` : '—'}</td></tr>
                  </tbody>
                </table>
              </div>
              ${renderQualityTable('Лиги (top 8)', byLeague, leagueColumns)}
              ${detailsBlock}
            </div>
          `;
        }

        function hoursSince(ts) {
          if (!ts) return null;
          const dt = new Date(ts);
          if (!Number.isFinite(dt.getTime())) return null;
          const hours = (Date.now() - dt.getTime()) / (1000 * 60 * 60);
          return hours >= 0 ? hours : null;
        }

        function evaluateMarketQuality(label, data) {
          const summary = data?.summary || {};
          const calib = data?.calibration || {};
          const alerts = [];
          let level = 0;

          const bets = Number(summary.bets || 0);
          if (!bets) {
            alerts.push('нет ставок');
            level = Math.max(level, 1);
          } else if (bets < 50) {
            alerts.push(`малый объём (${bets})`);
            level = Math.max(level, 1);
          }

          const clvCov = Number(summary.clv_cov_pct || 0);
          const clvAvg = Number(summary.clv_avg_pct || 0);
          if (bets > 0 && clvCov === 0) {
            alerts.push('CLV coverage 0% (нет pre-kickoff снапшотов)');
            level = Math.max(level, 1);
          } else if (clvCov > 0 && clvCov < 30) {
            alerts.push(`CLV coverage низкий (${formatPercent100(clvCov, 1)})`);
            level = Math.max(level, clvCov < 10 ? 2 : 1);
          }
          if (clvCov >= 30 && Number.isFinite(clvAvg) && clvAvg < 0) {
            alerts.push(`CLV отрицательный (${formatPercent100(clvAvg, 1)})`);
            level = Math.max(level, 2);
          }

          if (bets >= 100) {
            const brier = Number(calib.brier ?? 0);
            const logloss = Number(calib.logloss ?? 0);
            if (Number.isFinite(brier)) {
              if (brier > 0.30) {
                alerts.push(`Brier высокий (${formatFixed(brier, 3)})`);
                level = Math.max(level, 2);
              } else if (brier > 0.27) {
                alerts.push(`Brier выше нормы (${formatFixed(brier, 3)})`);
                level = Math.max(level, 1);
              }
            }
            if (Number.isFinite(logloss)) {
              if (logloss > 0.85) {
                alerts.push(`LogLoss высокий (${formatFixed(logloss, 3)})`);
                level = Math.max(level, 2);
              } else if (logloss > 0.75) {
                alerts.push(`LogLoss выше нормы (${formatFixed(logloss, 3)})`);
                level = Math.max(level, 1);
              }
            }
          }

          if (bets >= 50) {
            const roi = Number(summary.roi ?? 0);
            if (Number.isFinite(roi) && roi < 0) {
              alerts.push(`ROI отрицательный (${formatPercent100(roi, 1)})`);
              level = Math.max(level, 1);
            }
          }

          return { label, level, alerts };
        }

        function renderQualitySignals(report, payload) {
          const staleAlerts = [];
          const ageHours = hoursSince(report?.generated_at);
          let staleLevel = 0;
          if (ageHours !== null && ageHours > 18) {
            staleLevel = ageHours > 30 ? 2 : 1;
            staleAlerts.push(`Отчёт качества устарел (${ageHours.toFixed(1)}ч)`);
          }

          const markets = [
            evaluateMarketQuality('1X2', report?.['1x2'] || {}),
            evaluateMarketQuality('TOTAL', report?.total || {}),
          ];

          const overallLevel = Math.max(staleLevel, ...markets.map((m) => m.level));
          const overallLabel = overallLevel === 2 ? 'Риск' : overallLevel === 1 ? 'Внимание' : 'OK';
          const overallCls = overallLevel === 2 ? 'alert-danger' : overallLevel === 1 ? 'alert-warning' : 'alert-success';

          const reasonParts = [];
          if (staleAlerts.length) reasonParts.push(staleAlerts.join(' • '));
          markets.forEach((m) => {
            if (m.level > 0 && m.alerts.length) {
              reasonParts.push(`${m.label}: ${m.alerts.join('; ')}`);
            }
          });
          const reasonText = reasonParts.length ? reasonParts.join(' • ') : 'метрики в норме';

          const marketBlocks = markets.map((m) => {
            const cls = m.level === 2 ? 'alert-danger' : m.level === 1 ? 'alert-warning' : 'alert-success';
            const msg = m.alerts.length ? m.alerts.join('; ') : 'метрики в норме';
            return `
              <div class="col-md-6">
                <div class="alert ${cls}">
                  <strong>${escapeHtml(m.label)}:</strong> ${escapeHtml(msg)}
                </div>
              </div>
            `;
          }).join('');

          return `
            <div class="alert ${overallCls}">
              <strong>Статус качества: ${escapeHtml(overallLabel)}</strong> — ${escapeHtml(reasonText)}
            </div>
            <div class="row">${marketBlocks}</div>
          `;
        }

        function renderQualityReport(payload) {
          const container = el('quality-report');
          const metaEl = el('quality-report-meta');
          if (!container) return;
          const report = payload?.report;
          if (!report || typeof report !== 'object') {
            container.innerHTML = '<p class="text-muted">Нет данных</p>';
            if (metaEl) metaEl.textContent = 'Обновление: —';
            return;
          }

          const cached = Boolean(payload?.cached);
          const metaParts = [];
          metaParts.push(`Обновлено: ${formatDateTime(report.generated_at)}`);
          if (report.bookmaker_id !== null && report.bookmaker_id !== undefined) {
            metaParts.push(`bookmaker ${String(report.bookmaker_id)}`);
          }
          if (payload?.cron) metaParts.push(`cron ${String(payload.cron)}`);
          if (payload?.cache_ttl_seconds) metaParts.push(`ttl ${Math.round(Number(payload.cache_ttl_seconds) / 3600)}h`);
          metaParts.push(cached ? 'кэш' : 'пересчитано');
          if (metaEl) metaEl.textContent = metaParts.join(' • ');

          const signals = renderQualitySignals(report, payload);
          const block1x2 = renderQualityMarket('1X2', report['1x2'] || {}, 'primary');
          const blockTotal = renderQualityMarket('TOTAL', report.total || {}, 'warning');
          container.innerHTML = `
            ${signals}
            <div class="row">
              <div class="col-md-6">${block1x2}</div>
              <div class="col-md-6">${blockTotal}</div>
            </div>
          `;
        }

        async function loadQualityReportData(forceRefresh = false) {
          const container = el('quality-report');
          const metaEl = el('quality-report-meta');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';
          if (metaEl) metaEl.textContent = 'Обновление: —';
          try {
            const path = forceRefresh ? '/api/v1/quality_report?refresh=1' : '/api/v1/quality_report';
            const data = await apiFetchJson(path);
            renderQualityReport(data);
          } catch (e) {
            handleApiError(e);
            if (container) container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка загрузки')}</div>`;
          }
        }

        function readLiveFiltersFromDom() {
          const marketEl = el('live-market');
          if (marketEl) liveState.market = marketEl.value || 'all';
          const searchEl = el('live-search');
          if (searchEl) liveState.league = (searchEl.value || '').trim();
          scheduleUiStateSave();
        }

        function resetLiveFilters() {
          liveState.market = 'all';
          liveState.league = '';
          scheduleUiStateSave();
        }

        async function loadLiveData() {
          const container = el('live-picks');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';

          const dateFrom = new Date().toISOString().split('T')[0];
          const dateTo = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
          const params = new URLSearchParams({
            date_from: dateFrom,
            date_to: dateTo,
            limit: '50',
            offset: '0',
            sort: 'kickoff_desc',
          });

          const [picks1x2, picksTotals] = await Promise.all([
            apiFetchJson(`/api/v1/picks?${params.toString()}`),
            apiFetchJson(`/api/v1/picks/totals?${params.toString()}`),
          ]);

          const merged = [
            ...(Array.isArray(picks1x2) ? picks1x2.map((p) => ({ ...p, market: '1X2' })) : []),
            ...(Array.isArray(picksTotals) ? picksTotals.map((p) => ({ ...p, market: 'TOTAL' })) : []),
          ];

          const marketFilter = String(liveState.market || 'all').toLowerCase();
          const needle = String(liveState.league || '').trim().toLowerCase();
          const filtered = merged.filter((p) => {
            if (marketFilter === '1x2' && p.market !== '1X2') return false;
            if (marketFilter === 'totals' && p.market !== 'TOTAL') return false;
            if (!needle) return true;
            const league = String(p.league || '').toLowerCase();
            const teams = String(p.teams || '').toLowerCase();
            return league.includes(needle) || teams.includes(needle);
          });

          const groups = new Map();
          for (const pick of filtered) {
            const fixtureId = pick?.fixture_id;
            if (fixtureId === null || fixtureId === undefined) continue;
            const key = String(fixtureId);
            const names = teamNamesFromPick(pick);
            const existing = groups.get(key) || {
              fixture_id: fixtureId,
              kickoff: pick.kickoff || null,
              teams: pick.teams || null,
              home: names.home || null,
              away: names.away || null,
              home_logo_url: pick.home_logo_url || null,
              away_logo_url: pick.away_logo_url || null,
              league: pick.league || null,
              league_logo_url: pick.league_logo_url || null,
              fixture_status: pick.fixture_status || null,
              score: pick.score || null,
              picks: [],
            };
            if (!existing.kickoff && pick.kickoff) existing.kickoff = pick.kickoff;
            if (!existing.teams && pick.teams) existing.teams = pick.teams;
            if ((!existing.home || !existing.away) && (names.home || names.away)) {
              if (!existing.home && names.home) existing.home = names.home;
              if (!existing.away && names.away) existing.away = names.away;
            }
            if (!existing.home_logo_url && pick.home_logo_url) existing.home_logo_url = pick.home_logo_url;
            if (!existing.away_logo_url && pick.away_logo_url) existing.away_logo_url = pick.away_logo_url;
            if (!existing.league && pick.league) existing.league = pick.league;
            if (!existing.league_logo_url && pick.league_logo_url) existing.league_logo_url = pick.league_logo_url;
            existing.picks.push(pick);
            groups.set(key, existing);
          }

          const fixtures = Array.from(groups.values()).sort(
            (a, b) => new Date(a.kickoff || 0).getTime() - new Date(b.kickoff || 0).getTime(),
          );
          const shownFixtures = fixtures.slice(0, 12);
          const uniqueMatches = fixtures.length;
          const picksCount = filtered.length;

          if (!container) return;

          container.innerHTML = `
            <div class="live-summary">
              <div class="live-summary-left">
                <div class="live-title">Upcoming Live Picks</div>
                <div class="live-subtitle">Следующие 7 дней • поток обновляется автоматически</div>
              </div>
              <div class="live-summary-right">
                <span class="badge bg-primary">${uniqueMatches} matches</span>
                <span class="badge bg-secondary">${picksCount} picks</span>
              </div>
            </div>

            <div class="live-filters">
              <div class="row">
                <div class="col-md-3">
                  <label class="form-label">Market</label>
                  <select id="live-market" class="form-select">
                    <option value="all">all</option>
                    <option value="1x2">1x2</option>
                    <option value="totals">totals</option>
                  </select>
                </div>
                <div class="col-md-6">
                  <label class="form-label">Search (league/teams)</label>
                  <input id="live-search" class="form-input" placeholder="e.g. Premier / Arsenal">
                </div>
                <div class="col-md-3">
                  <label class="form-label">&nbsp;</label>
                  <div class="btn-group">
                    <button type="button" class="btn-secondary btn-sm" data-action="live-apply">Apply</button>
                    <button type="button" class="btn-secondary btn-sm" data-action="live-reset">Reset</button>
                  </div>
                </div>
              </div>
            </div>

            ${shownFixtures.length === 0 ? '<p class="text-center text-muted">Нет предстоящих live picks</p>' : `
              <div class="picks-grid">
                ${shownFixtures.map((fixture) => {
                  const fallbackNames = splitTeamsText(fixture.teams);
                  const homeName = fixture.home || fallbackNames.home || 'Home';
                  const awayName = fixture.away || fallbackNames.away || 'Away';
                  const kickoffText = fixture.kickoff
                    ? escapeHtml(new Date(fixture.kickoff).toLocaleString('ru-RU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }))
                    : 'TBD';
                  const leagueText = fixture.league ? escapeHtml(fixture.league) : '—';
                  const statusText = fixture.fixture_status ? escapeHtml(String(fixture.fixture_status)) : '';
                  const scoreText = fixture.score ? escapeHtml(String(fixture.score)) : '';
                  const leagueLogo = logoHtml(fixture.league_logo_url, fixture.league, 'league', 'sm');
                  const homeLogo = logoHtml(fixture.home_logo_url, homeName, 'team', 'md');
                  const awayLogo = logoHtml(fixture.away_logo_url, awayName, 'team', 'md');

                  const picksSorted = (fixture.picks || []).slice().sort((a, b) => {
                    const oa = a.market === '1X2' ? 0 : 1;
                    const ob = b.market === '1X2' ? 0 : 1;
                    return oa - ob;
                  });

                  return `
                    <div class="fixture-card cursor-pointer" data-action="fixture-details" data-fixture-id="${escapeHtml(String(fixture.fixture_id))}" title="Открыть match details">
                      <div class="fixture-head">
                        <div class="fixture-league">
                          ${leagueLogo}
                          <span class="league-name">${leagueText}</span>
                        </div>
                        <div class="fixture-meta">
                          <span class="meta-pill">${kickoffText}</span>
                          ${statusText ? `<span class="meta-pill">${statusText}</span>` : ''}
                          ${scoreText ? `<span class="meta-pill meta-score">${scoreText}</span>` : ''}
                        </div>
                        <div class="fixture-actions">
                          <span class="pill pill-primary">${escapeHtml(String(picksSorted.length))} picks</span>
                          <button type="button" class="btn-secondary btn-sm" data-action="publish-open" data-fixture-id="${escapeHtml(String(fixture.fixture_id))}" title="Preview publish">📣</button>
                        </div>
                      </div>
                      <div class="fixture-teams">
                        <div class="team-chip">
                          ${homeLogo}
                          <span class="team-name">${escapeHtml(homeName)}</span>
                        </div>
                        <span class="vs">vs</span>
                        <div class="team-chip">
                          ${awayLogo}
                          <span class="team-name">${escapeHtml(awayName)}</span>
                        </div>
                      </div>
                      <div class="pick-lines">
                        ${picksSorted.map((pick) => {
                          const marketLabel = pick.market === 'TOTAL' ? 'TOTAL' : '1X2';
                          const marketBadge = pick.market === 'TOTAL' ? 'warning' : 'primary';
                          const pickLabel = escapeHtml(pick.pick ? String(pick.pick).replaceAll('_', ' ') : '—');
                          const odd = pick.odd === null || pick.odd === undefined ? null : Number(pick.odd);
                          const confidence = pick.confidence === null || pick.confidence === undefined ? null : Number(pick.confidence);
                          const ev = pick.ev === null || pick.ev === undefined ? null : Number(pick.ev);
                          const value = pick.value === null || pick.value === undefined ? null : Number(pick.value);
                          const signal = pick.signal_score === null || pick.signal_score === undefined ? null : Number(pick.signal_score);
                          const oddText = odd === null ? '—' : odd.toFixed(2);
                          const confText = confidence === null ? '—' : `${(confidence * 100).toFixed(1)}%`;
                          const evText = ev === null ? '—' : `${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%`;
                          const valueText = value === null ? null : value.toFixed(2);
                          const signalText = signal === null ? null : formatPercent01(signal, 1);
                          const metrics = [
                            `@${oddText}`,
                            `Conf ${confText}`,
                            `EV ${evText}`,
                          ];
                          if (valueText !== null) metrics.push(`Val ${valueText}`);
                          if (signalText !== null) metrics.push(`Sig ${signalText}`);

                          return `
                            <div class="pick-line">
                              <div class="pick-line-left">
                                <span class="badge bg-${marketBadge}">${escapeHtml(marketLabel)}</span>
                                <span class="fw-bold text-truncate table-cell-truncate" title="${pickLabel}">${pickLabel}</span>
                              </div>
                              <div class="pick-line-right">
                                <div class="metric-line">${escapeHtml(metrics.join(' • '))}</div>
                              </div>
                            </div>
                          `;
                        }).join('')}
                      </div>
                    </div>
                  `;
                }).join('')}
              </div>
            `}
          `;
          applyLogoFallbacks(container);

          const marketEl = el('live-market');
          if (marketEl) marketEl.value = liveState.market;
          const searchEl = el('live-search');
          if (searchEl) searchEl.value = liveState.league;
        }

        function ensureInfoDefaults() {
          if (!infoState.dateFrom || !infoState.dateTo) {
            const now = new Date();
            const dateTo = new Date(now);
            dateTo.setDate(now.getDate() + 7);
            const dateFrom = new Date(now);
            dateFrom.setDate(now.getDate() - 7);
            if (!infoState.dateFrom) infoState.dateFrom = toInputDate(dateFrom);
            if (!infoState.dateTo) infoState.dateTo = toInputDate(dateTo);
          }
          if (!Number.isFinite(Number(infoState.limit)) || Number(infoState.limit) < 1) infoState.limit = 80;
        }

        function applyInfoFiltersToDom() {
          const fromEl = el('info-date-from');
          const toEl = el('info-date-to');
          const searchEl = el('info-search');
          const upcomingEl = el('info-only-upcoming');
          const limitEl = el('info-limit');
          if (fromEl) fromEl.value = infoState.dateFrom || '';
          if (toEl) toEl.value = infoState.dateTo || '';
          if (searchEl) searchEl.value = infoState.search || '';
          if (upcomingEl) upcomingEl.checked = Boolean(infoState.onlyUpcoming);
          if (limitEl) limitEl.value = String(infoState.limit || 80);
        }

        function readInfoFiltersFromDom() {
          const fromEl = el('info-date-from');
          const toEl = el('info-date-to');
          const searchEl = el('info-search');
          const upcomingEl = el('info-only-upcoming');
          const limitEl = el('info-limit');
          if (fromEl && /^\d{4}-\d{2}-\d{2}$/.test(fromEl.value)) infoState.dateFrom = fromEl.value;
          if (toEl && /^\d{4}-\d{2}-\d{2}$/.test(toEl.value)) infoState.dateTo = toEl.value;
          if (searchEl) infoState.search = searchEl.value || '';
          if (upcomingEl) infoState.onlyUpcoming = Boolean(upcomingEl.checked);
          if (limitEl) infoState.limit = clampInt(limitEl.value, 1, 500, infoState.limit);
        }

        function resetInfoFilters() {
          const now = new Date();
          const dateTo = new Date(now);
          dateTo.setDate(now.getDate() + 7);
          const dateFrom = new Date(now);
          dateFrom.setDate(now.getDate() - 7);
          infoState.dateFrom = toInputDate(dateFrom);
          infoState.dateTo = toInputDate(dateTo);
          infoState.search = '';
          infoState.onlyUpcoming = false;
          infoState.limit = 80;
          applyInfoFiltersToDom();
        }

        const INFO_MARKETS = [
          { id: 'INFO_BTTS', label: 'BTTS', selections: ['BTTS_YES', 'BTTS_NO'] },
          { id: 'INFO_OU_1_5', label: 'O/U 1.5', selections: ['OVER_1_5', 'UNDER_1_5'] },
          { id: 'INFO_OU_2_5', label: 'O/U 2.5', selections: ['OVER_2_5', 'UNDER_2_5'] },
          { id: 'INFO_OU_3_5', label: 'O/U 3.5', selections: ['OVER_3_5', 'UNDER_3_5'] },
        ];

        function infoSelectionShort(sel) {
          const raw = String(sel || '');
          if (!raw) return '—';
          if (raw.startsWith('OVER_')) return `O${raw.replace('OVER_', '').replace('_', '.')}`;
          if (raw.startsWith('UNDER_')) return `U${raw.replace('UNDER_', '').replace('_', '.')}`;
          if (raw.startsWith('BTTS_')) return raw.replace('BTTS_', '');
          return raw.replaceAll('_', ' ');
        }

        function infoTier(prob) {
          if (!Number.isFinite(prob)) return { label: '—', cls: 'info-tier-muted', bar: 'info-bar-muted' };
          if (prob >= 0.66) return { label: 'strong', cls: 'info-tier-strong', bar: 'info-bar-strong' };
          if (prob >= 0.58) return { label: 'lean', cls: 'info-tier-lean', bar: 'info-bar-lean' };
          if (prob >= 0.53) return { label: 'edge', cls: 'info-tier-edge', bar: 'info-bar-edge' };
          return { label: 'close', cls: 'info-tier-close', bar: 'info-bar-close' };
        }

        function numOrNull(value) {
          const n = value === null || value === undefined ? null : Number(value);
          return n === null || !Number.isFinite(n) ? null : n;
        }

        function candidatesMap(decision) {
          const out = {};
          const candidates = Array.isArray(decision?.candidates) ? decision.candidates : [];
          candidates.forEach((c) => {
            const sel = c?.selection;
            if (!sel) return;
            out[String(sel)] = {
              prob: numOrNull(c?.prob),
              odd: numOrNull(c?.odd),
              ev: numOrNull(c?.ev),
            };
          });
          const sel = decision?.selection;
          if (sel && !out[String(sel)] && numOrNull(decision?.prob) !== null) {
            out[String(sel)] = {
              prob: numOrNull(decision?.prob),
              odd: numOrNull(decision?.odd),
              ev: numOrNull(decision?.ev),
            };
          }
          return out;
        }

        function bestSelectionByProb(map) {
          let bestSel = null;
          let bestProb = null;
          Object.entries(map || {}).forEach(([sel, val]) => {
            const prob = numOrNull(val?.prob);
            if (prob === null) return;
            if (bestProb === null || prob > bestProb) {
              bestProb = prob;
              bestSel = sel;
            }
          });
          return { sel: bestSel, prob: bestProb };
        }

        function selectionLabel(selection, home, away) {
          const raw = String(selection || '');
          if (!raw) return '—';
          if (raw === 'HOME_WIN') return home || 'Home';
          if (raw === 'AWAY_WIN') return away || 'Away';
          if (raw === 'DRAW') return 'Draw';
          if (raw.startsWith('OVER_') || raw.startsWith('UNDER_') || raw.startsWith('BTTS_')) return infoSelectionShort(raw);
          return raw.replaceAll('_', ' ');
        }

        function actionBadge(action) {
          const raw = String(action || '').toUpperCase();
          if (!raw) return '';
          const cls = raw === 'BET' ? 'bg-success' : raw === 'SKIP' ? 'bg-warning' : 'bg-secondary';
          return `<span class="badge ${cls}">${escapeHtml(raw)}</span>`;
        }

        function renderOutcomeCell(item, bestSel, showMeta) {
          const probText = item.prob === null ? '—' : `${(item.prob * 100).toFixed(1)}%`;
          const oddText = item.odd === null ? '' : `@${item.odd.toFixed(2)}`;
          const evText = item.ev === null ? '' : `${item.ev >= 0 ? '+' : ''}${(item.ev * 100).toFixed(1)}%`;
          const metaText = showMeta ? [oddText, evText].filter(Boolean).join(' ') || '—' : '';
          return `
            <div class="info-outcome${item.selection === bestSel ? ' is-best' : ''}">
              <div class="info-outcome-label">${escapeHtml(item.label)}</div>
              <div class="info-outcome-prob">${escapeHtml(probText)}</div>
              ${showMeta ? `<div class="info-outcome-meta">${escapeHtml(metaText)}</div>` : ''}
            </div>
          `;
        }

        function renderOutcomeGrid(items, bestSel, columns, showMeta) {
          const cols = columns || items.length || 1;
          return `
            <div class="info-outcome-grid info-outcome-grid-${cols}">
              ${items.map((item) => renderOutcomeCell(item, bestSel, showMeta)).join('')}
            </div>
          `;
        }

        function formatPickMeta(decision, home, away) {
          if (!decision || typeof decision !== 'object') return '—';
          const sel = decision.selection ? String(decision.selection) : '';
          if (!sel) return '—';
          const label = selectionLabel(sel, home, away);
          const prob = numOrNull(decision.prob);
          const odd = numOrNull(decision.odd);
          const ev = numOrNull(decision.ev);
          const parts = [`Pick: ${label}`];
          if (prob !== null) parts.push(formatPercent01(prob, 1));
          if (odd !== null) parts.push(`@${odd.toFixed(2)}`);
          if (ev !== null) parts.push(`EV ${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%`);
          const market = decision.market ? String(decision.market) : '';
          if (market.startsWith('INFO_')) parts.push('без odds');
          return parts.join(' • ');
        }

        function renderInfoMarketBlock(def, market) {
          const candidates = candidatesMap(market);
          const selections = Array.isArray(def.selections) ? def.selections : [];
          const items = selections.map((sel) => {
            const entry = candidates[sel] || {};
            return {
              selection: sel,
              label: infoSelectionShort(sel),
              prob: numOrNull(entry.prob),
              odd: null,
              ev: null,
            };
          });
          const best = bestSelectionByProb(candidates);
          if (!items.some((item) => item.prob !== null)) {
            return `
              <div class="info-market is-empty">
                <div class="info-market-head">
                  <span class="info-market-label">${escapeHtml(def.label)}</span>
                </div>
                <div class="info-market-main">—</div>
              </div>
            `;
          }
          const tier = infoTier(best.prob);
          return `
            <div class="info-market">
              <div class="info-market-head">
                <span class="info-market-label">${escapeHtml(def.label)}</span>
                <span class="info-tier ${tier.cls}">${escapeHtml(tier.label)}</span>
              </div>
              ${renderOutcomeGrid(items, best.sel, 2, false)}
              <div class="info-bar">
                <span class="info-bar-fill ${tier.bar}" style="width: ${Math.max(0, Math.min((best.prob || 0) * 100, 100)).toFixed(1)}%"></span>
              </div>
            </div>
          `;
        }

        function renderInfoPrimaryBlock(title, decision, items, bestSel, metaText) {
          const hasData = items.some((item) => item.prob !== null);
          if (!hasData) {
            return `
              <div class="info-block is-empty">
                <div class="info-block-head">
                  <span class="info-block-title">${escapeHtml(title)}</span>
                </div>
                <div class="info-block-empty">—</div>
              </div>
            `;
          }
          return `
            <div class="info-block">
              <div class="info-block-head">
                <span class="info-block-title">${escapeHtml(title)}</span>
                ${actionBadge(decision?.action)}
              </div>
              ${renderOutcomeGrid(items, bestSel, items.length, true)}
              <div class="info-block-meta">${escapeHtml(metaText)}</div>
            </div>
          `;
        }

        function applyInfoTab(tab) {
          const next = tab === 'stats' ? 'stats' : 'picks';
          infoState.tab = next;
          const picksPanel = el('info-picks-panel');
          const statsPanel = el('info-stats-panel');
          if (picksPanel) setHidden(picksPanel, next !== 'picks');
          if (statsPanel) setHidden(statsPanel, next !== 'stats');
          document.querySelectorAll('.info-tab').forEach((btn) => {
            const btnTab = btn?.dataset?.tab || 'picks';
            btn.classList.toggle('is-active', btnTab === next);
          });
        }

        function formatInfoSelection(sel, prob) {
          const raw = String(sel || '');
          let label = raw.replaceAll('_', ' ');
          if (raw.startsWith('OVER_') || raw.startsWith('UNDER_')) {
            const prefix = raw.startsWith('OVER_') ? 'O' : 'U';
            const threshold = raw.replace('OVER_', '').replace('UNDER_', '').replace('_', '.');
            label = `${prefix}${threshold}`;
          } else if (raw.startsWith('BTTS_')) {
            label = raw.replace('BTTS_', '');
          }
          const p = Number(prob);
          const pct = Number.isFinite(p) ? `${(p * 100).toFixed(1)}%` : '—';
          return `${label} ${pct}`;
        }

        function renderInfoPicks(rows) {
          const container = el('info-picks');
          if (!container) return;
          const list = Array.isArray(rows) ? rows : [];
          if (!list.length) {
            container.innerHTML = '<p class="text-center text-muted">Нет данных по info-вероятностям</p>';
            return;
          }
          container.innerHTML = `
            <div class="info-grid">
              ${list.map((row) => {
                const kickoffText = row.kickoff
                  ? new Date(row.kickoff).toLocaleString('ru-RU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                  : 'TBD';
                const leagueText = row.league ? String(row.league) : '—';
                const home = row.home || '';
                const away = row.away || '';
                const statusText = row.fixture_status ? String(row.fixture_status) : '';
                const scoreText = row.home_goals !== null && row.home_goals !== undefined && row.away_goals !== null && row.away_goals !== undefined
                  ? `${row.home_goals}-${row.away_goals}`
                  : '';
                const leagueLogo = logoHtml(row.league_logo_url, row.league, 'league', 'sm');
                const homeLogo = logoHtml(row.home_logo_url, home, 'team', 'sm');
                const awayLogo = logoHtml(row.away_logo_url, away, 'team', 'sm');
                const decisions = row.decisions && typeof row.decisions === 'object' ? row.decisions : {};
                const d1 = decisions['1X2'] || null;
                const dt = decisions['TOTAL'] || decisions['INFO_OU_2_5'] || null;
                const map1x2 = candidatesMap(d1);
                const mapTotal = candidatesMap(dt);
                const best1x2 = bestSelectionByProb(map1x2);
                const bestTotal = bestSelectionByProb(mapTotal);
                const items1x2 = [
                  {
                    selection: 'HOME_WIN',
                    label: home || 'Home',
                    prob: numOrNull(map1x2?.HOME_WIN?.prob),
                    odd: numOrNull(map1x2?.HOME_WIN?.odd),
                    ev: numOrNull(map1x2?.HOME_WIN?.ev),
                  },
                  {
                    selection: 'DRAW',
                    label: 'Draw',
                    prob: numOrNull(map1x2?.DRAW?.prob),
                    odd: numOrNull(map1x2?.DRAW?.odd),
                    ev: numOrNull(map1x2?.DRAW?.ev),
                  },
                  {
                    selection: 'AWAY_WIN',
                    label: away || 'Away',
                    prob: numOrNull(map1x2?.AWAY_WIN?.prob),
                    odd: numOrNull(map1x2?.AWAY_WIN?.odd),
                    ev: numOrNull(map1x2?.AWAY_WIN?.ev),
                  },
                ];
                const itemsTotal = [
                  {
                    selection: 'OVER_2_5',
                    label: infoSelectionShort('OVER_2_5'),
                    prob: numOrNull(mapTotal?.OVER_2_5?.prob),
                    odd: numOrNull(mapTotal?.OVER_2_5?.odd),
                    ev: numOrNull(mapTotal?.OVER_2_5?.ev),
                  },
                  {
                    selection: 'UNDER_2_5',
                    label: infoSelectionShort('UNDER_2_5'),
                    prob: numOrNull(mapTotal?.UNDER_2_5?.prob),
                    odd: numOrNull(mapTotal?.UNDER_2_5?.odd),
                    ev: numOrNull(mapTotal?.UNDER_2_5?.ev),
                  },
                ];
                const picksMeta1x2 = formatPickMeta(d1, home, away);
                const picksMetaTotal = formatPickMeta(dt, home, away);
                return `
                  <div class="fixture-card info-card cursor-pointer" data-action="fixture-details" data-fixture-id="${escapeHtml(String(row.fixture_id))}">
                    <div class="info-card-head">
                      <div class="fixture-league">
                        ${leagueLogo}
                        <span class="league-name">${escapeHtml(leagueText)}</span>
                      </div>
                      <div class="fixture-meta">
                        <span class="meta-pill">${escapeHtml(kickoffText)}</span>
                        ${statusText ? `<span class="meta-pill">${escapeHtml(statusText)}</span>` : ''}
                        ${scoreText ? `<span class="meta-pill meta-score">${escapeHtml(scoreText)}</span>` : ''}
                      </div>
                    </div>
                    <div class="fixture-teams">
                      <div class="team-chip">
                        ${homeLogo}
                        <span class="team-name">${escapeHtml(home)}</span>
                      </div>
                      <span class="vs">vs</span>
                      <div class="team-chip">
                        ${awayLogo}
                        <span class="team-name">${escapeHtml(away)}</span>
                      </div>
                    </div>
                    <div class="info-main-grid">
                      ${renderInfoPrimaryBlock('1X2', d1, items1x2, best1x2.sel, picksMeta1x2)}
                      ${renderInfoPrimaryBlock('Total 2.5', dt, itemsTotal, bestTotal.sel, picksMetaTotal)}
                    </div>
                    <div class="info-markets">
                      ${INFO_MARKETS.map((m) => renderInfoMarketBlock(m, decisions[m.id])).join('')}
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          `;
          applyLogoFallbacks(container);
        }

        function renderInfoStats(payload) {
          const container = el('info-stats');
          if (!container) return;
          const summary = Array.isArray(payload?.summary) ? payload.summary : [];
          if (!summary.length) {
            container.innerHTML = '<p class="text-center text-muted">Нет статистики для info-рынков</p>';
            return;
          }
          container.innerHTML = `
            <div class="table-responsive">
              <table class="table table-sm table-striped">
                <thead class="table-dark">
                  <tr>
                    <th>Рынок</th>
                    <th>Bets</th>
                    <th>Win rate</th>
                    <th>ROI@2.0</th>
                    <th>Brier</th>
                    <th>LogLoss</th>
                  </tr>
                </thead>
                <tbody>
                  ${summary.map((row) => {
                    const bets = Number(row?.bets ?? 0);
                    const winRate = Number(row?.win_rate ?? 0);
                    const roiEven = Number(row?.roi_even_pct ?? 0);
                    const brier = Number(row?.brier ?? 0);
                    const logloss = Number(row?.logloss ?? 0);
                    return `
                      <tr>
                        <td>${escapeHtml(row?.label || row?.market || '—')}</td>
                        <td>${bets}</td>
                        <td>${bets ? formatPercent01(winRate, 1) : '—'}</td>
                        <td>${bets ? formatSignedPercent100(roiEven, 1) : '—'}</td>
                        <td>${bets ? formatFixed(brier, 3) : '—'}</td>
                        <td>${bets ? formatFixed(logloss, 3) : '—'}</td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          `;
        }

        async function loadInfoData() {
          const picksEl = el('info-picks');
          const statsEl = el('info-stats');
          const metaEl = el('info-meta');
          if (picksEl) picksEl.innerHTML = '<p class="text-muted">Загрузка...</p>';
          if (statsEl) statsEl.innerHTML = '<p class="text-muted">Загрузка...</p>';

          ensureInfoDefaults();
          applyInfoFiltersToDom();

          const dateFrom = infoState.dateFrom;
          const dateTo = infoState.dateTo;
          const onlyUpcoming = Boolean(infoState.onlyUpcoming);
          infoState.limit = clampInt(infoState.limit, 1, 500, 80);

          const params = new URLSearchParams();
          if (dateFrom) params.set('date_from', `${dateFrom}T00:00:00`);
          if (dateTo) params.set('date_to', `${dateTo}T23:59:59`);
          params.set('limit', String(infoState.limit));
          params.set('offset', '0');
          params.set('only_upcoming', onlyUpcoming ? '1' : '0');

          const statsParams = new URLSearchParams();
          if (dateFrom) statsParams.set('date_from', `${dateFrom}T00:00:00`);
          if (dateTo) statsParams.set('date_to', `${dateTo}T23:59:59`);
          try {
            const [fixtures, stats] = await Promise.all([
              apiFetchJson(`/api/v1/info/fixtures?${params.toString()}`),
              apiFetchJson(`/api/v1/info/stats?${statsParams.toString()}`),
            ]);
            let rows = Array.isArray(fixtures) ? fixtures : [];
            const needle = String(infoState.search || '').trim().toLowerCase();
            if (needle) {
              rows = rows.filter((row) => {
                const league = String(row?.league || '').toLowerCase();
                const teams = `${row?.home || ''} ${row?.away || ''}`.toLowerCase();
                return league.includes(needle) || teams.includes(needle);
              });
            }
            renderInfoPicks(rows);
            renderInfoStats(stats);
            applyInfoTab(infoState.tab);
            if (metaEl) {
              const windowText = dateFrom && dateTo ? `${dateFrom} → ${dateTo}` : '—';
              const updatedAt = stats?.generated_at ? formatDateTime(stats.generated_at) : formatDateTime(new Date());
              const upcomingNote = onlyUpcoming ? ' • только upcoming' : '';
              metaEl.textContent = `Окно: ${windowText} • Матчей: ${rows.length}${upcomingNote} • Обновление: ${updatedAt}`;
            }
          } catch (e) {
            handleApiError(e);
            if (picksEl) picksEl.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка')}</div>`;
            if (statsEl) statsEl.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка')}</div>`;
          }
        }

        async function loadJobsData() {
          const container = el('jobs-content');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';

          const [statusData, runs] = await Promise.all([
            apiFetchJson('/api/v1/jobs/status'),
            apiFetchJson('/api/v1/jobs/runs?limit=10'),
          ]);

          const pipelineRow = statusData?.pipeline?.full || null;
          const jobs = statusData?.jobs || {};
          if (!container) return;

          container.innerHTML = `
            <div class="row mb-4">
              <div class="col-12">
                <div class="card">
                  <div class="card-header">
                    <h5 class="mb-0">🎛️ Pipeline Control</h5>
                  </div>
                  <div class="card-body">
                    <div class="job-controls">
                      <button type="button" class="btn btn-primary" data-action="run-job" data-job="sync_data">📥 Sync Data</button>
                      <button type="button" class="btn btn-info" data-action="run-job" data-job="compute_indices">📊 Compute Indices</button>
                      <button type="button" class="btn btn-warning" data-action="run-job" data-job="build_predictions">🔮 Build Predictions</button>
                      <button type="button" class="btn btn-success" data-action="run-job" data-job="evaluate_results">📈 Evaluate Results</button>
                      <button type="button" class="btn btn-danger" data-action="run-job" data-job="full">🚀 Full Pipeline</button>
                      <button type="button" class="btn btn-success" data-action="run-job" data-job="maintenance">🧹 Maintenance</button>
                    </div>
                    <div id="job-execution-log" class="mt-3"></div>
                  </div>
                </div>
              </div>
            </div>

            <div class="row">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header">
                    <h6 class="mb-0">⚡ Status</h6>
                  </div>
                  <div class="card-body">
                    <div class="job-status-grid">
                      ${[['full', pipelineRow], ...JOB_NAMES.map((n) => [n, jobs[n] || null])].map(([name, row]) => {
                        const label = formatStatusLabel(row?.status);
                        const nameText = escapeHtml(String(name).replaceAll('_', ' '));
                        return `
                          <div class="status-item">
                            <div class="status-label">${nameText}</div>
                            <div class="status-value ${label.cls}">${label.text}</div>
                          </div>
                        `;
                      }).join('')}
                    </div>
                  </div>
                </div>
              </div>

              <div class="col-md-6">
                <div class="card">
                  <div class="card-header">
                    <h6 class="mb-0">📋 Recent Runs</h6>
                  </div>
                  <div class="card-body">
                    ${Array.isArray(runs) && runs.length ? `
                      <div class="recent-jobs">
                        ${runs.slice(0, 7).map((job) => {
                          const jobName = escapeHtml(job.job_name || '—');
                          const statusText = escapeHtml(job.status || '—');
                          const startedAtRaw = job.started_at
                            ? new Date(job.started_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
                            : '—';

                          const meta = job && typeof job.meta === 'object' && job.meta ? job.meta : {};
                          const apiFootball = meta?.result?.api_football || meta?.stages?.sync_data?.result?.api_football || null;
                          const skipped = Boolean(meta?.result?.skipped || meta?.stages?.sync_data?.result?.skipped);
                          const skipReasonRaw = meta?.result?.skip_reason || meta?.stages?.sync_data?.result?.skip_reason || '';
                          const quotaExhausted = Boolean(
                            meta?.result?.quota_exhausted || meta?.stages?.sync_data?.result?.quota_exhausted
                          );
                          const apiInfo = apiFootball
                            ? (() => {
                              const misses = Number(apiFootball.cache_misses || 0);
                              const reqs = Number(apiFootball.requests || 0);
                              const errors = Number(apiFootball.errors || 0);
                              const errText = errors ? `, err ${errors}` : '';
                              return `API miss ${misses}/${reqs}${errText}`;
                            })()
                            : '';
                          const skipReason = String(skipReasonRaw || '').trim();
                          const skipInfo = skipped ? `SKIP${skipReason ? ` ${skipReason}` : ''}` : '';
                          const errorInfo = job?.status === 'failed'
                            ? compactError(job?.error || '', 140)
                            : '';
                          const parts = [startedAtRaw];
                          if (apiInfo) parts.push(apiInfo);
                          if (skipInfo) parts.push(skipInfo);
                          if (errorInfo) parts.push(`err ${errorInfo}`);
                          const startedAt = escapeHtml(parts.join(' • '));
                          return `
                            <div class="job-item">
                              <div class="job-name">${jobName}</div>
                              <div class="job-time ${quotaExhausted ? 'text-danger' : ''}">${startedAt}</div>
                              <div class="job-status ${jobRunStatusClass(job.status)}">${statusText}</div>
                            </div>
                          `;
                        }).join('')}
                      </div>
                    ` : '<p class="text-muted">No recent jobs</p>'}
                  </div>
                </div>
              </div>
            </div>
          `;
        }

        async function runJob(jobType) {
          const logDiv = el('job-execution-log');
          const jobLabel = String(jobType || '').replaceAll('_', ' ');
          if (logDiv) {
            logDiv.innerHTML = `
              <div class="alert alert-info">
                <strong>🚀 Triggering ${escapeHtml(jobLabel)}...</strong>
                <div class="spinner-border spinner-border-sm ms-2" role="status"></div>
              </div>
            `;
          }

          try {
            const res = await apiFetch(`/api/v1/run-now?job=${encodeURIComponent(jobType)}`, {
              method: 'POST',
              headers: { 'X-Admin-Actor': 'ui' },
            });

            const payload = res.ok ? await res.json() : null;
            if (!res.ok) throw new Error(`Run-now failed: ${res.status}`);

            const started = payload?.started || jobType;
            const skipped = Boolean(payload?.skipped);
            const startedLabel = String(started || '').replaceAll('_', ' ');

            if (logDiv) {
              logDiv.innerHTML = skipped
                ? `<div class="alert alert-warning"><strong>⏳ Already running: ${escapeHtml(startedLabel)}</strong></div>`
                : `<div class="alert alert-success"><strong>✅ Started: ${escapeHtml(startedLabel)}</strong></div>`;
            }

            notify(skipped ? `⏳ Already running: ${started}` : `✅ Started: ${started}`, skipped ? 'warning' : 'success');
            window.setTimeout(() => {
              void loadJobsData().catch(handleApiError);
              const current = document.querySelector('.section.active');
              if (current && current.id === 'system') void loadModelData().catch(handleApiError);
            }, 1000);
          } catch (e) {
            if (e && (e.message === 'AUTH_REQUIRED' || e.message === 'FORBIDDEN')) {
              handleApiError(e);
              return;
            }
            console.error(e);
            if (logDiv) {
              logDiv.innerHTML = `
                <div class="alert alert-danger">
                  <strong>❌ Failed to trigger job</strong>
                  <div class="mt-2"><small>${escapeHtml(e?.message || 'unknown error')}</small></div>
                </div>
              `;
            }
            notify('❌ Failed to trigger job', 'error');
          }
        }

        function applyDbTableSearch(searchValue) {
          const select = el('db-table');
          if (!select) return;
          const needle = String(searchValue || '').trim().toLowerCase();
          let firstVisible = null;
          for (const opt of Array.from(select.options)) {
            const visible = !needle || String(opt.value || '').toLowerCase().includes(needle);
            opt.hidden = !visible;
            if (visible && !firstVisible) firstVisible = opt.value;
          }
          const selected = select.selectedOptions && select.selectedOptions.length ? select.selectedOptions[0] : null;
          if (selected && selected.hidden && firstVisible) select.value = firstVisible;
        }

        async function loadDatabaseData() {
          const container = el('database-content');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';

          const debug = await apiFetchJson('/health/debug');
          const counts = debug?.counts || {};
          const uptime = debug?.uptime_seconds;
          const env = debug?.env || {};
          if (!container) return;

          const debugEnvJson = escapeHtml(JSON.stringify({ uptime_seconds: uptime, env }, null, 2));
          const tables = ['fixtures', 'odds', 'odds_snapshots', 'prediction_decisions', 'match_indices', 'predictions', 'prediction_publications', 'job_runs'];

          container.innerHTML = `
            <div class="card">
              <div class="card-header">
                <h5 class="mb-0">💾 Database Overview</h5>
              </div>
              <div class="card-body">
                <div class="db-stats-grid">
                  ${[
                    { label: 'fixtures', value: counts.fixtures, table: 'fixtures' },
                    { label: 'odds', value: counts.odds, table: 'odds' },
                    { label: 'indices', value: counts.indices, table: 'match_indices' },
                    { label: 'predictions', value: counts.predictions, table: 'predictions' },
                  ].map((c) => `
                    <div class="db-stat-item">
                      <div class="db-stat-value">${(c.value ?? 0).toLocaleString()}</div>
                      <div class="db-stat-label">${escapeHtml(c.label)}</div>
                      <button
                        type="button"
                        class="btn btn-sm btn-outline-primary mt-2"
                        data-action="db-browse"
                        data-table="${escapeHtml(c.table)}"
                        data-limit="20"
                      >👁️ Browse</button>
                    </div>
                  `).join('')}
                </div>

                <div class="mt-3">
                  <div class="row">
                    <div class="col-md-6">
                      <label class="form-label">Table</label>
                      <input id="db-table-search" class="form-input mb-1" placeholder="filter tables...">
                      <select id="db-table" class="form-select">
                        ${tables.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('')}
                      </select>
                    </div>
                    <div class="col-md-3">
                      <label class="form-label">Limit (max 200)</label>
                      <input id="db-limit" class="form-input" value="20" inputmode="numeric">
                    </div>
                    <div class="col-md-3">
                      <label class="form-label">Offset</label>
                      <input id="db-offset" class="form-input" value="0" inputmode="numeric">
                    </div>
                  </div>

                  <div class="row mt-2">
                    <div class="col-md-4">
                      <label class="form-label">fixture_id (optional)</label>
                      <input id="db-fixture-id" class="form-input" placeholder="e.g. 123">
                    </div>
                    <div class="col-md-4">
                      <label class="form-label">league_id (optional)</label>
                      <input id="db-league-id" class="form-input" placeholder="e.g. 39">
                    </div>
                    <div class="col-md-4">
                      <label class="form-label">status (optional)</label>
                      <input id="db-status" class="form-input" placeholder="e.g. NS / PENDING / ok">
                    </div>
                  </div>

                  <div class="mt-3">
                    <div class="btn-group">
                      <button type="button" class="btn btn-primary" data-action="db-browse-form">Browse</button>
                      <button type="button" class="btn-secondary btn-sm" data-action="db-prev">← Prev</button>
                      <button type="button" class="btn-secondary btn-sm" data-action="db-next">Next →</button>
                    </div>
                  </div>
                </div>

                <details class="mt-3">
                  <summary class="text-muted">Debug env</summary>
                  <pre class="bg-light p-3 border rounded pre-scroll">${debugEnvJson}</pre>
                </details>

                <div id="database-result" class="mt-3"></div>
              </div>
            </div>
          `;

          const tableEl = el('db-table');
          if (tableEl && dbBrowseState.table && Array.from(tableEl.options).some((o) => o.value === dbBrowseState.table)) {
            tableEl.value = dbBrowseState.table;
          }
          const limitEl = el('db-limit');
          if (limitEl) limitEl.value = String(dbBrowseState.limit);
          const offsetEl = el('db-offset');
          if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
          const fixtureEl = el('db-fixture-id');
          if (fixtureEl) fixtureEl.value = String(dbBrowseState.fixtureId || '');
          const leagueEl = el('db-league-id');
          if (leagueEl) leagueEl.value = String(dbBrowseState.leagueId || '');
          const statusEl = el('db-status');
          if (statusEl) statusEl.value = String(dbBrowseState.status || '');

          const searchEl = el('db-table-search');
          if (searchEl) {
            searchEl.value = String(dbBrowseState.tableSearch || '');
            applyDbTableSearch(searchEl.value);
            if (tableEl) dbBrowseState.table = tableEl.value;
            searchEl.addEventListener('input', () => {
              dbBrowseState.tableSearch = (searchEl.value || '').trim();
              applyDbTableSearch(searchEl.value);
              if (tableEl) dbBrowseState.table = tableEl.value;
              scheduleUiStateSave();
            });
          }
        }

        function formatFixed(value, digits) {
          const n = value === null || value === undefined ? null : Number(value);
          if (n === null || !Number.isFinite(n)) return '—';
          return n.toFixed(digits);
        }

        function formatPercent01(value, digits = 1) {
          const n = value === null || value === undefined ? null : Number(value);
          if (n === null || !Number.isFinite(n)) return '—';
          return `${(n * 100).toFixed(digits)}%`;
        }

        function formatPercent100(value, digits = 1) {
          const n = value === null || value === undefined ? null : Number(value);
          if (n === null || !Number.isFinite(n)) return '—';
          return `${n.toFixed(digits)}%`;
        }

        function formatSignedPercent100(value, digits = 1) {
          const n = value === null || value === undefined ? null : Number(value);
          if (n === null || !Number.isFinite(n)) return '—';
          const sign = n > 0 ? '+' : '';
          return `${sign}${n.toFixed(digits)}%`;
        }

        function renderModelStatus(data) {
          const cfg = data?.config || {};
          const apiFootball = data?.api_football || null;
          const elo = data?.elo || {};
          const leagues = Array.isArray(data?.leagues) ? data.leagues : [];

          const finished = Number(elo.finished_total || 0);
          const processed = Number(elo.processed_total || 0);
          const unprocessed = Number(elo.unprocessed_total || 0);
          const rebuildNeeded = Boolean(elo.rebuild_needed);
          const teamsWithElo = Number(elo.teams_with_elo || 0);
          const teamsInFixtures = Number(elo.teams_in_fixtures || 0);

          const lastProcessed = formatDateTime(elo.last_processed_at);
          const maxKickoff = formatDateTime(elo.max_processed_kickoff);
          const minUnprocessedKickoff = formatDateTime(elo.min_unprocessed_kickoff);

          const statusCls = unprocessed > 0 ? 'text-danger' : 'status-active';
          const rebuildCls = rebuildNeeded ? 'text-danger' : 'status-active';
          const cfgSeason = cfg?.season ? escapeHtml(String(cfg.season)) : '—';
          const cfgProb = cfg?.prob_source ? escapeHtml(String(cfg.prob_source)) : '—';
          const cfgLeagues = Array.isArray(cfg?.league_ids) ? escapeHtml(cfg.league_ids.join(', ')) : '—';

          const apiToday = apiFootball?.today || {};
          const apiLimit = Number(apiFootball?.daily_limit || 0);
          const apiResetAt = formatDateTime(apiFootball?.reset_at);
          const apiBlocked = Boolean(apiFootball?.blocked);
          const apiBlockedReason = apiFootball?.blocked_reason ? String(apiFootball.blocked_reason) : '';
          const apiMisses = Number(apiToday?.cache_misses || 0);
          const apiHits = Number(apiToday?.cache_hits || 0);
          const apiErrors = Number(apiToday?.errors || 0);
          const apiRunsOk = Number(apiToday?.ok_runs || 0);
          const apiRunsFailed = Number(apiToday?.failed_runs || 0);
          const apiRunBudget = Number(apiFootball?.run_budget_cache_misses || 0);
          const lastRun = apiFootball?.last_run || null;
          const lastRunWithCalls = apiFootball?.last_run_with_calls || null;
          const chosenRun = lastRunWithCalls || lastRun;
          const lastMetrics = chosenRun?.api_football && typeof chosenRun.api_football === 'object' ? chosenRun.api_football : null;
          const lastStarted = formatDateTime(chosenRun?.started_at);
          const lastStatus = chosenRun?.status ? String(chosenRun.status) : '—';
          const lastJob = chosenRun?.job_name ? String(chosenRun.job_name) : '—';
          const lastSkipped = Boolean(chosenRun?.skipped);
          const lastSkipReason = chosenRun?.skip_reason ? String(chosenRun.skip_reason) : '';
          const lastReq = lastMetrics ? Number(lastMetrics.requests || 0) : 0;
          const lastMiss = lastMetrics ? Number(lastMetrics.cache_misses || 0) : 0;
          const lastHit = lastMetrics ? Number(lastMetrics.cache_hits || 0) : 0;
          const lastErr = lastMetrics ? Number(lastMetrics.errors || 0) : 0;
          const lastBudget = lastMetrics && typeof lastMetrics.budget === 'object' ? lastMetrics.budget : null;
          const lastBudgetLimit = lastBudget ? Number(lastBudget.cache_misses_limit || 0) : 0;
          const lastBudgetUsed = lastBudget ? Number(lastBudget.cache_misses_used || 0) : 0;
          const lastBudgetText = lastBudgetLimit > 0 ? ` • budget ${lastBudgetUsed.toLocaleString()}/${lastBudgetLimit.toLocaleString()}` : '';

          const leagueNameById = new Map(
            leagues
              .filter((l) => l && l.league_id !== undefined && l.league_id !== null)
              .map((l) => [String(l.league_id), String(l.league_name || `league ${l.league_id}`)])
          );
          const byEndpoint = lastMetrics && typeof lastMetrics.by_endpoint === 'object' ? lastMetrics.by_endpoint : null;
          const byLeague = lastMetrics && typeof lastMetrics.by_league === 'object' ? lastMetrics.by_league : null;

          const warning = rebuildNeeded
            ? `
              <div class="alert alert-warning mb-3">
                <strong>⚠️ Elo needs rebuild</strong>
                <div class="small mt-1">min_unprocessed_kickoff < max_processed_kickoff → нажми “Rebuild Elo”</div>
              </div>
            `
            : '';
          const apiWarning = apiFootball && apiBlocked
            ? `
              <div class="alert alert-warning mb-3">
                <strong>⚠️ API‑Football quota guard</strong>
                <div class="small mt-1">sync_data будет пропускаться до reset (UTC).${apiBlockedReason ? ` reason: ${escapeHtml(apiBlockedReason)}` : ''}</div>
              </div>
            `
            : '';

          return `
            ${warning}
            ${apiWarning}
            <div class="job-status-grid mb-3">
              <div class="status-item">
                <div class="status-label">Elo processed</div>
                <div class="status-value ${statusCls}">${processed.toLocaleString()} / ${finished.toLocaleString()}</div>
              </div>
              <div class="status-item">
                <div class="status-label">Elo unprocessed</div>
                <div class="status-value ${unprocessed > 0 ? 'text-danger' : 'status-idle'}">${unprocessed.toLocaleString()}</div>
              </div>
              <div class="status-item">
                <div class="status-label">Teams (elo / fixtures)</div>
                <div class="status-value">${teamsWithElo.toLocaleString()} / ${teamsInFixtures.toLocaleString()}</div>
              </div>
              <div class="status-item">
                <div class="status-label">Rebuild needed</div>
                <div class="status-value ${rebuildCls}">${rebuildNeeded ? 'yes' : 'no'}</div>
              </div>
              ${apiFootball ? `
                <div class="status-item">
                  <div class="status-label">API cache_misses today</div>
                  <div class="status-value ${apiBlocked ? 'text-danger' : 'status-active'}">${apiMisses.toLocaleString()}${apiLimit ? ` / ${apiLimit.toLocaleString()}` : ''}</div>
                </div>
                ${apiRunBudget > 0 ? `
                  <div class="status-item">
                    <div class="status-label">API run budget</div>
                    <div class="status-value">${apiRunBudget.toLocaleString()} miss/run${lastBudgetLimit > 0 ? ` • last ${lastBudgetUsed.toLocaleString()}/${lastBudgetLimit.toLocaleString()}` : ''}</div>
                  </div>
                ` : ''}
              ` : ''}
            </div>

            <div class="small text-muted mb-3">
              Updated: ${escapeHtml(formatDateTime(data?.generated_at))} • season ${cfgSeason} • prob ${cfgProb} • leagues ${cfgLeagues}
              <br>
              Elo last_processed_at: ${escapeHtml(lastProcessed)} • max_processed_kickoff: ${escapeHtml(maxKickoff)} • min_unprocessed_kickoff: ${escapeHtml(minUnprocessedKickoff)}
              ${apiFootball ? `
                <br>
                API‑Football today (UTC): cache_misses ${apiMisses.toLocaleString()}${apiLimit ? ` / ${apiLimit.toLocaleString()}` : ''} • cache_hits ${apiHits.toLocaleString()} • errors ${apiErrors.toLocaleString()} • runs ok ${apiRunsOk.toLocaleString()}, failed ${apiRunsFailed.toLocaleString()} • reset ${escapeHtml(apiResetAt)}${apiRunBudget > 0 ? ` • budget/run ${apiRunBudget.toLocaleString()}` : ''}
                ${chosenRun ? `<br>Last API run: ${escapeHtml(`${lastJob}/${lastStatus}`)} • ${escapeHtml(lastStarted)}${lastSkipped ? ` • SKIP${lastSkipReason ? ` ${escapeHtml(lastSkipReason)}` : ''}` : ''} • miss ${lastMiss.toLocaleString()}/${lastReq.toLocaleString()} • hit ${lastHit.toLocaleString()} • err ${lastErr.toLocaleString()}${lastBudgetText}` : ''}
              ` : ''}
            </div>

            ${lastMetrics && (byEndpoint || byLeague) ? `
              <details class="mb-3">
                <summary class="text-muted">API breakdown (last run)</summary>
                ${byEndpoint && Object.keys(byEndpoint).length ? `
                  <div class="table-responsive mt-2">
                    <table class="table table-sm table-striped">
                      <thead class="table-dark">
                        <tr>
                          <th>Endpoint</th>
                          <th>Miss</th>
                          <th>Hit</th>
                          <th>Req</th>
                          <th>Err</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${Object.entries(byEndpoint)
                          .map(([ep, v]) => [ep, v && typeof v === 'object' ? v : {}])
                          .sort((a, b) => Number(b[1]?.cache_misses || 0) - Number(a[1]?.cache_misses || 0))
                          .map(([ep, v]) => `
                            <tr>
                              <td class="text-truncate table-cell-truncate" title="${escapeHtml(ep)}">${escapeHtml(ep)}</td>
                              <td>${Number(v.cache_misses || 0).toLocaleString()}</td>
                              <td>${Number(v.cache_hits || 0).toLocaleString()}</td>
                              <td>${Number(v.requests || 0).toLocaleString()}</td>
                              <td>${Number(v.errors || 0).toLocaleString()}</td>
                            </tr>
                          `).join('')}
                      </tbody>
                    </table>
                  </div>
                ` : '<div class="text-muted mt-2">No endpoint breakdown</div>'}

                ${byLeague && Object.keys(byLeague).length ? `
                  <div class="table-responsive mt-3">
                    <table class="table table-sm table-striped">
                      <thead class="table-dark">
                        <tr>
                          <th>League</th>
                          <th>Miss</th>
                          <th>Hit</th>
                          <th>Req</th>
                          <th>Err</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${Object.entries(byLeague)
                          .map(([lid, v]) => [lid, v && typeof v === 'object' ? v : {}])
                          .sort((a, b) => Number(b[1]?.cache_misses || 0) - Number(a[1]?.cache_misses || 0))
                          .map(([lid, v]) => {
                            const name = leagueNameById.get(String(lid)) || `league ${lid}`;
                            return `
                              <tr>
                                <td class="text-truncate table-cell-truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</td>
                                <td>${Number(v.cache_misses || 0).toLocaleString()}</td>
                                <td>${Number(v.cache_hits || 0).toLocaleString()}</td>
                                <td>${Number(v.requests || 0).toLocaleString()}</td>
                                <td>${Number(v.errors || 0).toLocaleString()}</td>
                              </tr>
                            `;
                          }).join('')}
                      </tbody>
                    </table>
                  </div>
                ` : '<div class="text-muted mt-2">No league breakdown</div>'}
              </details>
            ` : ''}

            ${leagues.length ? `
              <div class="table-responsive">
                <table class="table table-sm table-striped">
                  <thead class="table-dark">
                    <tr>
                      <th>League</th>
                      <th>Date</th>
                      <th>Draw</th>
                      <th>ρ</th>
                      <th>α</th>
                      <th>Finished</th>
                      <th>xG</th>
                      <th>Dec 1X2</th>
                      <th>Dec TOTAL</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${leagues.map((row) => {
                      const lid = row?.league_id ?? '';
                      const name = row?.league_name ? String(row.league_name) : `league ${lid}`;
                      const dateKey = row?.date_key ? String(row.date_key) : '—';
                      const finishedTotal = Number(row?.finished_total || 0);
                      const xgTotal = Number(row?.xg_total || 0);
                      const dec1 = Number(row?.decisions_1x2 || 0);
                      const decT = Number(row?.decisions_total || 0);
                      return `
                        <tr>
                          <td class="text-truncate table-cell-truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</td>
                          <td>${escapeHtml(dateKey)}</td>
                          <td>${escapeHtml(formatPercent01(row?.draw_freq, 1))}</td>
                          <td>${escapeHtml(formatFixed(row?.dc_rho, 4))}</td>
                          <td>${escapeHtml(formatFixed(row?.calib_alpha, 3))}</td>
                          <td>${finishedTotal.toLocaleString()}</td>
                          <td>${xgTotal.toLocaleString()}</td>
                          <td>${dec1.toLocaleString()}</td>
                          <td>${decT.toLocaleString()}</td>
                        </tr>
                      `;
                    }).join('')}
                  </tbody>
                </table>
              </div>
            ` : '<div class="text-muted">No league rows</div>'}
          `;
        }

        async function loadModelData() {
          const container = el('model-content');
          const updatedEl = el('model-updated');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';
          if (updatedEl) updatedEl.textContent = '—';

          const data = await apiFetchJson('/api/v1/model/status');
          if (updatedEl) updatedEl.textContent = `Updated: ${formatDateTime(data?.generated_at)}`;
          if (container) container.innerHTML = renderModelStatus(data);
        }

        function browseTableFromForm() {
          const { table, params } = syncDbBrowseStateFromDom();
          void browseTable(table, params);
        }

        function syncDbBrowseStateFromDom() {
          const table = el('db-table')?.value || 'fixtures';
          const limitEl = el('db-limit');
          const offsetEl = el('db-offset');
          const limit = clampInt(limitEl?.value ?? dbBrowseState.limit, 1, 200, dbBrowseState.limit);
          const offset = clampInt(offsetEl?.value ?? dbBrowseState.offset, 0, 1_000_000, dbBrowseState.offset);
          const fixtureId = (el('db-fixture-id')?.value || '').trim();
          const leagueId = (el('db-league-id')?.value || '').trim();
          const status = (el('db-status')?.value || '').trim();

          dbBrowseState.table = table;
          dbBrowseState.limit = limit;
          dbBrowseState.offset = offset;
          dbBrowseState.fixtureId = fixtureId;
          dbBrowseState.leagueId = leagueId;
          dbBrowseState.status = status;

          if (limitEl) limitEl.value = String(limit);
          if (offsetEl) offsetEl.value = String(offset);
          scheduleUiStateSave();

          const params = { limit, offset };
          if (fixtureId) params.fixture_id = fixtureId;
          if (leagueId) params.league_id = leagueId;
          if (status) params.status = status;

          return { table, params };
        }

        async function browseTable(tableName, params = {}) {
          const resultDiv = el('database-result');
          if (resultDiv) {
            resultDiv.innerHTML = '<div class="text-center"><div class="spinner-border"></div> Loading table data...</div>';
          }

          const sp = new URLSearchParams({ table: tableName });
          for (const [k, v] of Object.entries(params || {})) {
            if (v === undefined || v === null || String(v).trim() === '') continue;
            sp.set(k, String(v));
          }

          try {
            const data = await apiFetchJson(`/api/v1/db/browse?${sp.toString()}`);
            const rows = Array.isArray(data?.rows) ? data.rows : [];
            if (!resultDiv) return;

            if (rows.length === 0) {
              dbLastResult = { table: tableName, query: sp.toString(), rows: [] };
              resultDiv.innerHTML = `
                <div class="alert alert-info">
                  Нет данных
                  <div class="text-muted small">Проверь filters / уменьши offset</div>
                </div>
              `;
              return;
            }

            const columns = Object.keys(rows[0] || {});
            const safeTable = escapeHtml(tableName);
            dbLastResult = { table: tableName, query: sp.toString(), rows };

            resultDiv.innerHTML = `
              <div class="card">
                <div class="card-header">
                  <h6 class="mb-0">📊 ${safeTable} (${rows.length} rows)</h6>
                  <div class="btn-group">
                    <button type="button" class="btn-secondary btn-sm" data-action="db-copy-json">Copy JSON</button>
                  </div>
                </div>
                <div class="card-body">
                  <div class="table-responsive">
                    <table class="table table-sm table-striped">
                      <thead class="table-dark">
                        <tr>${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join('')}</tr>
                      </thead>
                      <tbody>
                        ${rows.map((row) => `
                          <tr>
                            ${columns.map((col) => {
                              const v = row[col];
                              const val = v === null || v === undefined ? '' : String(v);
                              return `<td class="text-truncate table-cell-truncate">${escapeHtml(val || '—')}</td>`;
                            }).join('')}
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            `;
          } catch (e) {
            handleApiError(e);
            dbLastResult = null;
            if (resultDiv) resultDiv.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'error')}</div>`;
          }
        }

        async function copyDbJson() {
          if (!dbLastResult || !Array.isArray(dbLastResult.rows)) {
            notify('Нет данных для копирования', 'warning');
            return;
          }
          const ok = await copyToClipboard(JSON.stringify(dbLastResult.rows, null, 2));
          if (ok) notify('📋 JSON скопирован', 'success');
          else notify('Не удалось скопировать', 'error');
        }

        async function loadSectionData(sectionId) {
          try {
            if (sectionId === 'dashboard') {
              await Promise.all([loadDashboardData(), loadLiveData()]);
            } else if (sectionId === 'info') {
              await Promise.all([loadInfoData()]);
            } else if (sectionId === 'system') {
              await Promise.all([loadJobsData(), loadDatabaseData(), loadModelData()]);
            }
            setConnectionStatus('Online', true);
            lastRefreshAt = new Date();
            renderPageMeta();
          } catch (e) {
            handleApiError(e);
          }
        }

        function toggleSidebar() {
          const sidebar = el('sidebar');
          if (sidebar) sidebar.classList.toggle('mobile-hidden');
        }

        function updatePageHeader(sectionId) {
          const titles = {
            dashboard: { title: 'Dashboard', subtitle: 'Метрики, live picks, недавние ставки' },
            info: { title: 'Info', subtitle: 'Полные вероятности по всем рынкам' },
            system: { title: 'Система', subtitle: 'Управление jobs и DB browser' },
          };
          const pageInfo = titles[sectionId] || titles.dashboard;
          const titleEl = el('page-title');
          const subtitleEl = el('page-subtitle');
          if (titleEl) titleEl.textContent = pageInfo.title;
          if (subtitleEl) subtitleEl.textContent = pageInfo.subtitle;
        }

        function showSection(sectionId) {
          document.querySelectorAll('.section').forEach((section) => section.classList.remove('active'));
          document.querySelectorAll('.nav-item').forEach((item) => item.classList.remove('active'));

          const targetSection = el(sectionId);
          if (targetSection) targetSection.classList.add('active');
          const navItem = document.querySelector(`.nav-item[data-section="${CSS.escape(sectionId)}"]`);
          if (navItem) navItem.classList.add('active');

          updatePageHeader(sectionId);
          void loadSectionData(sectionId);
          scheduleUiStateSave();
        }

        async function authenticateUser() {
          setAuthError('');
          const token = (el('admin-token')?.value || '').trim();
          if (!token) {
            setAuthError('Введите ADMIN_TOKEN');
            return;
          }

          setConnectionStatus('Checking…', true);
          try {
            const ok = await validateToken(token);
            if (!ok) {
              setConnectionStatus('Forbidden', false);
              setAuthError('Неверный ADMIN_TOKEN (403)');
              return;
            }

            tokenState = token;
            storeToken(token);
            showApp();
            initializeApp();
          } catch (e) {
            console.error(e);
            setConnectionStatus('Offline', false);
            setAuthError('Не удалось подключиться к API (см. консоль)');
          }
        }

        function initializeApp() {
          const { initialSection, openBetsHistory } = applyUiStateFromStorage(loadStoredUiState());
          const section = initialSection === 'system' ? 'system' : initialSection === 'info' ? 'info' : 'dashboard';
          showSection(section);
          void loadMeta();
          if (openBetsHistory && section === 'dashboard') {
            window.setTimeout(() => {
              const panel = el('bets-history-panel');
              if (panel && panel.classList.contains('is-hidden') && !betsHistoryState.expanded) void toggleBetsHistory();
            }, 0);
          }
          if (dashboardRefreshTimer) clearInterval(dashboardRefreshTimer);
          dashboardRefreshTimer = window.setInterval(() => {
            const current = document.querySelector('.section.active');
            if (current && (current.id === 'dashboard' || current.id === 'info')) {
              void loadSectionData(current.id);
            }
          }, 30000);
        }

        async function handleAction(actionEl) {
          const action = actionEl.dataset.action;

          if (action === 'toggle-sidebar') {
            toggleSidebar();
            return;
          }
          if (action === 'logout') {
            logout();
            return;
          }
          if (action === 'auth-submit') {
            await authenticateUser();
            return;
          }
          if (action === 'refresh-live') {
            await loadSectionData('dashboard');
            return;
          }
          if (action === 'info-tab') {
            const tab = actionEl.dataset.tab || 'picks';
            applyInfoTab(tab);
            scheduleUiStateSave();
            return;
          }
          if (action === 'info-apply') {
            readInfoFiltersFromDom();
            scheduleUiStateSave();
            await loadInfoData();
            return;
          }
          if (action === 'info-reset') {
            resetInfoFilters();
            scheduleUiStateSave();
            await loadInfoData();
            return;
          }
          if (action === 'refresh-info') {
            await loadInfoData();
            return;
          }
          if (action === 'publish-open') {
            const fid = actionEl.dataset.fixtureId;
            if (fid) await openFixtureModal(fid);
            return;
          }
          if (action === 'publish-refresh') {
            const fid = actionEl.dataset.fixtureId || fixtureModalState.fixtureId;
            if (fid) {
              await loadPublishPreview(fid);
              await loadPublishHistory(fid);
            }
            return;
          }
          if (action === 'publish-now') {
            const fid = actionEl.dataset.fixtureId || fixtureModalState.fixtureId;
            const force = actionEl.dataset.force === '1';
            if (fid) await publishNow(fid, force);
            return;
          }
          if (action === 'refresh-quality') {
            const refresh = actionEl.dataset.refresh === '1';
            await loadQualityReportData(refresh);
            return;
          }
          if (action === 'live-apply') {
            readLiveFiltersFromDom();
            await loadLiveData();
            return;
          }
          if (action === 'live-reset') {
            resetLiveFilters();
            await loadLiveData();
            return;
          }
          if (action === 'fixture-details') {
            const fid = actionEl.dataset.fixtureId;
            if (fid) await openFixtureModal(fid);
            return;
          }
          if (action === 'close-fixture-modal') {
            closeFixtureModal();
            return;
          }
          if (action === 'toggle-bets-history') {
            await toggleBetsHistory();
            return;
          }
          if (action === 'bets-open-all-time') {
            betsHistoryState.allTime = true;
            if (!betsHistoryState.expanded) {
              await toggleBetsHistory();
              return;
            }
            const allTimeEl = el('bets-all-time');
            if (allTimeEl) allTimeEl.checked = true;
            await loadBetsHistoryPage({ resetOffset: true });
            return;
          }
          if (action === 'bets-refresh') {
            await loadBetsHistoryPage();
            return;
          }
          if (action === 'bets-apply') {
            await loadBetsHistoryPage({ resetOffset: true });
            return;
          }
          if (action === 'bets-load-all') {
            await loadBetsHistoryAll({ maxRows: 5000 });
            return;
          }
          if (action === 'bets-export-csv') {
            await exportBetsHistoryCsv({ maxRows: 5000 });
            return;
          }
          if (action === 'bets-prev') {
            readBetsHistoryFiltersFromDom();
            betsHistoryState.offset = Math.max(0, betsHistoryState.offset - betsHistoryState.limit);
            await loadBetsHistoryPage();
            return;
          }
          if (action === 'bets-next') {
            readBetsHistoryFiltersFromDom();
            const total = betsHistoryState.total;
            if (total === null || betsHistoryState.offset + betsHistoryState.limit < total) {
              betsHistoryState.offset += betsHistoryState.limit;
            }
            await loadBetsHistoryPage();
            return;
          }
          if (action === 'refresh-jobs' || action === 'refresh-db') {
            await loadSectionData('system');
            return;
          }
          if (action === 'refresh-model') {
            await loadModelData();
            return;
          }
          if (action === 'run-job') {
            const job = actionEl.dataset.job;
            if (job) await runJob(job);
            return;
          }
          if (action === 'db-browse-form') {
            browseTableFromForm();
            return;
          }
          if (action === 'db-prev') {
            const { table, params } = syncDbBrowseStateFromDom();
            dbBrowseState.offset = Math.max(0, dbBrowseState.offset - dbBrowseState.limit);
            const offsetEl = el('db-offset');
            if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
            scheduleUiStateSave();
            await browseTable(table, { ...params, offset: dbBrowseState.offset });
            return;
          }
          if (action === 'db-next') {
            const { table, params } = syncDbBrowseStateFromDom();
            dbBrowseState.offset = dbBrowseState.offset + dbBrowseState.limit;
            const offsetEl = el('db-offset');
            if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
            scheduleUiStateSave();
            await browseTable(table, { ...params, offset: dbBrowseState.offset });
            return;
          }
          if (action === 'db-browse') {
            const table = actionEl.dataset.table || 'fixtures';
            const limit = parseInt(actionEl.dataset.limit || '20', 10);
            const params = { limit };
            const formSelect = el('db-table');
            if (formSelect) formSelect.value = table;
            const limitEl = el('db-limit');
            if (limitEl) limitEl.value = String(limit);
            const offsetEl = el('db-offset');
            if (offsetEl) offsetEl.value = '0';

            dbBrowseState.table = table;
            dbBrowseState.limit = clampInt(limit, 1, 200, dbBrowseState.limit);
            dbBrowseState.offset = 0;
            dbBrowseState.fixtureId = '';
            dbBrowseState.leagueId = '';
            dbBrowseState.status = '';
            scheduleUiStateSave();
            await browseTable(table, params);
          }
          if (action === 'db-copy-json') {
            await copyDbJson();
            return;
          }
        }

        document.addEventListener('click', (e) => {
          const navLink = e.target.closest('.nav-item[data-section]');
          if (navLink) {
            e.preventDefault();
            const sectionId = navLink.dataset.section;
            if (sectionId) showSection(sectionId);
            return;
          }

          const actionEl = e.target.closest('[data-action]');
          if (!actionEl) return;
          e.preventDefault();
          void handleAction(actionEl);
        });

        document.addEventListener('keydown', (e) => {
          if (e.key === 'Escape' && isFixtureModalOpen()) {
            e.preventDefault();
            closeFixtureModal();
            return;
          }
          if (e.key !== 'Enter') return;
          const target = e.target;
          if (!target || !target.id) return;
          if (target.id === 'live-search') {
            e.preventDefault();
            readLiveFiltersFromDom();
            void loadLiveData();
          }
          if (target.id === 'info-search') {
            e.preventDefault();
            readInfoFiltersFromDom();
            scheduleUiStateSave();
            void loadInfoData();
          }
        });

        document.addEventListener('DOMContentLoaded', async () => {
          const fixtureOverlay = el('fixture-modal');
          if (fixtureOverlay) {
            fixtureOverlay.addEventListener('click', (e) => {
              if (e.target === fixtureOverlay) closeFixtureModal();
            });
          }

          const tokenInput = el('admin-token');
          const stored = loadStoredToken();
          if (tokenInput) tokenInput.value = stored || '';
          if (tokenInput) {
            tokenInput.addEventListener('keydown', (e) => {
              if (e.key === 'Enter') void authenticateUser();
            });
          }

          const periodSelect = el('stats-period');
          if (periodSelect) {
            periodSelect.addEventListener('change', () => {
              scheduleUiStateSave();
              void loadSectionData('dashboard');
            });
          }

          if (stored) {
            setConnectionStatus('Checking…', true);
            try {
              const ok = await validateToken(stored);
              if (ok) {
                tokenState = stored;
                showApp();
                initializeApp();
                return;
              }
            } catch (e) {
              console.error(e);
            }
            clearStoredToken();
          }

          showAuth();
        });
      })();
    
