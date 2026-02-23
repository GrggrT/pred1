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
        const JOB_LABELS = {
          full: 'Полный пайплайн',
          sync_data: 'Синхронизация данных',
          compute_indices: 'Расчет индексов',
          build_predictions: 'Расчет прогнозов',
          evaluate_results: 'Оценка результатов',
          quality_report: 'Отчет качества',
          maintenance: 'Обслуживание',
          rebuild_elo: 'Пересборка Elo',
          snapshot_autofill: 'Автодозаполнение снапшотов',
        };

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
        let dbBrowseInFlight = false;
        const liveState = {
          market: 'all',
          league: '',
        };
        let livePartialFetchWarned = false;
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
          lastPageRows: 0,
        };
        const fixtureModalState = {
          fixtureId: null,
          cache: new Map(),
          returnFocusEl: null,
          requestSeq: 0,
        };
        let betsHistoryInFlight = false;
        let betsHistoryBusyNotifyAt = 0;
        const BETS_HISTORY_BUSY_NOTIFY_COOLDOWN_MS = 1200;
        let publishInFlight = false;
        let publishControlsPending = false;
        const publishPreviewState = {
          hasLoaded: false,
          readyMarkets: 0,
          totalMarkets: 0,
          reasons: [],
          error: '',
        };
        const publishPostPreviewState = {
          hasLoaded: false,
          error: '',
        };
        let publishLastResponse = null;
        const publishResultUiState = {
          issuesOnly: false,
        };
        const publishHistoryUiState = {
          fixtureId: null,
          rows: [],
          issuesOnly: false,
          limit: 25,
          loading: false,
          error: '',
        };
        const publishUiState = {
          dryRun: false,
          imageTheme: 'pro',
        };
        const publishStateHintState = {
          element: null,
          text: '',
          tone: 'info',
        };
        const publishHistoryLiveState = {
          text: '',
          tone: 'info',
        };
        const publishBusyNotifyState = {
          key: '',
          at: 0,
        };
        const PUBLISH_BUSY_NOTIFY_COOLDOWN_MS = 1200;
        let publishHistoryLoadingNotifyAt = 0;
        const PUBLISH_HISTORY_LOADING_NOTIFY_COOLDOWN_MS = 1000;
        const PUBLISH_HISTORY_LIMIT_OPTIONS = [10, 25, 50, 100];
        let wasMobileViewport = null;

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

        function isTextInputLike(target) {
          if (!(target instanceof HTMLElement)) return false;
          const tag = target.tagName;
          if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
          if (tag !== 'INPUT') return false;
          const type = String(target.getAttribute('type') || 'text').toLowerCase();
          return !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(type);
        }

        function isElementTabbable(node) {
          if (!(node instanceof HTMLElement)) return false;
          if (node.hidden) return false;
          if (node.getAttribute('aria-hidden') === 'true') return false;
          if (node.closest('[aria-hidden="true"]')) return false;
          const style = window.getComputedStyle(node);
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          return node.offsetParent !== null || style.position === 'fixed';
        }

        function setHidden(element, hidden) {
          if (!element) return;
          element.classList.toggle('is-hidden', hidden);
          element.setAttribute('aria-hidden', hidden ? 'true' : 'false');
        }

        function alertA11yByClass(alertEl) {
          if (alertEl.closest('#quality-report') || alertEl.closest('#job-execution-log')) {
            return { role: 'status', live: 'polite' };
          }
          const isCritical = alertEl.classList.contains('alert-danger') || alertEl.classList.contains('alert-warning');
          return isCritical ? { role: 'alert', live: 'assertive' } : { role: 'status', live: 'polite' };
        }

        function applyAlertA11y(alertEl) {
          if (!(alertEl instanceof HTMLElement) || !alertEl.classList.contains('alert')) return;
          const a11y = alertA11yByClass(alertEl);
          if (!alertEl.hasAttribute('role')) alertEl.setAttribute('role', a11y.role);
          if (!alertEl.hasAttribute('aria-live')) alertEl.setAttribute('aria-live', a11y.live);
          if (!alertEl.hasAttribute('aria-atomic')) alertEl.setAttribute('aria-atomic', 'true');
        }

        function applyAlertsA11y(scope) {
          const root = scope || document;
          if (!(root instanceof Document) && !(root instanceof Element)) return;
          root.querySelectorAll('.alert').forEach((node) => applyAlertA11y(node));
        }

        function initAlertsA11yObserver() {
          if (!window.MutationObserver || !document.body) return;
          const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
              mutation.addedNodes.forEach((node) => {
                if (!(node instanceof Element)) return;
                if (node.classList.contains('alert')) applyAlertA11y(node);
                node.querySelectorAll('.alert').forEach((alertNode) => applyAlertA11y(alertNode));
              });
            });
          });
          observer.observe(document.body, { childList: true, subtree: true });
        }

        const NOTIFICATION_MAX_VISIBLE = 4;
        const NOTIFICATION_TIMEOUT_MS = 4500;

        function getNotificationRegion() {
          let region = el('notification-region');
          if (region) return region;
          region = document.createElement('div');
          region.id = 'notification-region';
          region.className = 'notification-region';
          region.setAttribute('aria-label', 'Уведомления');
          document.body.appendChild(region);
          return region;
        }

        function notificationA11yForType(typeRaw) {
          const type = String(typeRaw || 'info').toLowerCase();
          if (type === 'error' || type === 'warning') return { role: 'alert', live: 'assertive' };
          return { role: 'status', live: 'polite' };
        }

        function showNotification(message, type = 'info') {
          const normalizedType = ['info', 'success', 'warning', 'error'].includes(String(type || '').toLowerCase())
            ? String(type || '').toLowerCase()
            : 'info';
          const notification = document.createElement('div');
          notification.className = `notification notification-${normalizedType}`;
          const a11y = notificationA11yForType(normalizedType);
          notification.setAttribute('role', a11y.role);
          notification.setAttribute('aria-live', a11y.live);
          notification.setAttribute('aria-atomic', 'true');
          notification.innerHTML = `
            <div class="notification-content">
              <span class="notification-message"></span>
              <button type="button" class="notification-close" aria-label="Закрыть уведомление" title="Закрыть уведомление">×</button>
            </div>
          `;
          const msgEl = notification.querySelector('.notification-message');
          if (msgEl) msgEl.textContent = String(message);
          const closeEl = notification.querySelector('.notification-close');
          const removeNotification = () => {
            if (removeTimer) {
              window.clearTimeout(removeTimer);
              removeTimer = null;
            }
            notification.remove();
          };
          const scheduleRemove = (delay = NOTIFICATION_TIMEOUT_MS) => {
            if (removeTimer) window.clearTimeout(removeTimer);
            removeTimer = window.setTimeout(removeNotification, delay);
          };
          let removeTimer = null;

          if (closeEl) closeEl.addEventListener('click', removeNotification);
          notification.addEventListener('mouseenter', () => {
            if (removeTimer) window.clearTimeout(removeTimer);
          });
          notification.addEventListener('mouseleave', () => scheduleRemove(2200));
          notification.addEventListener('focusin', () => {
            if (removeTimer) window.clearTimeout(removeTimer);
          });
          notification.addEventListener('focusout', () => scheduleRemove(2200));

          const region = getNotificationRegion();
          region.appendChild(notification);
          while (region.childElementCount > NOTIFICATION_MAX_VISIBLE) {
            const stale = region.firstElementChild;
            if (!stale || stale === notification) break;
            stale.remove();
          }
          scheduleRemove();
        }

        function notify(message, type = 'info') {
          try {
            showNotification(message, type);
          } catch (e) {
            console.log(message);
          }
        }

        function setAuthError(message, options = {}) {
          const { focus = false } = options;
          const box = el('auth-error');
          const tokenInput = el('admin-token');
          if (!box) return;
          if (!message) {
            box.textContent = '';
            setHidden(box, true);
            if (tokenInput) tokenInput.setAttribute('aria-invalid', 'false');
            return;
          }
          box.textContent = String(message);
          setHidden(box, false);
          if (tokenInput) tokenInput.setAttribute('aria-invalid', 'true');
          if (focus && typeof box.focus === 'function') box.focus();
        }

        function setAuthPending(pending) {
          const submitBtn = document.querySelector('[data-action="auth-submit"]');
          if (submitBtn instanceof HTMLButtonElement) {
            submitBtn.disabled = pending;
            submitBtn.setAttribute('aria-busy', pending ? 'true' : 'false');
            submitBtn.textContent = pending ? 'Проверка…' : 'Войти';
          }
          const tokenInput = el('admin-token');
          if (tokenInput) tokenInput.disabled = pending;
        }

        function setActionButtonPending(buttonEl, pending, busyText = 'Выполняется…') {
          if (!(buttonEl instanceof HTMLButtonElement)) return;
          if (pending) {
            if (!buttonEl.dataset.pendingLabel) buttonEl.dataset.pendingLabel = buttonEl.textContent || '';
            buttonEl.disabled = true;
            buttonEl.setAttribute('aria-busy', 'true');
            buttonEl.setAttribute('aria-disabled', 'true');
            buttonEl.textContent = busyText;
            return;
          }
          buttonEl.disabled = false;
          buttonEl.removeAttribute('aria-busy');
          buttonEl.removeAttribute('aria-disabled');
          if (buttonEl.dataset.pendingLabel !== undefined) {
            buttonEl.textContent = buttonEl.dataset.pendingLabel;
            delete buttonEl.dataset.pendingLabel;
          }
        }

        function setRunJobButtonsPending(pending, activeButton = null) {
          document.querySelectorAll('[data-action="run-job"]').forEach((node) => {
            if (!(node instanceof HTMLButtonElement)) return;
            if (node === activeButton) return;
            node.disabled = pending;
            if (pending) node.setAttribute('aria-busy', 'true');
            else node.removeAttribute('aria-busy');
          });
          setActionButtonPending(activeButton, pending, 'Запуск…');
        }

        function updateDbBrowsePagerAvailability() {
          const hasRows = Array.isArray(dbLastResult?.rows);
          const rowCount = hasRows ? dbLastResult.rows.length : 0;

          const prevButton = document.querySelector('[data-action="db-prev"]');
          if (prevButton instanceof HTMLButtonElement) {
            const prevDisabled = dbBrowseInFlight || dbBrowseState.offset <= 0;
            prevButton.disabled = prevDisabled;
            if (prevDisabled) prevButton.setAttribute('aria-disabled', 'true');
            else prevButton.removeAttribute('aria-disabled');
          }

          const nextButton = document.querySelector('[data-action="db-next"]');
          if (nextButton instanceof HTMLButtonElement) {
            const noFurtherRowsLikely = hasRows && rowCount < dbBrowseState.limit;
            const nextDisabled = dbBrowseInFlight || !hasRows || noFurtherRowsLikely;
            nextButton.disabled = nextDisabled;
            if (nextDisabled) nextButton.setAttribute('aria-disabled', 'true');
            else nextButton.removeAttribute('aria-disabled');
          }

          const hintEl = el('db-page-hint');
          if (hintEl) {
            if (dbBrowseInFlight) {
              hintEl.textContent = 'Загрузка данных таблицы…';
              return;
            }
            if (!hasRows) {
              hintEl.textContent = `Смещение: ${dbBrowseState.offset} • Лимит: ${dbBrowseState.limit}`;
              return;
            }
            const tail = rowCount < dbBrowseState.limit ? ' • вероятно конец выборки' : '';
            hintEl.textContent = `Смещение: ${dbBrowseState.offset} • Лимит: ${dbBrowseState.limit} • Строк: ${rowCount}${tail}`;
          }
        }

        function setDbBrowseControlsPending(pending, activeButton = null) {
          ['db-browse', 'db-browse-form', 'db-prev', 'db-next'].forEach((action) => {
            document.querySelectorAll(`[data-action="${action}"]`).forEach((node) => {
              if (!(node instanceof HTMLButtonElement)) return;
              if (node === activeButton) return;
              node.disabled = pending;
              if (pending) node.setAttribute('aria-busy', 'true');
              else node.removeAttribute('aria-busy');
            });
          });
          setActionButtonPending(activeButton, pending, 'Загрузка…');

          ['db-table-search', 'db-table', 'db-limit', 'db-offset', 'db-fixture-id', 'db-league-id', 'db-status'].forEach((id) => {
            const input = el(id);
            if (input instanceof HTMLInputElement || input instanceof HTMLSelectElement) input.disabled = pending;
          });

          const resultDiv = el('database-result');
          if (resultDiv) {
            if (pending) resultDiv.setAttribute('aria-busy', 'true');
            else resultDiv.removeAttribute('aria-busy');
          }
          updateDbBrowsePagerAvailability();
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

        function formatFixtureStatusLabel(statusRaw, minuteRaw) {
          const status = String(statusRaw || '').toUpperCase();
          const minuteNum = Number.isFinite(Number(minuteRaw)) ? Number(minuteRaw) : null;
          const liveSet = new Set(['LIVE', '1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT']);
          const finalSet = new Set(['FT', 'AET', 'PEN']);
          const canceledSet = new Set(['CANC', 'ABD', 'AWD', 'WO']);

          if (!status) return { label: '', isLive: false };
          if (canceledSet.has(status)) return { label: status, isLive: false };
          if (finalSet.has(status)) return { label: status, isLive: false };
          if (liveSet.has(status)) {
            if (minuteNum !== null && minuteNum >= 0) return { label: `LIVE ${Math.floor(minuteNum)}'`, isLive: true };
            return { label: 'LIVE', isLive: true };
          }
          return { label: status, isLive: false };
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
            if (metaState.uiMtime) parts.push(`UI изм. ${formatDateTime(metaState.uiMtime)}`);
            if (metaState.appStartedAt) parts.push(`Запуск ${formatDateTime(metaState.appStartedAt)}`);
            if (metaState.pythonVersion) parts.push(`Py ${metaState.pythonVersion}`);
            if (metaState.pid) parts.push(`PID ${metaState.pid}`);

            if (sha) titleParts.push(`UI sha256: ${sha}`);
            if (metaState.uiMtime) titleParts.push(`UI изменен: ${metaState.uiMtime}`);
            if (metaState.appStartedAt) titleParts.push(`Запуск: ${metaState.appStartedAt}`);
            if (metaState.pythonVersion) titleParts.push(`Python: ${metaState.pythonVersion}`);
            if (metaState.pid) titleParts.push(`PID: ${metaState.pid}`);
          }

          if (lastRefreshAt) {
            parts.push(`Обновлено ${formatTime(lastRefreshAt)}`);
            titleParts.push(`Последнее обновление: ${lastRefreshAt.toISOString()}`);
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
          const safeText = String(text || '—');
          badge.textContent = safeText;
          badge.className = isOk ? 'nav-badge' : 'nav-badge status-offline';
          badge.setAttribute('aria-label', `Статус соединения: ${safeText}`);
          badge.setAttribute('title', `Статус соединения: ${safeText}`);
        }

        function setSectionBusy(sectionId, busy) {
          const section = el(sectionId);
          if (!section) return;
          section.setAttribute('aria-busy', busy ? 'true' : 'false');
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
            publish: {
              dryRun: Boolean(publishUiState.dryRun),
              imageTheme: publishUiState.imageTheme,
              historyLimit: publishHistoryUiState.limit,
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

          const publish = state.publish || {};
          if (publish.dryRun !== undefined) publishUiState.dryRun = Boolean(publish.dryRun);
          if (typeof publish.imageTheme === 'string') {
            publishUiState.imageTheme = normalizePublishImageTheme(publish.imageTheme);
          }
          if (publish.historyLimit !== undefined) {
            publishHistoryUiState.limit = normalizePublishHistoryLimit(publish.historyLimit);
          }

          return { initialSection, openBetsHistory };
        }

        function getToken() {
          return (tokenState || loadStoredToken() || '').trim();
        }

        async function validateToken(token) {
          const res = await fetch('/health/debug', { headers: { 'X-Admin-Token': token } });
          if (res.ok) return true;
          if (res.status === 403) return false;
          throw new Error(`health/debug недоступен: ${res.status}`);
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
            throw new Error(`Ошибка запроса: ${res.status}${details ? ` ${details}` : ''}`);
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
            throw new Error(`Ошибка запроса: ${res.status}${details ? ` ${details}` : ''}`);
          }
          const data = await res.json();
          return { data, totalCount: Number.isFinite(totalCount) ? totalCount : null };
        }

        function showAuth() {
          setHidden(el('auth-container'), false);
          setHidden(el('main-app'), true);
          setConnectionStatus('Требуется вход', false);
          setAuthPending(false);
          const tokenInput = el('admin-token');
          if (tokenInput) tokenInput.focus();
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

        function isAuthApiError(error) {
          return Boolean(error && (error.message === 'AUTH_REQUIRED' || error.message === 'FORBIDDEN'));
        }

        function handleScopedApiError(error, options = {}) {
          const { showGenericNotify = true, updateConnection = true } = options;
          console.error(error);
          if (isAuthApiError(error)) {
            notify('🔒 Требуется ADMIN_TOKEN', 'warning');
            logout();
            return true;
          }
          if (updateConnection) setConnectionStatus('Ошибка', false);
          if (showGenericNotify) notify('❌ Ошибка загрузки данных', 'error');
          return false;
        }

        function handleApiError(error) {
          handleScopedApiError(error);
        }

        function translateRunStatus(statusRaw) {
          const s = String(statusRaw || '').toLowerCase();
          if (!s) return '—';
          if (s === 'running') return 'в работе';
          if (s === 'ok' || s === 'completed') return 'готово';
          if (s === 'failed') return 'ошибка';
          if (s === 'skipped') return 'пропуск';
          if (s === 'queued') return 'в очереди';
          return s;
        }

        function formatJobLabel(jobRaw) {
          const raw = String(jobRaw || '').trim();
          if (!raw) return '—';
          return JOB_LABELS[raw] || raw.replaceAll('_', ' ');
        }

        function formatStatusLabel(status) {
          const s = String(status || '').toLowerCase();
          if (s === 'running') return { text: '🟡 в работе', cls: 'status-active' };
          if (s === 'ok' || s === 'completed') return { text: '🟢 готово', cls: 'status-active' };
          if (s === 'failed') return { text: '🔴 ошибка', cls: 'text-danger' };
          if (s === 'skipped') return { text: '⚪ пропуск', cls: 'status-idle' };
          if (s === 'queued') return { text: '⚪ в очереди', cls: 'status-idle' };
          return { text: '⚪ ожидание', cls: 'status-idle' };
        }

        function jobRunStatusClass(status) {
          const s = String(status || '').toLowerCase();
          if (s === 'running') return 'job-status-running';
          if (s === 'ok' || s === 'completed') return 'job-status-completed';
          if (s === 'failed') return 'job-status-failed';
          if (s === 'skipped' || s === 'queued') return 'job-status-idle';
          return 'job-status-idle';
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

        function translateBetStatus(statusRaw) {
          const status = String(statusRaw || '').toUpperCase();
          if (status === 'WIN') return 'Победа';
          if (status === 'LOSS') return 'Поражение';
          if (status === 'VOID') return 'Возврат';
          if (status === 'PENDING') return 'Ожидает';
          if (!status) return '—';
          return status;
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

        function getFixtureModalFocusable() {
          const overlay = el('fixture-modal');
          if (!overlay || overlay.classList.contains('is-hidden')) return [];
          const selector = [
            'button:not([disabled])',
            '[href]',
            'input:not([disabled])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            '[tabindex]:not([tabindex="-1"])',
          ].join(', ');
          return Array.from(overlay.querySelectorAll(selector)).filter((node) => isElementTabbable(node));
        }

        function focusFixtureModalPrimaryControl() {
          const overlay = el('fixture-modal');
          if (!overlay || overlay.classList.contains('is-hidden')) return;
          const closeBtn = overlay.querySelector('[data-action="close-fixture-modal"]');
          if (closeBtn instanceof HTMLElement) {
            try {
              closeBtn.focus();
            } catch (e) {
              // ignore
            }
          }
        }

        function trapFixtureModalFocus(event) {
          if (!isFixtureModalOpen() || event.key !== 'Tab') return;
          const focusables = getFixtureModalFocusable();
          if (!focusables.length) {
            event.preventDefault();
            return;
          }
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          const current = document.activeElement;
          if (event.shiftKey) {
            if (current === first || !focusables.includes(current)) {
              event.preventDefault();
              last.focus();
            }
            return;
          }
          if (current === last || !focusables.includes(current)) {
            event.preventDefault();
            first.focus();
          }
        }

        function closeFixtureModal() {
          fixtureModalState.requestSeq += 1;
          fixtureModalState.fixtureId = null;
          resetPublishModalState();
          setPublishResultState('Результат: —');
          const overlay = el('fixture-modal');
          if (overlay) setHidden(overlay, true);
          const bodyEl = el('fixture-modal-body');
          if (bodyEl) bodyEl.setAttribute('aria-busy', 'false');
          document.body.classList.remove('modal-open');
          const returnFocusEl = fixtureModalState.returnFocusEl;
          fixtureModalState.returnFocusEl = null;
          if (returnFocusEl instanceof HTMLElement && document.contains(returnFocusEl)) {
            try {
              returnFocusEl.focus();
            } catch (e) {
              // ignore
            }
            return;
          }
          const contentRoot = el('main-content-root');
          if (contentRoot instanceof HTMLElement) {
            try {
              contentRoot.focus();
            } catch (e) {
              // ignore
            }
          }
        }

        function renderDecisionBlock(decision, marketKey) {
          if (!decision || typeof decision !== 'object') return `<p class="text-muted">Нет данных решения (${escapeHtml(String(marketKey))})</p>`;
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
            if (r === 'ev_below_threshold_or_out_of_range') return 'EV ниже порога или коэффициент вне диапазона';
            if (r === 'no_candidate_in_range') return 'Нет кандидатов в диапазоне коэффициентов';
            if (r === 'no_odds') return 'Нет коэффициентов';
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
                      <th>Выбор</th>
                      <th class="text-end">Вер.</th>
                      <th class="text-end">Коэфф.</th>
                      <th class="text-end">EV</th>
                      ${hasInRange ? '<th>В диапазоне</th>' : ''}
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
                          ${hasInRange ? `<td>${inRange === null ? '—' : (inRange ? 'да' : 'нет')}</td>` : ''}
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

          const statusText = translateBetStatus(status);
          const statusView = statusText === status ? status : `${statusText} (${status})`;
          const items = [
            ['Статус', statusView],
            ['Прибыль', pred.profit === null || pred.profit === undefined ? '—' : formatEuro(pred.profit)],
            ['Вероятность', conf === null ? '—' : formatPercent01(conf, 1)],
            ['Имплайд', implied === null ? '—' : formatPercent01(implied, 1)],
            ['Коэфф.', odd === null ? '—' : odd.toFixed(2)],
            ['EV', ev === null ? '—' : `${(ev * 100).toFixed(1)}%`],
            ['Brier', brier === null ? '—' : brier.toFixed(3)],
            ['LogLoss', logloss === null ? '—' : logloss.toFixed(3)],
          ];

          return `
            <div class="card mt-3">
              <div class="card-title mb-0">${escapeHtml(label)} — Пост‑матч</div>
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

        function normalizePublishImageTheme(rawValue) {
          const raw = String(rawValue || '').trim().toLowerCase();
          return raw === 'viral' ? 'viral' : 'pro';
        }

        function getPublishDryRun() {
          const checkbox = el('publish-dry-run');
          if (checkbox) return Boolean(checkbox.checked);
          return Boolean(publishUiState.dryRun);
        }

        function getPublishImageTheme() {
          const select = el('publish-image-theme');
          if (select) return normalizePublishImageTheme(select.value);
          return normalizePublishImageTheme(publishUiState.imageTheme);
        }

        function resolvePublishFixtureId(actionEl = null) {
          const fromAction = actionEl && actionEl.dataset ? String(actionEl.dataset.fixtureId || '').trim() : '';
          if (fromAction) return fromAction;
          const fromPreview = el('publish-preview')?.dataset ? String(el('publish-preview').dataset.fixtureId || '').trim() : '';
          if (fromPreview) return fromPreview;
          const fromModalState = String(fixtureModalState.fixtureId || '').trim();
          if (fromModalState) return fromModalState;
          return '';
        }

        function parseFixtureIdOrNull(rawValue) {
          const n = Number.parseInt(String(rawValue || '').trim(), 10);
          if (!Number.isFinite(n) || n <= 0) return null;
          return n;
        }

        function publishFixtureLabel(fixtureIdRaw) {
          const fixtureId = parseFixtureIdOrNull(fixtureIdRaw);
          return fixtureId === null ? 'Матч' : `Матч ${fixtureId}`;
        }

        function normalizeRequestSeqOrNull(rawValue) {
          const n = Number.parseInt(String(rawValue ?? '').trim(), 10);
          if (!Number.isFinite(n) || n <= 0) return null;
          return n;
        }

        function normalizePublishHistoryLimit(rawValue) {
          const n = Number.parseInt(String(rawValue ?? '').trim(), 10);
          if (!Number.isFinite(n)) return publishHistoryUiState.limit;
          if (PUBLISH_HISTORY_LIMIT_OPTIONS.includes(n)) return n;
          return publishHistoryUiState.limit;
        }

        function isFixtureModalContextCurrent(options = {}) {
          const overlay = el('fixture-modal');
          if (!overlay || overlay.classList.contains('is-hidden')) return false;
          const expectedSeq = normalizeRequestSeqOrNull(options?.requestSeq);
          if (expectedSeq !== null && fixtureModalState.requestSeq !== expectedSeq) return false;
          const expectedFixtureId = parseFixtureIdOrNull(options?.fixtureId);
          if (expectedFixtureId !== null) {
            const activeFixtureId = parseFixtureIdOrNull(fixtureModalState.fixtureId);
            if (activeFixtureId === null || activeFixtureId !== expectedFixtureId) return false;
          }
          return true;
        }

        function isPublishControlsBusy() {
          return publishInFlight || publishControlsPending;
        }

        function notifyPublishBusyContext() {
          let key = '';
          let message = '';
          if (publishInFlight) {
            key = 'publish';
            message = 'Дождитесь завершения текущей публикации';
          } else if (publishControlsPending) {
            key = 'refresh';
            message = 'Дождитесь завершения текущего обновления publish-данных';
          }
          if (!key || !message) return;
          const now = Date.now();
          if (publishBusyNotifyState.key === key && now - publishBusyNotifyState.at < PUBLISH_BUSY_NOTIFY_COOLDOWN_MS) {
            return;
          }
          publishBusyNotifyState.key = key;
          publishBusyNotifyState.at = now;
          notify(message, 'warning');
        }

        function notifyPublishHistoryLoading() {
          const now = Date.now();
          if (now - publishHistoryLoadingNotifyAt < PUBLISH_HISTORY_LOADING_NOTIFY_COOLDOWN_MS) return;
          publishHistoryLoadingNotifyAt = now;
          notify('История публикаций уже обновляется', 'warning');
        }

        function normalizePublishHintTone(toneRaw) {
          const tone = String(toneRaw || 'info').toLowerCase();
          if (tone === 'error' || tone === 'warning' || tone === 'success') return tone;
          return 'info';
        }

        function setPublishStateHint(message, tone = 'info') {
          const hintEl = el('publish-state-hint');
          if (!hintEl) return;
          const text = String(message || '').trim();
          const normalizedTone = text ? normalizePublishHintTone(tone) : 'info';
          const cls = normalizedTone === 'error'
            ? 'text-danger'
            : normalizedTone === 'warning'
              ? 'text-warning'
              : normalizedTone === 'success'
                ? 'text-success'
                : 'text-muted';
          hintEl.className = `small mt-2 ${cls}`;
          const isSameTarget = publishStateHintState.element === hintEl;
          const isSameText = publishStateHintState.text === text;
          const isSameTone = publishStateHintState.tone === normalizedTone;
          if (isSameTarget && isSameText && isSameTone) return;
          hintEl.textContent = text;
          applyPublishLiveA11y(hintEl, normalizedTone);
          publishStateHintState.element = hintEl;
          publishStateHintState.text = text;
          publishStateHintState.tone = normalizedTone;
        }

        function resetPublishPreviewState() {
          publishPreviewState.hasLoaded = false;
          publishPreviewState.readyMarkets = 0;
          publishPreviewState.totalMarkets = 0;
          publishPreviewState.reasons = [];
          publishPreviewState.error = '';
        }

        function resetPublishPostPreviewState() {
          publishPostPreviewState.hasLoaded = false;
          publishPostPreviewState.error = '';
        }

        function resetPublishHistoryState() {
          publishHistoryUiState.fixtureId = null;
          publishHistoryUiState.rows = [];
          publishHistoryUiState.issuesOnly = false;
          publishHistoryUiState.loading = false;
          publishHistoryUiState.error = '';
        }

        function resetPublishTransientState() {
          publishStateHintState.element = null;
          publishStateHintState.text = '';
          publishStateHintState.tone = 'info';
          publishHistoryLiveState.text = '';
          publishHistoryLiveState.tone = 'info';
          publishBusyNotifyState.key = '';
          publishBusyNotifyState.at = 0;
          publishHistoryLoadingNotifyAt = 0;
          publishInFlight = false;
          publishControlsPending = false;
        }

        function resetPublishModalState() {
          resetPublishPreviewState();
          resetPublishPostPreviewState();
          resetPublishHistoryState();
          resetPublishTransientState();
        }

        function updatePublishPreviewStateFromData(data) {
          const markets = Array.isArray(data?.markets) ? data.markets : [];
          const summary = summarizePublishPreviewMarkets(markets);
          const reasonsSet = new Set();
          markets.forEach((market) => {
            const headlineRaw = String(market?.headline_raw || market?.headline || '').trim();
            const analysisRaw = String(market?.analysis_raw || market?.analysis || '').trim();
            if (headlineRaw && analysisRaw) return;
            const reasons = Array.isArray(market?.reasons) ? market.reasons : [];
            reasons.forEach((reason) => {
              const text = String(reason || '').trim();
              if (text) reasonsSet.add(text);
            });
          });
          publishPreviewState.hasLoaded = true;
          publishPreviewState.readyMarkets = summary.ready;
          publishPreviewState.totalMarkets = summary.total;
          publishPreviewState.reasons = Array.from(reasonsSet).slice(0, 3);
          publishPreviewState.error = '';
        }

        function applyPublishActionAvailability() {
          const body = el('fixture-modal-body');
          if (body) {
            body.querySelectorAll('[data-action="publish-now"]').forEach((node) => {
              if (!(node instanceof HTMLButtonElement)) return;
              let shouldDisable = false;
              if (isPublishControlsBusy()) shouldDisable = true;
              else if (!publishPreviewState.hasLoaded) shouldDisable = true;
              else shouldDisable = publishPreviewState.readyMarkets <= 0;
              node.disabled = shouldDisable;
              if (shouldDisable) node.setAttribute('aria-disabled', 'true');
              else node.removeAttribute('aria-disabled');
            });
          }

          if (publishInFlight) {
            setPublishStateHint('Идёт отправка публикации…', 'info');
            return;
          }
          if (publishControlsPending) {
            setPublishStateHint('Идёт обновление publish-данных…', 'info');
            return;
          }
          if (!publishPreviewState.hasLoaded) {
            setPublishStateHint('Проверка готовности публикации…', 'info');
            return;
          }
          if (publishPreviewState.error) {
            setPublishStateHint(`Публикация недоступна: ${publishPreviewState.error}`, 'error');
            return;
          }
          if (publishPreviewState.readyMarkets > 0) {
            const total = publishPreviewState.totalMarkets;
            const suffix = total > 0 ? ` из ${total}` : '';
            setPublishStateHint(`Готово к отправке: ${publishPreviewState.readyMarkets}${suffix}`, 'success');
            return;
          }
          const reason = publishPreviewState.reasons.length
            ? ` Причина: ${translatePublishReason(publishPreviewState.reasons[0])}`
            : '';
          setPublishStateHint(`Нет готовых рынков для отправки.${reason}`, 'warning');
        }

        function updatePublishResultActionsAvailability() {
          const box = el('publish-result');
          if (!box) return;
          box.querySelectorAll('[data-action="publish-copy-result"]').forEach((node) => {
            if (!(node instanceof HTMLButtonElement)) return;
            const disabled = isPublishControlsBusy() || !publishLastResponse;
            node.disabled = disabled;
            if (disabled) node.setAttribute('aria-disabled', 'true');
            else node.removeAttribute('aria-disabled');
          });
          box.querySelectorAll('[data-action="publish-toggle-issues"]').forEach((node) => {
            if (!(node instanceof HTMLButtonElement)) return;
            const hasResult = Boolean(publishLastResponse && Array.isArray(publishLastResponse.results));
            const hasIssues = hasResult && publishLastResponse.results.some((row) => isPublishIssueStatus(row?.status));
            const disabled = isPublishControlsBusy() || !hasResult || (!hasIssues && !publishResultUiState.issuesOnly);
            node.disabled = disabled;
            node.setAttribute('aria-pressed', publishResultUiState.issuesOnly ? 'true' : 'false');
            if (disabled) node.setAttribute('aria-disabled', 'true');
            else node.removeAttribute('aria-disabled');
          });
        }

        function translatePublishReason(reasonRaw) {
          const raw = String(reasonRaw || '').trim();
          if (!raw) return 'без причины';
          const reason = raw.toLowerCase();
          if (reason === 'already_published') return 'уже опубликовано';
          if (reason === 'quality_risk') return 'риск качества';
          if (reason === 'no_data') return 'нет данных';
          if (reason === 'no_pred') return 'нет прогноза';
          if (reason === 'send_failed') return 'ошибка отправки';
          if (reason === 'publish_locked') return 'публикация уже выполняется';
          if (reason === 'idempotent_duplicate') return 'дубликат (idempotency)';
          if (reason === 'html_render_failed') return 'ошибка рендера HTML-картинки';
          if (reason === 'html_renderer_unavailable') return 'HTML-рендерер недоступен';
          if (reason === 'reason_no_report' || reason === 'no quality report') return 'нет отчёта качества';
          if (reason === 'reason_no_summary' || reason === 'no quality summary') return 'нет сводки качества';
          if (reason === 'reason_clv_zero') return 'CLV coverage 0%';
          if (reason.startsWith('reason_low_sample')) {
            const sampleMatch = raw.match(/(\d+)/);
            return sampleMatch ? `малый объём выборки (${sampleMatch[1]})` : 'малый объём выборки';
          }
          if (reason.startsWith('reason_clv_low')) {
            const clvMatch = raw.match(/(\d+(?:[.,]\d+)?)\s*%?/);
            return clvMatch ? `CLV coverage низкий (${clvMatch[1]}%)` : 'CLV coverage низкий';
          }
          if (reason.startsWith('reason_brier')) {
            const metricMatch = raw.match(/(\d+(?:[.,]\d+)?)/);
            return metricMatch ? `Brier ${metricMatch[1]}` : 'Brier';
          }
          if (reason.startsWith('reason_logloss')) {
            const metricMatch = raw.match(/(\d+(?:[.,]\d+)?)/);
            return metricMatch ? `LogLoss ${metricMatch[1]}` : 'LogLoss';
          }
          if (/^brier\s+\d/i.test(raw)) return raw.replace(/^brier/i, 'Brier');
          if (/^logloss\s+\d/i.test(raw)) return raw.replace(/^logloss/i, 'LogLoss');
          return raw;
        }

        function translatePublishReasonsList(reasonsRaw, fallback = 'нет данных') {
          if (!Array.isArray(reasonsRaw) || !reasonsRaw.length) return fallback;
          const translated = [];
          reasonsRaw.forEach((item) => {
            const text = String(item || '').trim();
            if (!text) return;
            const translatedText = translatePublishReason(text);
            if (!translatedText) return;
            if (!translated.includes(translatedText)) translated.push(translatedText);
          });
          if (!translated.length) return fallback;
          return translated.slice(0, 3).join('; ');
        }

        function summarizePublishResults(results) {
          const safeResults = Array.isArray(results) ? results : [];
          const summary = {
            total: safeResults.length,
            ok: 0,
            dryRun: 0,
            skipped: 0,
            failed: 0,
            reasons: new Map(),
          };

          safeResults.forEach((row) => {
            const status = String(row?.status || '').trim().toLowerCase();
            if (status === 'ok') {
              summary.ok += 1;
              return;
            }
            if (status === 'dry_run') {
              summary.dryRun += 1;
              return;
            }
            if (status === 'failed') {
              summary.failed += 1;
            } else {
              summary.skipped += 1;
            }
            const reasonSource = row?.reason || (status === 'failed' ? 'send_failed' : row?.error) || 'без причины';
            const reason = translatePublishReason(reasonSource);
            const prev = summary.reasons.get(reason) || 0;
            summary.reasons.set(reason, prev + 1);
          });

          return summary;
        }

        function summarizePublishStatusCounts(results) {
          const safeResults = Array.isArray(results) ? results : [];
          const summary = {
            total: safeResults.length,
            ok: 0,
            dryRun: 0,
            skipped: 0,
            failed: 0,
          };
          safeResults.forEach((row) => {
            const status = String(row?.status || '').trim().toLowerCase();
            if (status === 'ok') {
              summary.ok += 1;
              return;
            }
            if (status === 'dry_run') {
              summary.dryRun += 1;
              return;
            }
            if (status === 'failed') {
              summary.failed += 1;
              return;
            }
            summary.skipped += 1;
          });
          return summary;
        }

        function publishResultStatusMeta(statusRaw) {
          const status = String(statusRaw || '').trim().toLowerCase();
          if (status === 'ok') return { text: 'Отправлено', badge: 'success' };
          if (status === 'dry_run') return { text: 'Dry-run', badge: 'secondary' };
          if (status === 'skipped') return { text: 'Пропуск', badge: 'warning' };
          if (status === 'failed') return { text: 'Ошибка', badge: 'danger' };
          return { text: status || '—', badge: 'secondary' };
        }

        function publishResultRowClass(statusRaw) {
          const status = String(statusRaw || '').trim().toLowerCase();
          if (status === 'ok') return 'publish-result-row-ok';
          if (status === 'dry_run') return 'publish-result-row-dry';
          if (status === 'skipped') return 'publish-result-row-skipped';
          if (status === 'failed') return 'publish-result-row-failed';
          return '';
        }

        function isPublishIssueStatus(statusRaw) {
          const status = String(statusRaw || '').trim().toLowerCase();
          return status === 'failed' || status === 'skipped';
        }

        function renderPublishReasonCell(reasonText, statusRaw) {
          const reason = String(reasonText || '—').trim() || '—';
          const status = String(statusRaw || '').trim().toLowerCase();
          const compactLimit = (status === 'failed' || status === 'skipped') ? 120 : 84;
          if (reason.length <= compactLimit) {
            return `<span class="publish-reason-text">${escapeHtml(reason)}</span>`;
          }
          const compactReason = `${reason.slice(0, compactLimit - 1).trimEnd()}…`;
          return `
            <details class="publish-reason-expand">
              <summary class="publish-reason-summary" title="${escapeHtml(reason)}">${escapeHtml(compactReason)}</summary>
              <div class="publish-reason-full">${escapeHtml(reason)}</div>
            </details>
          `;
        }

        function formatPublishHistoryReason(row) {
          const reasonRaw = row?.reason ? String(row.reason).trim() : '';
          const translatedReason = reasonRaw ? translatePublishReason(reasonRaw) : '';
          const reasons = Array.isArray(row?.reasons)
            ? Array.from(new Set(row.reasons.map((item) => String(item || '').trim()).filter(Boolean).map((item) => translatePublishReason(item))))
            : [];
          const filteredReasons = translatedReason
            ? reasons.filter((item) => item !== translatedReason)
            : reasons;
          if (!translatedReason && reasons.length) {
            return reasons.slice(0, 2).join('; ');
          }
          if (!translatedReason) return '—';
          if (reasonRaw.toLowerCase() === 'quality_risk' && filteredReasons.length) {
            return `${translatedReason}: ${filteredReasons.slice(0, 2).join('; ')}`;
          }
          return translatedReason;
        }

        function setPublishResultState(message, tone = 'info') {
          const box = el('publish-result');
          if (!box) return;
          publishLastResponse = null;
          publishResultUiState.issuesOnly = false;
          const cls = tone === 'error'
            ? 'text-danger'
            : tone === 'warning'
              ? 'text-warning'
              : tone === 'success'
                ? 'text-success'
                : 'text-muted';
          box.className = `p-3 small ${cls}`;
          box.textContent = String(message || 'Результат: —');
          applyPublishLiveA11y(box, tone);
          updatePublishResultActionsAvailability();
        }

        function renderPublishResultDetails(response, summary, options = {}) {
          const box = el('publish-result');
          if (!box) return;
          const preserveCapturedAt = Boolean(options?.preserveCapturedAt);
          const previousDetailsEl = box.querySelector('.publish-result-details');
          const previousDetailsOpen = previousDetailsEl instanceof HTMLDetailsElement ? Boolean(previousDetailsEl.open) : null;
          const results = Array.isArray(response?.results) ? response.results : [];
          if (!results.length) {
            setPublishResultState('Результат: пустой ответ публикации', 'warning');
            return;
          }
          const capturedAt = preserveCapturedAt && publishLastResponse?.captured_at
            ? String(publishLastResponse.captured_at)
            : new Date().toISOString();
          publishLastResponse = {
            captured_at: capturedAt,
            dry_run: Boolean(response?.dry_run),
            summary: {
              total: Number(summary?.total || 0),
              ok: Number(summary?.ok || 0),
              dry_run: Number(summary?.dryRun || 0),
              skipped: Number(summary?.skipped || 0),
              failed: Number(summary?.failed || 0),
            },
            results: results.map((row) => ({
              market: row?.market ?? null,
              lang: row?.lang ?? null,
              status: row?.status ?? null,
              reason: row?.reason ?? null,
              error: row?.error ?? null,
            })),
          };

          const isDryRun = Boolean(response?.dry_run);
          const total = Number(summary?.total || 0);
          const title = isDryRun
            ? `Dry-run: ${summary?.dryRun || 0} из ${total}`
            : `Результат: ok ${summary?.ok || 0} • skip ${summary?.skipped || 0} • fail ${summary?.failed || 0}`;
          const reasonPairs = summary?.reasons instanceof Map
            ? Array.from(summary.reasons.entries()).sort((a, b) => b[1] - a[1]).slice(0, 4)
            : [];
          const toneClass = Number(summary?.failed || 0) > 0
            ? 'is-danger'
            : Number(summary?.ok || 0) > 0
              ? 'is-success'
              : Number(summary?.skipped || 0) > 0
                ? 'is-warning'
                : '';
          const toneForA11y = Number(summary?.failed || 0) > 0
            ? 'error'
            : Number(summary?.ok || 0) > 0
              ? 'success'
              : Number(summary?.skipped || 0) > 0
                ? 'warning'
                : 'info';
          const hasFailedRows = Number(summary?.failed || 0) > 0;
          const defaultOpenDetails = hasFailedRows || (Number(summary?.ok || 0) <= 0 && Number(summary?.skipped || 0) > 0);
          const openDetails = hasFailedRows
            ? true
            : (previousDetailsOpen === null ? defaultOpenDetails : previousDetailsOpen);
          const issueCount = results.filter((row) => isPublishIssueStatus(row?.status)).length;
          const visibleResults = publishResultUiState.issuesOnly
            ? results.filter((row) => isPublishIssueStatus(row?.status))
            : results;
          const hiddenCount = Math.max(0, results.length - visibleResults.length);
          const capturedAtText = publishLastResponse?.captured_at ? formatDateTime(publishLastResponse.captured_at) : '—';
          const visibleMetaText = hiddenCount ? ` • Показано: ${visibleResults.length} из ${results.length}` : '';
          const filterBadge = publishResultUiState.issuesOnly
            ? '<span class="badge bg-warning publish-result-filter-badge">фильтр: проблемы</span>'
            : '';
          const filterButtonText = publishResultUiState.issuesOnly ? 'Показать все' : 'Только проблемы';
          const filterButtonTitle = publishResultUiState.issuesOnly
            ? 'Показать все результаты публикации'
            : 'Показать только проблемные (skip/fail) результаты';

          box.className = `p-3 small publish-result-box ${toneClass}`.trim();
          applyPublishLiveA11y(box, toneForA11y);
          box.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2 publish-result-head">
              <div class="fw-bold">${escapeHtml(title)}</div>
              <div class="btn-group publish-result-actions" role="group" aria-label="Действия результата публикации">
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  data-action="publish-toggle-issues"
                  aria-controls="publish-result"
                  aria-label="Переключить фильтр проблемных результатов публикации"
                  title="${escapeHtml(filterButtonTitle)}"
                  aria-pressed="${publishResultUiState.issuesOnly ? 'true' : 'false'}"
                >⚠️ ${escapeHtml(filterButtonText)}</button>
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  data-action="publish-copy-result"
                  aria-controls="publish-result"
                  aria-label="Копировать результат публикации"
                  title="Копировать результат публикации"
                >📋 Копировать</button>
              </div>
            </div>
            <div class="d-flex gap-md mb-2 publish-result-kpis">
              ${filterBadge}
              <span class="badge bg-success">ok ${Number(summary?.ok || 0)}</span>
              <span class="badge bg-secondary">dry ${Number(summary?.dryRun || 0)}</span>
              <span class="badge bg-warning">skip ${Number(summary?.skipped || 0)}</span>
              <span class="badge bg-danger">fail ${Number(summary?.failed || 0)}</span>
            </div>
            <div id="publish-result-meta" class="text-muted publish-result-meta mb-2">Обновлено: ${escapeHtml(capturedAtText)} • Проблемных: ${issueCount}${escapeHtml(visibleMetaText)}</div>
            ${reasonPairs.length ? `
              <div class="publish-reason-chips mb-2">
                ${reasonPairs.map(([reason, count]) => {
                  const reasonText = String(reason || 'без причины');
                  const chipText = reasonText.length > 54 ? `${reasonText.slice(0, 53).trimEnd()}…` : reasonText;
                  return `
                    <span class="badge bg-secondary publish-reason-chip" title="${escapeHtml(reasonText)}">${escapeHtml(chipText)} ×${Number(count)}</span>
                  `;
                }).join('')}
              </div>
            ` : ''}
            <details class="publish-result-details"${openDetails ? ' open' : ''}>
              <summary class="small">Подробности (${visibleResults.length}${hiddenCount ? ` из ${results.length}` : ''}${issueCount ? `, проблемных ${issueCount}` : ''})</summary>
              <div class="table-responsive mt-2">
                <table class="table table-sm table-striped mb-0" aria-describedby="publish-result-meta">
                  <caption class="sr-only">Результаты публикации по рынкам и языкам</caption>
                  <thead class="table-dark">
                    <tr>
                      <th scope="col">Рынок</th>
                      <th scope="col">Язык</th>
                      <th scope="col">Статус</th>
                      <th scope="col">Причина</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${visibleResults.length ? visibleResults.map((row) => {
                      const market = row?.market ? String(row.market) : '—';
                      const lang = row?.lang ? String(row.lang) : '—';
                      const statusMeta = publishResultStatusMeta(row?.status);
                      const rowClass = publishResultRowClass(row?.status);
                      const reasonRaw = row?.reason || row?.error || '';
                      const reason = reasonRaw ? translatePublishReason(reasonRaw) : '—';
                      return `
                        <tr class="${escapeHtml(rowClass)}">
                          <td data-label="Рынок">${escapeHtml(market)}</td>
                          <td data-label="Язык">${escapeHtml(lang)}</td>
                          <td data-label="Статус"><span class="badge bg-${escapeHtml(statusMeta.badge)}">${escapeHtml(statusMeta.text)}</span></td>
                          <td data-label="Причина" class="publish-reason-cell">${renderPublishReasonCell(reason, row?.status)}</td>
                        </tr>
                      `;
                    }).join('') : `
                      <tr class="publish-result-row-empty">
                        <td colspan="4" class="publish-empty-note">Проблемных строк не найдено. Нажмите “Показать все”.</td>
                      </tr>
                    `}
                  </tbody>
                </table>
              </div>
            </details>
          `;
          updatePublishResultActionsAvailability();
        }

        function focusPublishResultAction(actionName) {
          const action = String(actionName || '').trim();
          if (!action) return;
          const box = el('publish-result');
          if (!box) return;
          const btn = box.querySelector(`[data-action="${action}"]`);
          if (!(btn instanceof HTMLButtonElement) || btn.disabled) return;
          try {
            btn.focus();
          } catch (e) {
            // ignore
          }
        }

        function rerenderPublishResultFromCache(options = {}) {
          if (!publishLastResponse || !Array.isArray(publishLastResponse.results) || !publishLastResponse.results.length) return;
          const response = {
            dry_run: Boolean(publishLastResponse.dry_run),
            results: publishLastResponse.results,
          };
          const summary = summarizePublishResults(response.results);
          renderPublishResultDetails(response, summary, { preserveCapturedAt: true });
          if (typeof options?.focusAction === 'string' && options.focusAction) {
            focusPublishResultAction(options.focusAction);
          }
        }

        function togglePublishIssuesView() {
          if (isPublishControlsBusy()) {
            notifyPublishBusyContext();
            return;
          }
          if (!publishLastResponse || !Array.isArray(publishLastResponse.results) || !publishLastResponse.results.length) {
            notify('Нет результата для фильтрации', 'warning');
            return;
          }
          publishResultUiState.issuesOnly = !publishResultUiState.issuesOnly;
          rerenderPublishResultFromCache({ focusAction: 'publish-toggle-issues' });
        }

        async function copyPublishResult() {
          if (isPublishControlsBusy()) {
            notifyPublishBusyContext();
            return;
          }
          if (!publishLastResponse) {
            notify('Нет результата для копирования', 'warning');
            return;
          }
          const ok = await copyToClipboard(JSON.stringify(publishLastResponse, null, 2));
          if (!ok) {
            notify('Не удалось скопировать результат публикации', 'error');
            return;
          }
          setPublishLog('Результат публикации скопирован', 'success');
          notify('📋 Результат публикации скопирован', 'success');
        }

        function applyPublishLiveA11y(targetEl, tone = 'info') {
          if (!(targetEl instanceof HTMLElement)) return;
          const level = String(tone || 'info').toLowerCase();
          const isCritical = level === 'error';
          targetEl.setAttribute('role', isCritical ? 'alert' : 'status');
          targetEl.setAttribute('aria-live', isCritical ? 'assertive' : 'polite');
          targetEl.setAttribute('aria-atomic', 'true');
        }

        function setPublishLog(message, level = 'info') {
          const logEl = el('publish-log');
          if (!logEl) return;
          if (!message) {
            logEl.textContent = '';
            logEl.className = 'small text-muted p-3';
            applyPublishLiveA11y(logEl, 'info');
            return;
          }
          const ts = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          const cls = level === 'error'
            ? 'text-danger'
            : level === 'success'
              ? 'text-success'
              : level === 'warning'
                ? 'text-warning'
                : 'text-muted';
          logEl.className = `small p-3 ${cls}`;
          logEl.textContent = `[${ts}] ${message}`;
          applyPublishLiveA11y(logEl, level);
        }

        function updatePublishHistoryActionsAvailability() {
          const container = el('publish-history');
          if (!container) return;
          const recentRows = Array.isArray(publishHistoryUiState.rows) ? publishHistoryUiState.rows : [];
          const hasIssues = recentRows.some((row) => isPublishIssueStatus(row?.status));
          const hasFixture = parseFixtureIdOrNull(publishHistoryUiState.fixtureId) !== null;
          const isBusy = isPublishControlsBusy() || publishHistoryUiState.loading;

          container.querySelectorAll('[data-action="publish-history-toggle-issues"]').forEach((node) => {
            if (!(node instanceof HTMLButtonElement)) return;
            const disabled = isBusy || !recentRows.length || (!hasIssues && !publishHistoryUiState.issuesOnly);
            node.disabled = disabled;
            node.setAttribute('aria-pressed', publishHistoryUiState.issuesOnly ? 'true' : 'false');
            if (disabled) node.setAttribute('aria-disabled', 'true');
            else node.removeAttribute('aria-disabled');
          });

          container.querySelectorAll('[data-action="publish-history-refresh"]').forEach((node) => {
            if (!(node instanceof HTMLButtonElement)) return;
            const disabled = isBusy || !hasFixture;
            node.disabled = disabled;
            if (disabled) node.setAttribute('aria-disabled', 'true');
            else node.removeAttribute('aria-disabled');
          });

          container.querySelectorAll('[data-action="publish-history-limit"]').forEach((node) => {
            if (!(node instanceof HTMLButtonElement)) return;
            const nodeLimit = normalizePublishHistoryLimit(node.dataset.limit);
            const selected = nodeLimit === publishHistoryUiState.limit;
            node.setAttribute('aria-pressed', selected ? 'true' : 'false');
            const disabled = isBusy || !hasFixture;
            node.disabled = disabled;
            if (disabled) node.setAttribute('aria-disabled', 'true');
            else node.removeAttribute('aria-disabled');
          });
        }

        function setPublishControlsPending(pending, actionButton = null, busyText = 'Отправка…') {
          const nextPending = Boolean(pending);
          if (publishControlsPending !== nextPending) {
            publishBusyNotifyState.key = '';
            publishBusyNotifyState.at = 0;
          }
          publishControlsPending = nextPending;
          const body = el('fixture-modal-body');
          if (body) {
            body.querySelectorAll('[data-action="publish-now"], [data-action="publish-refresh"], [data-action="publish-post-preview"], [data-action="publish-copy-result"], [data-action="publish-toggle-issues"], [data-action="publish-history-toggle-issues"], [data-action="publish-history-refresh"], [data-action="publish-history-limit"]').forEach((node) => {
              if (!(node instanceof HTMLButtonElement)) return;
              if (node === actionButton) return;
              node.disabled = pending;
              if (pending) {
                node.setAttribute('aria-busy', 'true');
                node.setAttribute('aria-disabled', 'true');
              } else {
                node.removeAttribute('aria-busy');
                node.removeAttribute('aria-disabled');
              }
            });
          }
          const dryRun = el('publish-dry-run');
          if (dryRun) {
            dryRun.disabled = pending;
            if (pending) dryRun.setAttribute('aria-disabled', 'true');
            else dryRun.removeAttribute('aria-disabled');
          }
          const theme = el('publish-image-theme');
          if (theme) {
            theme.disabled = pending;
            if (pending) theme.setAttribute('aria-disabled', 'true');
            else theme.removeAttribute('aria-disabled');
          }
          const pendingText = typeof busyText === 'string' && busyText.trim() ? busyText : 'Загрузка…';
          setActionButtonPending(actionButton, pending, pendingText);
          applyPublishActionAvailability();
          updatePublishResultActionsAvailability();
          updatePublishHistoryActionsAvailability();
        }

        function summarizePublishPreviewMarkets(marketsRaw) {
          const markets = Array.isArray(marketsRaw) ? marketsRaw : [];
          let ready = 0;
          let experimental = 0;
          markets.forEach((market) => {
            const headlineRaw = String(market?.headline_raw || market?.headline || '').trim();
            const analysisRaw = String(market?.analysis_raw || market?.analysis || '').trim();
            if (headlineRaw && analysisRaw) ready += 1;
            if (market?.experimental) experimental += 1;
          });
          const total = markets.length;
          const blocked = Math.max(0, total - ready);
          return { total, ready, blocked, experimental };
        }

        function renderPublishPreview(data) {
          const markets = Array.isArray(data?.markets) ? data.markets : [];
          if (!markets.length) return '<p class="text-muted">Нет данных для публикации</p>';
          const mode = data?.mode ? String(data.mode) : 'manual';
          const summary = summarizePublishPreviewMarkets(markets);
          return `
            <div class="small text-muted mb-2 publish-preview-head">
              <div class="publish-preview-summary">
                <span>Режим: ${escapeHtml(mode)} • Превью (RU)</span>
                <span class="badge ${summary.ready > 0 ? 'bg-success' : 'bg-warning'}">готово ${summary.ready}/${summary.total}</span>
                ${summary.blocked > 0 ? `<span class="badge bg-warning">блок ${summary.blocked}</span>` : ''}
                ${summary.experimental > 0 ? `<span class="badge bg-secondary">эксп ${summary.experimental}</span>` : ''}
              </div>
            </div>
            ${markets.map((m) => {
              if (!m?.headline || !m?.analysis) {
                const reason = translatePublishReasonsList(m?.reasons, 'нет данных');
                return `<div class="alert alert-warning">${escapeHtml(m?.market || 'рынок')}: ${escapeHtml(reason)}</div>`;
              }
              const tag = m.experimental ? '⚠️ ЭКСПЕРИМЕНТ' : 'OK';
              return `
                <div class="border rounded p-3 mb-3">
                  <div class="d-flex justify-content-between align-items-center mb-2">
                    <div class="fw-bold">${escapeHtml(m.market || 'рынок')}</div>
                    <span class="badge ${m.experimental ? 'bg-warning' : 'bg-success'}">${escapeHtml(tag)}</span>
                  </div>
                  <div class="telegram-preview mb-2">${renderTelegramHtml(m.headline)}</div>
                  <div class="telegram-preview">${renderTelegramHtml(m.analysis)}</div>
                </div>
              `;
            }).join('')}
          `;
        }

        function renderPostPreviewStatus(post) {
          const status = String(post?.status || '').trim().toLowerCase();
          if (status === 'ready') return { text: 'готово', cls: 'bg-success' };
          if (status === 'blocked') return { text: 'блок', cls: 'bg-warning' };
          if (status === 'unavailable') return { text: 'нет данных', cls: 'bg-secondary' };
          return { text: status || '—', cls: 'bg-secondary' };
        }

        function renderPublishPostPreview(data) {
          const posts = Array.isArray(data?.posts) ? data.posts : [];
          const mode = String(data?.mode || 'manual');
          const lang = String(data?.lang || 'ru').toUpperCase();
          const imageTheme = String(data?.image_theme || 'pro');
          const generatedAt = data?.generated_at ? formatDateTime(data.generated_at) : '—';
          if (!posts.length) {
            return `
              <div class="small text-muted publish-post-preview-headline">Пост-превью • ${escapeHtml(lang)} • ${escapeHtml(imageTheme)} • ${escapeHtml(mode)}</div>
              <p class="text-muted mt-2 mb-0">Нет данных для поста</p>
            `;
          }
          return `
            <div class="small text-muted publish-post-preview-headline">
              Пост-превью (как уйдет в Telegram): ${escapeHtml(lang)} • ${escapeHtml(imageTheme)} • ${escapeHtml(mode)} • ${escapeHtml(generatedAt)}
            </div>
            ${posts.map((post) => {
              const statusMeta = renderPostPreviewStatus(post);
              const reason = post?.reason ? translatePublishReason(post.reason) : '';
              const messages = Array.isArray(post?.messages) ? post.messages : [];
              const imageHtml = post?.uses_image && post?.image_data_url
                ? `<div class="publish-post-preview-image-wrap mb-2"><img class="publish-post-preview-image" src="${escapeHtml(post.image_data_url)}" alt="Превью изображения ${escapeHtml(String(post?.market || ''))}"></div>`
                : '';
              const fallbackNote = !post?.uses_image && post?.image_fallback_reason
                ? `<div class="small text-warning mb-2">Картинка недоступна: ${escapeHtml(translatePublishReason(post.image_fallback_reason))}</div>`
                : '';
              return `
                <div class="border rounded p-3 mb-3">
                  <div class="d-flex justify-content-between align-items-center mb-2 publish-post-preview-market-head">
                    <div class="fw-bold">${escapeHtml(String(post?.market || 'рынок'))}</div>
                    <span class="badge ${statusMeta.cls}">${escapeHtml(statusMeta.text)}</span>
                  </div>
                  ${reason ? `<div class="small text-warning mb-2">Причина: ${escapeHtml(reason)}</div>` : ''}
                  ${imageHtml}
                  ${fallbackNote}
                  <div class="small text-muted mb-2">Порядок отправки:</div>
                  ${messages.length ? messages.map((msg) => {
                    const msgType = String(msg?.type || '').trim().toLowerCase();
                    const order = Number(msg?.order || 0);
                    const section = String(msg?.section || '').trim() || 'text';
                    if (msgType === 'image') {
                      return `
                        <div class="publish-post-preview-message mb-2">
                          <div class="publish-post-preview-message-head">#${Number.isFinite(order) && order > 0 ? order : '—'} • image • ${escapeHtml(section)}</div>
                          <div class="telegram-preview">[image]</div>
                        </div>
                      `;
                    }
                    return `
                      <div class="publish-post-preview-message mb-2">
                        <div class="publish-post-preview-message-head">#${Number.isFinite(order) && order > 0 ? order : '—'} • text • ${escapeHtml(section)}</div>
                        <div class="telegram-preview">${renderTelegramHtml(msg?.text || '')}</div>
                      </div>
                    `;
                  }).join('') : '<div class="small text-muted">Сообщения не сформированы</div>'}
                </div>
              `;
            }).join('')}
          `;
        }

        function renderPublishStatusA11y(text, tone = 'info') {
          const message = String(text || '').trim();
          if (!message) return '';
          const level = String(tone || 'info').toLowerCase();
          const isCritical = level === 'error';
          const role = isCritical ? 'alert' : 'status';
          const live = isCritical ? 'assertive' : 'polite';
          return `<div class="sr-only" role="${role}" aria-live="${live}" aria-atomic="true">${escapeHtml(message)}</div>`;
        }

        async function loadPublishPreview(fixtureId, options = {}) {
          const expectedRequestSeq = normalizeRequestSeqOrNull(options?.requestSeq);
          const silentLog = Boolean(options?.silentLog);
          const fixtureLabel = publishFixtureLabel(fixtureId);
          const container = el('publish-preview');
          const isCurrentContext = () => isFixtureModalContextCurrent({ fixtureId, requestSeq: expectedRequestSeq });
          if (container && isCurrentContext()) {
            container.setAttribute('aria-busy', 'true');
            container.innerHTML = `${renderPublishStatusA11y(`${fixtureLabel}: превью загружается`, 'info')}<p class="text-muted">Загрузка...</p>`;
          }
          if (isCurrentContext() && !silentLog) setPublishLog(`${fixtureLabel}: превью — загрузка...`);
          try {
            const fixtureIdNum = parseFixtureIdOrNull(fixtureId);
            if (fixtureIdNum === null) throw new Error('Некорректный fixture_id');
            const data = await apiFetchJson(`/api/v1/publish/preview?fixture_id=${encodeURIComponent(String(fixtureIdNum))}`);
            if (!isCurrentContext()) return false;
            const summary = summarizePublishPreviewMarkets(data?.markets);
            const liveStatusText = summary.total > 0
              ? `${fixtureLabel}: превью обновлено, готово ${summary.ready} из ${summary.total}`
              : `${fixtureLabel}: превью без доступных рынков`;
            const liveTone = summary.ready > 0 ? 'success' : 'warning';
            if (container) {
              container.innerHTML = `${renderPublishStatusA11y(liveStatusText, liveTone)}${renderPublishPreview(data)}`;
              container.setAttribute('aria-busy', 'false');
            }
            updatePublishPreviewStateFromData(data);
            applyPublishActionAvailability();
            if (!silentLog) {
              const logMessage = summary.total > 0
                ? `${fixtureLabel}: превью обновлено (готово ${summary.ready}/${summary.total})`
                : `${fixtureLabel}: превью без доступных рынков`;
              const logLevel = summary.ready > 0 ? 'success' : 'warning';
              setPublishLog(logMessage, logLevel);
            }
            return true;
          } catch (e) {
            if (!isCurrentContext()) return false;
            handleScopedApiError(e, { showGenericNotify: false, updateConnection: false });
            if (container) {
              container.innerHTML = `${renderPublishStatusA11y(`${fixtureLabel}: ошибка загрузки превью`, 'error')}<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка загрузки')}</div>`;
              container.setAttribute('aria-busy', 'false');
            }
            publishPreviewState.hasLoaded = true;
            publishPreviewState.readyMarkets = 0;
            publishPreviewState.totalMarkets = 0;
            publishPreviewState.reasons = [];
            publishPreviewState.error = String(e?.message || 'ошибка загрузки превью');
            applyPublishActionAvailability();
            if (!silentLog) setPublishLog(`${fixtureLabel}: превью — ошибка ${e?.message || 'ошибка'}`, 'error');
            return false;
          }
        }

        async function loadPublishPostPreview(fixtureId, options = {}) {
          const expectedRequestSeq = normalizeRequestSeqOrNull(options?.requestSeq);
          const silentLog = Boolean(options?.silentLog);
          const fixtureLabel = publishFixtureLabel(fixtureId);
          const container = el('publish-post-preview');
          const isCurrentContext = () => isFixtureModalContextCurrent({ fixtureId, requestSeq: expectedRequestSeq });
          if (container && isCurrentContext()) {
            container.setAttribute('aria-busy', 'true');
            container.innerHTML = `${renderPublishStatusA11y(`${fixtureLabel}: пост-превью загружается`, 'info')}<p class="text-muted">Генерация поста...</p>`;
          }
          if (isCurrentContext() && !silentLog) setPublishLog(`${fixtureLabel}: пост-превью — генерация...`);
          try {
            const fixtureIdNum = parseFixtureIdOrNull(fixtureId);
            if (fixtureIdNum === null) throw new Error('Некорректный fixture_id');
            const imageTheme = getPublishImageTheme();
            const data = await apiFetchJson(
              `/api/v1/publish/post_preview?fixture_id=${encodeURIComponent(String(fixtureIdNum))}&image_theme=${encodeURIComponent(imageTheme)}`
            );
            if (!isCurrentContext()) return false;
            publishPostPreviewState.hasLoaded = true;
            publishPostPreviewState.error = '';
            if (container) {
              container.innerHTML = `${renderPublishStatusA11y(`${fixtureLabel}: пост-превью обновлено`, 'success')}${renderPublishPostPreview(data)}`;
              container.setAttribute('aria-busy', 'false');
            }
            if (!silentLog) setPublishLog(`${fixtureLabel}: пост-превью обновлено`, 'success');
            return true;
          } catch (e) {
            if (!isCurrentContext()) return false;
            handleScopedApiError(e, { showGenericNotify: false, updateConnection: false });
            publishPostPreviewState.hasLoaded = true;
            publishPostPreviewState.error = String(e?.message || 'ошибка загрузки пост-превью');
            if (container) {
              container.innerHTML = `${renderPublishStatusA11y(`${fixtureLabel}: ошибка загрузки пост-превью`, 'error')}<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка загрузки')}</div>`;
              container.setAttribute('aria-busy', 'false');
            }
            if (!silentLog) setPublishLog(`${fixtureLabel}: пост-превью — ошибка ${e?.message || 'ошибка'}`, 'error');
            return false;
          }
        }

        function buildPublishPanelsRefreshFeedback(fixtureLabel, previewOk, historyOk) {
          if (previewOk && historyOk) {
            const text = `${fixtureLabel}: превью и история обновлены`;
            return {
              status: 'ok',
              logLevel: 'success',
              logMessage: text,
              notifyLevel: 'success',
              notifyMessage: `${fixtureLabel}: обновление выполнено`,
            };
          }
          if (previewOk && !historyOk) {
            return {
              status: 'partial',
              logLevel: 'warning',
              logMessage: `${fixtureLabel}: превью обновлено, история — ошибка`,
              notifyLevel: 'warning',
              notifyMessage: `${fixtureLabel}: частично (история: ошибка)`,
            };
          }
          if (!previewOk && historyOk) {
            return {
              status: 'partial',
              logLevel: 'warning',
              logMessage: `${fixtureLabel}: превью — ошибка, история обновлена`,
              notifyLevel: 'warning',
              notifyMessage: `${fixtureLabel}: частично (превью: ошибка)`,
            };
          }
          return {
            status: 'failed',
            logLevel: 'error',
            logMessage: `${fixtureLabel}: обновление publish-данных — ошибка`,
            notifyLevel: 'error',
            notifyMessage: `${fixtureLabel}: обновление не удалось`,
          };
        }

        async function refreshPublishPanels(fixtureId, options = {}) {
          const requestSeq = normalizeRequestSeqOrNull(options?.requestSeq);
          const announce = options?.announce !== false;
          const notifyUser = Boolean(options?.notifyUser);
          const notifyOnSuccess = Boolean(options?.notifyOnSuccess);
          const fixtureLabel = publishFixtureLabel(fixtureId);
          const isCurrentContext = () => isFixtureModalContextCurrent({ fixtureId, requestSeq });
          if (announce && isCurrentContext()) setPublishLog(`${fixtureLabel}: обновление publish-данных...`);
          const [previewOk, historyOk] = await Promise.all([
            loadPublishPreview(fixtureId, { requestSeq, silentLog: true }),
            loadPublishHistory(fixtureId, { requestSeq, silentLog: true }),
          ]);
          const feedback = buildPublishPanelsRefreshFeedback(fixtureLabel, previewOk, historyOk);
          if (announce && isCurrentContext()) {
            setPublishLog(feedback.logMessage, feedback.logLevel);
          }
          if (notifyUser && isCurrentContext()) {
            if (feedback.status !== 'ok' || notifyOnSuccess) {
              notify(feedback.notifyMessage, feedback.notifyLevel);
            }
          }
          return { previewOk, historyOk, status: feedback.status, feedback };
        }

        function focusPublishHistoryAction(actionName, options = {}) {
          const action = String(actionName || '').trim();
          if (!action) return;
          const container = el('publish-history');
          if (!container) return;
          const limitValue = options?.limit === undefined || options?.limit === null
            ? ''
            : String(options.limit).trim();
          const specificSelector = action === 'publish-history-limit' && limitValue
            ? `[data-action="${action}"][data-limit="${limitValue}"]`
            : '';
          const btn = specificSelector
            ? (container.querySelector(specificSelector) || container.querySelector(`[data-action="${action}"]`))
            : container.querySelector(`[data-action="${action}"]`);
          if (!(btn instanceof HTMLButtonElement) || btn.disabled) return;
          try {
            btn.focus();
          } catch (e) {
            // ignore
          }
        }

        function renderPublishHistoryLimitControls() {
          return `
            <div class="btn-group publish-history-limit-group" role="group" aria-label="Лимит записей истории публикации">
              ${PUBLISH_HISTORY_LIMIT_OPTIONS.map((limit) => `
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  data-action="publish-history-limit"
                  data-limit="${limit}"
                  aria-controls="publish-history"
                  aria-describedby="publish-state-hint publish-history-summary"
                  aria-label="Показать последние ${limit} записей истории публикации"
                  title="Показать последние ${limit} записей"
                  aria-pressed="${publishHistoryUiState.limit === limit ? 'true' : 'false'}"
                >${limit}</button>
              `).join('')}
            </div>
          `;
        }

        function renderPublishHistoryFromState(options = {}) {
          const container = el('publish-history');
          if (!container) return;
          const fixtureLabel = publishFixtureLabel(publishHistoryUiState.fixtureId);
          const rows = Array.isArray(publishHistoryUiState.rows) ? publishHistoryUiState.rows : [];
          const hasRows = rows.length > 0;
          const isLoading = Boolean(publishHistoryUiState.loading);
          container.setAttribute('aria-busy', isLoading ? 'true' : 'false');
          const errorText = publishHistoryUiState.error ? String(publishHistoryUiState.error) : '';
          const historySummary = summarizePublishStatusCounts(rows);
          const issueRows = rows.filter((row) => isPublishIssueStatus(row?.status));
          const visibleRows = publishHistoryUiState.issuesOnly ? issueRows : rows;
          const hiddenCount = Math.max(0, rows.length - visibleRows.length);
          const hasIssues = issueRows.length > 0;
          const hasPossibleMore = hasRows && rows.length >= publishHistoryUiState.limit;
          const toggleLabel = publishHistoryUiState.issuesOnly ? 'Показать все' : 'Только проблемы';
          const toggleTitle = publishHistoryUiState.issuesOnly
            ? 'Показать все строки истории публикации'
            : 'Показать только проблемные строки (skip/fail)';
          const toggleDisabled = isLoading || !hasRows || (!hasIssues && !publishHistoryUiState.issuesOnly);
          const toggleDisabledAttrs = toggleDisabled ? 'disabled aria-disabled="true"' : '';
          const summaryText = isLoading
            ? `${fixtureLabel}: история — обновление...`
            : hasRows
              ? `${fixtureLabel}: последние ${rows.length}${hasPossibleMore ? '+' : ''}${hiddenCount ? ` • показано ${visibleRows.length}` : ''}`
              : `${fixtureLabel}: история пуста`;
          const filterBadge = publishHistoryUiState.issuesOnly
            ? '<span class="badge bg-warning publish-history-filter-badge">фильтр: проблемы</span>'
            : '';
          const liveStatusText = isLoading
            ? `${fixtureLabel}: история загружается`
            : errorText
              ? `${fixtureLabel}: история не загружена`
              : hasRows
                ? `${fixtureLabel}: загружено ${rows.length} записей истории`
                : `${fixtureLabel}: история без записей`;
          const liveStatusTone = errorText ? 'error' : 'info';
          const shouldAnnounceLive = publishHistoryLiveState.text !== liveStatusText || publishHistoryLiveState.tone !== liveStatusTone;
          const liveAnnouncement = shouldAnnounceLive ? renderPublishStatusA11y(liveStatusText, liveStatusTone) : '';
          publishHistoryLiveState.text = liveStatusText;
          publishHistoryLiveState.tone = liveStatusTone;
          const refreshLabel = isLoading ? '⏳ Обновление' : '🔄';
          const refreshActionLabel = isLoading ? 'История публикаций обновляется' : 'Обновить историю публикаций';

          container.innerHTML = `
            ${liveAnnouncement}
            <div class="small text-muted mb-2 publish-history-head">
              <div class="publish-history-summary">
                <span id="publish-history-summary">${escapeHtml(summaryText)}</span>
                ${filterBadge}
                <span class="badge bg-success">ok ${historySummary.ok}</span>
                <span class="badge bg-secondary">dry ${historySummary.dryRun}</span>
                <span class="badge bg-warning">skip ${historySummary.skipped}</span>
                <span class="badge bg-danger">fail ${historySummary.failed}</span>
              </div>
              <div class="publish-history-actions">
                ${renderPublishHistoryLimitControls()}
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  data-action="publish-history-refresh"
                  aria-controls="publish-history"
                  aria-describedby="publish-state-hint publish-history-summary"
                  aria-label="${escapeHtml(refreshActionLabel)}"
                  title="${escapeHtml(refreshActionLabel)}"
                  ${isLoading ? 'aria-busy="true"' : ''}
                >${refreshLabel}</button>
                <button
                  type="button"
                  class="btn-secondary btn-sm"
                  data-action="publish-history-toggle-issues"
                  aria-controls="publish-history"
                  aria-describedby="publish-state-hint publish-history-summary"
                  aria-label="Переключить фильтр проблемных записей истории публикации"
                  title="${escapeHtml(toggleTitle)}"
                  aria-pressed="${publishHistoryUiState.issuesOnly ? 'true' : 'false'}"
                  ${toggleDisabledAttrs}
                >⚠️ ${escapeHtml(toggleLabel)}</button>
              </div>
            </div>
            ${errorText ? `<div class="alert alert-danger mb-2" role="alert">${escapeHtml(errorText)}</div>` : ''}
            ${isLoading && !hasRows ? `<div class="small text-muted mb-2">Загрузка истории публикации...</div>` : ''}
            ${!hasRows && !isLoading && !errorText ? '<div class="publish-empty-note py-2">История публикации пуста.</div>' : ''}
            ${hasRows ? `
              <div class="table-responsive">
                <table class="table table-sm table-striped" aria-describedby="publish-history-summary">
                  <caption class="sr-only">История публикаций по рынкам и языкам</caption>
                    <thead class="table-dark">
                      <tr>
                        <th scope="col">Время</th>
                        <th scope="col">Рынок</th>
                        <th scope="col">Язык</th>
                        <th scope="col">Статус</th>
                        <th scope="col">Причина</th>
                        <th scope="col">Сообщ.</th>
                        <th scope="col">Ошибка</th>
                        <th scope="col">Эксп.</th>
                      </tr>
                    </thead>
                  <tbody>
                    ${visibleRows.length ? visibleRows.map((row) => {
                      const statusMeta = publishResultStatusMeta(row?.status);
                      const rowClass = publishResultRowClass(row?.status);
                      const reasonText = formatPublishHistoryReason(row);
                      const messageId = String(row?.headline_message_id || row?.analysis_message_id || '—');
                      const errorRowText = row?.error ? String(row.error) : '—';
                      return `
                        <tr class="${escapeHtml(rowClass)}">
                          <td data-label="Время">${escapeHtml(formatDateTime(row?.created_at))}</td>
                          <td data-label="Рынок">${escapeHtml(row?.market || '—')}</td>
                          <td data-label="Язык">${escapeHtml(row?.language || '—')}</td>
                          <td data-label="Статус"><span class="badge bg-${escapeHtml(statusMeta.badge)}">${escapeHtml(statusMeta.text)}</span></td>
                          <td data-label="Причина" class="publish-history-reason">${renderPublishReasonCell(reasonText, row?.status)}</td>
                          <td data-label="Сообщ."><span class="publish-history-message-id">${escapeHtml(messageId)}</span></td>
                          <td data-label="Ошибка" class="publish-history-error">${renderPublishReasonCell(errorRowText, row?.status)}</td>
                          <td data-label="Эксп.">${row?.experimental ? 'да' : 'нет'}</td>
                        </tr>
                      `;
                    }).join('') : `
                      <tr class="publish-result-row-empty">
                        <td colspan="8" class="publish-empty-note">Проблемных записей не найдено. Нажмите “Показать все”.</td>
                      </tr>
                    `}
                  </tbody>
                </table>
              </div>
            ` : ''}
          `;
          updatePublishHistoryActionsAvailability();
          if (typeof options?.focusAction === 'string' && options.focusAction) {
            focusPublishHistoryAction(options.focusAction, { limit: options?.focusLimit });
          }
        }

        function togglePublishHistoryIssuesView() {
          if (publishHistoryUiState.loading) {
            notifyPublishHistoryLoading();
            return;
          }
          if (isPublishControlsBusy()) {
            notifyPublishBusyContext();
            return;
          }
          if (!Array.isArray(publishHistoryUiState.rows) || !publishHistoryUiState.rows.length) {
            notify('Нет истории для фильтрации', 'warning');
            return;
          }
          publishHistoryUiState.issuesOnly = !publishHistoryUiState.issuesOnly;
          renderPublishHistoryFromState({ focusAction: 'publish-history-toggle-issues' });
        }

        async function applyPublishHistoryLimit(limitRaw) {
          if (isPublishControlsBusy() || publishHistoryUiState.loading) return false;
          const nextLimit = normalizePublishHistoryLimit(limitRaw);
          const previousLimit = publishHistoryUiState.limit;
          publishHistoryUiState.limit = nextLimit;
          if (nextLimit !== previousLimit) scheduleUiStateSave();
          const fixtureId = resolvePublishFixtureId();
          if (!fixtureId) {
            renderPublishHistoryFromState({ focusAction: 'publish-history-limit', focusLimit: nextLimit });
            return false;
          }
          if (nextLimit === previousLimit && publishHistoryUiState.rows.length > 0) {
            renderPublishHistoryFromState({ focusAction: 'publish-history-limit', focusLimit: nextLimit });
            return true;
          }
          return await loadPublishHistory(fixtureId, {
            requestSeq: fixtureModalState.requestSeq,
            limit: nextLimit,
            focusAction: 'publish-history-limit',
            focusLimit: nextLimit,
          });
        }

        async function loadPublishHistory(fixtureId, options = {}) {
          const expectedRequestSeq = normalizeRequestSeqOrNull(options?.requestSeq);
          const silentLog = Boolean(options?.silentLog);
          const fixtureLabel = publishFixtureLabel(fixtureId);
          const isCurrentContext = () => isFixtureModalContextCurrent({ fixtureId, requestSeq: expectedRequestSeq });
          const fixtureIdNum = parseFixtureIdOrNull(fixtureId);
          const requestedLimit = normalizePublishHistoryLimit(options?.limit ?? publishHistoryUiState.limit);
          if (isCurrentContext()) {
            publishHistoryUiState.fixtureId = fixtureIdNum;
            publishHistoryUiState.limit = requestedLimit;
            publishHistoryUiState.loading = true;
            publishHistoryUiState.error = '';
            publishHistoryLoadingNotifyAt = 0;
            renderPublishHistoryFromState();
          }
          try {
            if (fixtureIdNum === null) throw new Error('Некорректный fixture_id');
            const rows = await apiFetchJson(
              `/api/v1/publish/history?fixture_id=${encodeURIComponent(String(fixtureIdNum))}&limit=${encodeURIComponent(String(requestedLimit))}`
            );
            if (!isCurrentContext()) return false;
            publishHistoryUiState.fixtureId = fixtureIdNum;
            publishHistoryUiState.rows = Array.isArray(rows) ? rows : [];
            publishHistoryUiState.limit = requestedLimit;
            publishHistoryUiState.loading = false;
            publishHistoryUiState.error = '';
            renderPublishHistoryFromState({
              focusAction: typeof options?.focusAction === 'string' ? options.focusAction : '',
              focusLimit: options?.focusLimit,
            });
            return true;
          } catch (e) {
            if (!isCurrentContext()) return false;
            handleScopedApiError(e, { showGenericNotify: false, updateConnection: false });
            publishHistoryUiState.fixtureId = parseFixtureIdOrNull(fixtureId);
            publishHistoryUiState.loading = false;
            publishHistoryUiState.error = String(e?.message || 'Ошибка загрузки истории');
            renderPublishHistoryFromState({
              focusAction: typeof options?.focusAction === 'string' ? options.focusAction : '',
              focusLimit: options?.focusLimit,
            });
            if (!silentLog) setPublishLog(`${fixtureLabel}: история — ошибка ${e?.message || 'ошибка'}`, 'error');
            return false;
          }
        }

        async function publishNow(fixtureId, force = false, actionButton = null) {
          if (isPublishControlsBusy()) {
            notifyPublishBusyContext();
            return false;
          }
          const fixtureIdNum = parseFixtureIdOrNull(fixtureId);
          if (fixtureIdNum === null) {
            setPublishLog('Ошибка отправки: не найден корректный ID матча', 'error');
            setPublishResultState('Ошибка отправки: не найден корректный ID матча', 'error');
            notify('Не удалось определить fixture_id для публикации', 'error');
            return false;
          }
          const fixturePrefix = `Матч ${fixtureIdNum}: `;
          if (publishPreviewState.hasLoaded && publishPreviewState.readyMarkets <= 0) {
            setPublishLog(`${fixturePrefix}отправка отменена: в превью нет готовых рынков`, 'error');
            setPublishResultState(`${fixturePrefix}отправка отменена: в превью нет готовых рынков`, 'warning');
            notify('Нет готовых данных для отправки', 'warning');
            applyPublishActionAvailability();
            return false;
          }

          const payload = {
            fixture_id: fixtureIdNum,
            force: Boolean(force),
            dry_run: getPublishDryRun(),
            image_theme: getPublishImageTheme(),
          };
          const requestSeq = fixtureModalState.requestSeq;
          const shouldApplyUi = () => isFixtureModalContextCurrent({ fixtureId: fixtureIdNum, requestSeq });
          publishInFlight = true;
          setPublishControlsPending(true, actionButton);
          setPublishLog(payload.dry_run ? `${fixturePrefix}отправка dry-run...` : `${fixturePrefix}отправка: запуск...`);
          setPublishResultState(payload.dry_run ? 'Отправка dry-run…' : 'Отправка публикации…', 'info');
          try {
            const res = await apiFetchJson('/api/v1/publish', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
            if (res?.reservation_locked) {
              const summary = summarizePublishResults(res?.results);
              const msg = `${fixturePrefix}публикация уже выполняется в другом процессе`;
              if (shouldApplyUi()) {
                setPublishLog(msg, 'warning');
                renderPublishResultDetails(res, summary);
              }
              notify(msg, 'warning');
              return false;
            }
            const refreshState = await refreshPublishPanels(fixtureId, { requestSeq, announce: false, notifyUser: false });
            const summary = summarizePublishResults(res?.results);
            if (res?.dry_run) {
              const msg = `${fixturePrefix}dry-run: ${summary.dryRun} публикаций${summary.total ? ` из ${summary.total}` : ''}`;
              if (shouldApplyUi()) setPublishLog(msg, summary.dryRun > 0 ? 'success' : 'error');
              notify(msg, summary.dryRun > 0 ? 'info' : 'warning');
            } else {
              const reasonPairs = Array.from(summary.reasons.entries()).sort((a, b) => b[1] - a[1]).slice(0, 2);
              const reasonText = reasonPairs.length
                ? ` • причины: ${reasonPairs.map(([k, v]) => `${k}×${v}`).join(', ')}`
                : '';
              const msg = `${fixturePrefix}публикация: ok ${summary.ok}, skip ${summary.skipped}, fail ${summary.failed}${reasonText}`;
              const level = summary.ok > 0 ? 'success' : (summary.failed > 0 ? 'error' : 'warning');
              if (shouldApplyUi()) setPublishLog(msg, level);
              notify(msg, level === 'success' ? 'success' : 'warning');
            }
            if (refreshState.status !== 'ok') {
              const refreshMessageBase = refreshState?.feedback?.notifyMessage
                ? String(refreshState.feedback.notifyMessage)
                : (refreshState.status === 'failed'
                  ? `${fixturePrefix}интерфейс не обновлен`
                  : `${fixturePrefix}интерфейс обновлен частично`);
              const refreshMessage = `${refreshMessageBase} (после отправки)`;
              notify(refreshMessage, refreshState.status === 'failed' ? 'error' : 'warning');
            }
            if (shouldApplyUi()) renderPublishResultDetails(res, summary);
            return true;
          } catch (e) {
            handleScopedApiError(e, { showGenericNotify: false, updateConnection: false });
            if (shouldApplyUi()) {
              setPublishLog(`${fixturePrefix}ошибка отправки: ${e?.message || 'ошибка'}`, 'error');
              setPublishResultState(`Ошибка отправки: ${e?.message || 'ошибка'}`, 'error');
            }
            notify(`${fixturePrefix}публикация не удалась`, 'error');
            return false;
          } finally {
            publishInFlight = false;
            setPublishControlsPending(false, actionButton);
            if (actionButton && shouldApplyUi() && !actionButton.disabled) {
              try {
                actionButton.focus();
              } catch (e) {
                // ignore
              }
            }
          }
        }

        function renderFixtureModalContent(data) {
          const f = data?.fixture || {};
          const teams = f.home && f.away ? `${String(f.home)} vs ${String(f.away)}` : `Матч ${String(f.id ?? '—')}`;
          const league = f.league ? String(f.league) : '—';
          const kickoff = f.kickoff ? formatDateTime(f.kickoff) : '—';
          const status = f.status ? String(f.status) : '—';
          const score = f.home_goals !== null && f.home_goals !== undefined && f.away_goals !== null && f.away_goals !== undefined
            ? `${f.home_goals}-${f.away_goals}`
            : '—';
          const homeName = f.home ? String(f.home) : 'Домашняя';
          const awayName = f.away ? String(f.away) : 'Гостевая';
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
            ['Лига', league],
            ['Старт', kickoff],
            ['Статус матча', status],
            ['Счет', score],
            ['Время получения коэффициентов', data?.odds?.fetched_at ? formatDateTime(data.odds.fetched_at) : '—'],
            ['Снапшот коэффициентов до матча', data?.odds_pre_kickoff?.fetched_at ? formatDateTime(data.odds_pre_kickoff.fetched_at) : '—'],
            ['Источник вероятностей', ff?.prob_source ? String(ff.prob_source) : d1?.prob_source ? String(d1.prob_source) : '—'],
            ['λ дом / гость / total', `${formatFixed(ff?.lam_home, 2)} / ${formatFixed(ff?.lam_away, 2)} / ${formatFixed(ff?.lam_total, 2)}`],
            ['Elo дом / гость / разница', `${formatFixed(ff?.elo_home, 1)} / ${formatFixed(ff?.elo_away, 1)} / ${formatFixed(ff?.elo_diff, 1)}`],
            ['Корр. фактор', formatFixed(ff?.adj_factor, 3)],
            ['Сигнал', p1?.signal_score === null || p1?.signal_score === undefined ? '—' : formatPercent01(p1.signal_score, 1)],
            ['Сигнал (raw)', formatFixed(ff?.signal_score_raw, 3)],
            ['Компоненты сигнала (samples/vol/elo)', `${formatFixed(ff?.samples_score, 3)} / ${formatFixed(ff?.volatility_score, 3)} / ${formatFixed(ff?.elo_gap_score, 3)}`],
            ['xPts разница', ff?.xpts_diff === null || ff?.xpts_diff === undefined ? '—' : String(ff.xpts_diff)],
            ['Частота ничьих', ff?.league_draw_freq === null || ff?.league_draw_freq === undefined ? '—' : formatPercent01(ff.league_draw_freq, 1)],
            ['Dixon‑Coles ρ', ff?.dc_rho === null || ff?.dc_rho === undefined ? '—' : String(ff.dc_rho)],
            ['Калибровка α', ff?.calib_alpha === null || ff?.calib_alpha === undefined ? '—' : String(ff.calib_alpha)],
            ['Дельта таблицы', ff?.standings_delta === null || ff?.standings_delta === undefined ? '—' : String(ff.standings_delta)],
            ['Травмы (дом/гость)', (ff?.injuries_home !== undefined || ff?.injuries_away !== undefined) ? `${String(ff.injuries_home ?? 0)} / ${String(ff.injuries_away ?? 0)}` : '—'],
            ['Штраф за травмы (дом/гость)', `${formatFixed(ff?.injury_penalty_home, 3)} / ${formatFixed(ff?.injury_penalty_away, 3)}`],
            ['Неопределенность травм', formatFixed(ff?.injury_uncertainty, 3)],
            ['Дисперсия голов', ff?.goal_variance === null || ff?.goal_variance === undefined ? '—' : String(ff.goal_variance)],
            ['Рыночное отклонение', md === null || !Number.isFinite(md) ? '—' : `${(md * 100).toFixed(1)}%`],
            ['Порог', thr === null || !Number.isFinite(thr) ? '—' : `${(thr * 100).toFixed(1)}%`],
            ['Бэктест', ff?.backtest ? 'да' : 'нет'],
            ['Тип BT', ff?.bt_kind ? String(ff.bt_kind) : '—'],
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
                  <div class="text-muted small">Вер. ${escapeHtml(prob)} • Коэфф. ${escapeHtml(odd)} • EV ${escapeHtml(ev)}</div>
                  <div class="mt-2"><span class="badge bg-${escapeHtml(badge)}">${escapeHtml(statusRaw)}</span> <span class="ms-2">${escapeHtml(profit)}</span></div>
                </div>
              </div>
            `;
          };

          const rawBlocks = `
            <div class="small text-muted mt-3">
              Технические JSON-блоки для диагностики решения модели.
            </div>
            <details class="mt-2 fixture-raw-details">
              <summary class="fw-bold">Технические данные: решения модели (decisions)</summary>
              <div class="small text-muted mt-2">Показывает action/reason/кандидатов по каждому рынку.</div>
              <pre class="bg-light p-3 border rounded pre-scroll mt-2">${escapeHtml(prettyJson(data?.decisions || {}))}</pre>
            </details>
            <details class="mt-2 fixture-raw-details">
              <summary class="fw-bold">Технические данные: индексы матча (match_indices)</summary>
              <div class="small text-muted mt-2">Фичи и агрегаты, использованные в расчете.</div>
              <pre class="bg-light p-3 border rounded pre-scroll mt-2">${escapeHtml(prettyJson(data?.match_indices || null))}</pre>
            </details>
            <details class="mt-2 fixture-raw-details">
              <summary class="fw-bold">Технические данные: коэффициенты (odds)</summary>
                <div class="small text-muted mt-2">Текущие коэффициенты и pre-kickoff снапшот для CLV (closing line value).</div>
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
                  <div class="card-title mb-0">Ключевые факты</div>
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
              <div class="card-title mb-0">Инфо-рынки</div>
              <div class="info-markets">
                ${infoBlocks}
              </div>
            </div>

            <div class="card mt-3">
              <div class="card-header">
                <h3 class="card-title mb-0">📣 Публикация (Telegram)</h3>
                <div class="publish-toolbar">
                  <div class="publish-toolbar-options" role="group" aria-label="Настройки публикации">
                    <label class="small text-muted" for="publish-dry-run">
                      <input type="checkbox" id="publish-dry-run"${publishUiState.dryRun ? ' checked' : ''}> тестовый прогон
                    </label>
                    <label class="small text-muted publish-theme-control" for="publish-image-theme">
                      Стиль картинки
                      <select id="publish-image-theme" class="form-select select-compact">
                        <option value="pro"${publishUiState.imageTheme === 'pro' ? ' selected' : ''}>Pro</option>
                        <option value="viral"${publishUiState.imageTheme === 'viral' ? ' selected' : ''}>Viral</option>
                      </select>
                    </label>
                  </div>
                  <div class="btn-group publish-toolbar-actions" role="group" aria-label="Действия публикации">
                    <button type="button" class="btn-secondary btn-sm" data-action="publish-refresh" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" aria-controls="publish-preview publish-history publish-log publish-state-hint" aria-describedby="publish-state-hint" aria-label="Обновить превью и историю публикации" title="Обновить превью и историю публикации">🔄 Обновить</button>
                    <button type="button" class="btn-secondary btn-sm" data-action="publish-post-preview" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" aria-controls="publish-post-preview publish-log publish-state-hint" aria-describedby="publish-state-hint" aria-label="Предпросмотр полного поста (картинка + текст)" title="Предпросмотр полного поста (картинка + текст)">👁 Предпросмотр поста</button>
                    <button type="button" class="btn btn-success btn-sm" data-action="publish-now" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" aria-controls="publish-result publish-log publish-state-hint publish-history" aria-describedby="publish-state-hint" aria-label="Отправить публикацию" title="Отправить публикацию">Отправить</button>
                    <button type="button" class="btn btn-danger btn-sm" data-action="publish-now" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" data-force="1" aria-controls="publish-result publish-log publish-state-hint publish-history" aria-describedby="publish-state-hint publish-force-help" aria-label="Принудительная публикация (обойти защитные проверки)" title="Принудительная публикация (обойти защитные проверки)">Принудительно</button>
                  </div>
                  <span id="publish-force-help" class="sr-only">Принудительная публикация обходит защитные проверки и должна использоваться только в аварийном сценарии.</span>
                </div>
              </div>
            </div>
              <div id="publish-state-hint" class="small mt-2 text-muted" role="status" aria-live="polite" aria-atomic="true">Проверка готовности публикации…</div>
              <div id="publish-log" class="small text-muted p-3" role="status" aria-live="polite" aria-atomic="true"></div>
              <div id="publish-result" class="p-3 small text-muted" role="status" aria-live="polite" aria-atomic="false">Результат: —</div>
              <div id="publish-preview" class="p-3" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" role="region" aria-label="Превью публикации" aria-describedby="publish-state-hint" aria-busy="true">Загрузка...</div>
              <div id="publish-post-preview" class="p-3 text-muted" data-fixture-id="${escapeHtml(String(f.id ?? ''))}" role="region" aria-label="Предпросмотр полного поста" aria-describedby="publish-state-hint" aria-busy="false">Нажмите “Предпросмотр поста”, чтобы увидеть картинку и итоговый текст перед отправкой.</div>
              <div id="publish-history" class="p-3 text-muted" role="region" aria-label="История публикаций" aria-describedby="publish-state-hint">История: —</div>
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
          const requestSeq = fixtureModalState.requestSeq + 1;
          fixtureModalState.requestSeq = requestSeq;

          fixtureModalState.returnFocusEl = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          fixtureModalState.fixtureId = fid;
          resetPublishModalState();
          setHidden(overlay, false);
          document.body.classList.add('modal-open');
          titleEl.textContent = `Матч ${fid}`;
          bodyEl.innerHTML = '<p class="text-muted">Загрузка...</p>';
          bodyEl.setAttribute('aria-busy', 'true');
          focusFixtureModalPrimaryControl();

          try {
            const cached = fixtureModalState.cache.get(fid);
            const data = cached || await apiFetchJson(`/api/v1/fixtures/${encodeURIComponent(fid)}/details`);
            if (
              fixtureModalState.requestSeq !== requestSeq
              || fixtureModalState.fixtureId !== fid
              || overlay.classList.contains('is-hidden')
            ) {
              return;
            }
            fixtureModalState.cache.set(fid, data);
            const f = data?.fixture || {};
            const teams = f.home && f.away ? `${String(f.home)} vs ${String(f.away)}` : `Матч ${fid}`;
            const league = f.league ? String(f.league) : '';
            titleEl.textContent = league ? `${teams} • ${league}` : teams;
            bodyEl.innerHTML = renderFixtureModalContent(data);
            setPublishResultState('Результат: —');
            applyPublishActionAvailability();
            applyLogoFallbacks(bodyEl);
            focusFixtureModalPrimaryControl();
            await refreshPublishPanels(fid, { requestSeq });
          } catch (e) {
            if (
              fixtureModalState.requestSeq !== requestSeq
              || fixtureModalState.fixtureId !== fid
              || overlay.classList.contains('is-hidden')
            ) {
              return;
            }
            console.error(e);
            bodyEl.innerHTML = `<div class="alert alert-danger">Не удалось загрузить детали матча (${escapeHtml(String(e?.message || e))})</div>`;
          } finally {
            if (fixtureModalState.requestSeq !== requestSeq) return;
            bodyEl.setAttribute('aria-busy', 'false');
          }
        }

        function updateBetsHistoryPagerAvailability() {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return;

          const isPageMode = betsHistoryState.viewMode === 'page';
          const total = Number.isFinite(betsHistoryState.total) ? betsHistoryState.total : null;
          const rawRows = Number(betsHistoryState.lastPageRows);
          const rowCount = Number.isFinite(rawRows) && rawRows >= 0 ? Math.floor(rawRows) : 0;
          const noFurtherRowsLikely = isPageMode && total === null && rowCount < betsHistoryState.limit;

          const prevBtn = panel.querySelector('[data-action="bets-prev"]');
          if (prevBtn instanceof HTMLButtonElement) {
            const prevDisabled = betsHistoryInFlight || !isPageMode || betsHistoryState.offset <= 0;
            prevBtn.disabled = prevDisabled;
            if (prevDisabled) prevBtn.setAttribute('aria-disabled', 'true');
            else prevBtn.removeAttribute('aria-disabled');
          }

          const nextBtn = panel.querySelector('[data-action="bets-next"]');
          if (nextBtn instanceof HTMLButtonElement) {
            let nextDisabled = betsHistoryInFlight || !isPageMode;
            if (!nextDisabled) {
              if (total !== null) nextDisabled = betsHistoryState.offset + betsHistoryState.limit >= total;
              else nextDisabled = noFurtherRowsLikely;
            }
            nextBtn.disabled = nextDisabled;
            if (nextDisabled) nextBtn.setAttribute('aria-disabled', 'true');
            else nextBtn.removeAttribute('aria-disabled');
          }

          const hintEl = el('bets-history-page-hint');
          if (!hintEl) return;
          if (betsHistoryInFlight) {
            hintEl.textContent = 'Загрузка истории ставок…';
            return;
          }
          if (!isPageMode) {
            hintEl.textContent = 'Режим: загружены все строки, постраничная навигация отключена';
            return;
          }
          if (total !== null) {
            const from = rowCount > 0 ? betsHistoryState.offset + 1 : 0;
            const to = betsHistoryState.offset + rowCount;
            hintEl.textContent = `Страница: ${from}-${to} из ${total} • Лимит: ${betsHistoryState.limit}`;
            return;
          }
          const tail = noFurtherRowsLikely ? ' • вероятно конец выборки' : '';
          hintEl.textContent = `Смещение: ${betsHistoryState.offset} • Лимит: ${betsHistoryState.limit} • Строк: ${rowCount}${tail}`;
        }

        function setBetsHistoryControlsPending(pending, activeButton = null, busyText = 'Загрузка…') {
          ['bets-refresh', 'bets-apply', 'bets-load-all', 'bets-export-csv', 'bets-open-all-time', 'bets-prev', 'bets-next', 'toggle-bets-history'].forEach((action) => {
            document.querySelectorAll(`[data-action="${action}"]`).forEach((node) => {
              if (!(node instanceof HTMLButtonElement)) return;
              if (node === activeButton) return;
              node.disabled = pending;
              if (pending) {
                node.setAttribute('aria-busy', 'true');
                node.setAttribute('aria-disabled', 'true');
              } else {
                node.removeAttribute('aria-busy');
                node.removeAttribute('aria-disabled');
              }
            });
          });
          setActionButtonPending(activeButton, pending, busyText);

          ['bets-market', 'bets-status', 'bets-sort', 'bets-limit', 'bets-team', 'bets-settled-only', 'bets-all-time'].forEach((id) => {
            const input = el(id);
            if (!(input instanceof HTMLInputElement || input instanceof HTMLSelectElement)) return;
            input.disabled = pending;
            if (pending) input.setAttribute('aria-disabled', 'true');
            else input.removeAttribute('aria-disabled');
          });

          const panel = el('bets-history-panel');
          if (panel) {
            if (pending) panel.setAttribute('aria-busy', 'true');
            else panel.removeAttribute('aria-busy');
          }
          const resultEl = el('bets-history-result');
          if (resultEl) {
            if (pending) resultEl.setAttribute('aria-busy', 'true');
            else resultEl.removeAttribute('aria-busy');
          }
          const taskEl = el('bets-history-task');
          if (taskEl) {
            if (pending) taskEl.setAttribute('aria-busy', 'true');
            else taskEl.removeAttribute('aria-busy');
          }

          updateBetsHistoryPagerAvailability();
        }

        function setBetsHistoryTaskState(message, tone = 'info') {
          const taskEl = el('bets-history-task');
          if (!taskEl) return;
          const text = String(message || '').trim();
          if (!text) {
            taskEl.className = 'text-muted small';
            taskEl.textContent = '';
            applyPublishLiveA11y(taskEl, 'info');
            return;
          }
          const level = String(tone || 'info').toLowerCase();
          const cls = level === 'error'
            ? 'text-danger'
            : level === 'success'
              ? 'text-success'
              : level === 'warning'
                ? 'text-warning'
                : 'text-muted';
          taskEl.className = `${cls} small`;
          taskEl.textContent = text;
          applyPublishLiveA11y(taskEl, level);
        }

        function notifyBetsHistoryBusy() {
          const now = Date.now();
          if (now - betsHistoryBusyNotifyAt < BETS_HISTORY_BUSY_NOTIFY_COOLDOWN_MS) return;
          betsHistoryBusyNotifyAt = now;
          notify('Дождитесь завершения текущей операции истории ставок', 'warning');
        }

        async function runBetsHistoryTask(taskFn, options = {}) {
          const actionButton = options?.actionButton instanceof HTMLButtonElement ? options.actionButton : null;
          const busyText = typeof options?.busyText === 'string' && options.busyText.trim() ? options.busyText : 'Загрузка…';
          const notifyOnBusy = Boolean(options?.notifyOnBusy);
          const onError = typeof options?.onError === 'function' ? options.onError : null;

          if (betsHistoryInFlight) {
            if (notifyOnBusy) notifyBetsHistoryBusy();
            return false;
          }

          betsHistoryBusyNotifyAt = 0;
          betsHistoryInFlight = true;
          setBetsHistoryControlsPending(true, actionButton, busyText);
          try {
            await taskFn();
            return true;
          } catch (e) {
            handleApiError(e);
            if (onError) {
              try {
                onError(e);
              } catch (callbackError) {
                console.error(callbackError);
              }
            }
            return false;
          } finally {
            betsHistoryInFlight = false;
            betsHistoryBusyNotifyAt = 0;
            setBetsHistoryControlsPending(false, actionButton);
          }
        }

        function syncBetsHistoryToggleA11y() {
          const expanded = Boolean(betsHistoryState.expanded);
          const buttons = document.querySelectorAll('[data-action="toggle-bets-history"]');
          buttons.forEach((btn) => {
            if (!(btn instanceof HTMLElement)) return;
            const stateText = expanded ? 'Свернуть историю ставок' : 'Открыть историю ставок';
            btn.setAttribute('aria-controls', 'bets-history-panel');
            btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            btn.setAttribute('aria-label', stateText);
            btn.setAttribute('title', stateText);
          });
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
                <button type="button" class="btn-secondary btn-sm" data-action="toggle-bets-history" aria-label="${escapeHtml(betsHistoryState.expanded ? 'Свернуть историю ставок' : 'Открыть историю ставок')}" title="${escapeHtml(betsHistoryState.expanded ? 'Свернуть историю ставок' : 'Открыть историю ставок')}" aria-controls="bets-history-panel" aria-expanded="${betsHistoryState.expanded ? 'true' : 'false'}">
                  ${escapeHtml(label)}${total !== null ? ` (${total})` : ''}
                </button>
                <button type="button" class="btn-secondary btn-sm" data-action="bets-open-all-time" aria-label="Показать историю ставок за всё время" title="Показать историю ставок за всё время">
                  Всё время
                </button>
              </div>
            </div>
          `;
          syncBetsHistoryToggleA11y();
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
                  <button type="button" class="btn-secondary btn-sm" data-action="bets-refresh" aria-label="Обновить историю ставок" title="Обновить историю ставок">🔄</button>
                  <button type="button" class="btn-secondary btn-sm" data-action="toggle-bets-history" aria-label="Свернуть историю ставок" title="Свернуть историю ставок" aria-controls="bets-history-panel" aria-expanded="true">✕</button>
                </div>
              </div>

              <div class="row mt-3">
                <div class="col-md-3">
                  <label class="form-label" for="bets-market">Рынок</label>
                  <select id="bets-market" class="form-select">
                    <option value="all">все</option>
                    <option value="1x2">1X2</option>
                    <option value="totals">TOTAL</option>
                  </select>
                </div>
                <div class="col-md-3">
                  <label class="form-label" for="bets-status">Статус</label>
                  <select id="bets-status" class="form-select">
                    <option value="">все</option>
                    <option value="WIN">Победа</option>
                    <option value="LOSS">Поражение</option>
                    <option value="PENDING">Ожидает</option>
                    <option value="VOID">Возврат</option>
                  </select>
                </div>
                <div class="col-md-3">
                  <label class="form-label" for="bets-sort">Сортировка</label>
                  <select id="bets-sort" class="form-select">
                    <option value="kickoff_desc">старт ↓</option>
                    <option value="ev_desc">EV ↓</option>
                    <option value="profit_desc">прибыль ↓</option>
                    <option value="signal_desc">сигнал ↓</option>
                  </select>
                </div>
                <div class="col-md-3">
                  <label class="form-label" for="bets-limit">Размер страницы</label>
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
                  <label class="form-label" for="bets-team">Команда (опц.)</label>
                  <input id="bets-team" class="form-input" placeholder="например Arsenal">
                </div>
                <div class="col-md-3">
                  <div class="form-label">Опции</div>
                  <div class="d-flex align-items-center gap-md">
                    <label class="small text-muted" for="bets-settled-only"><input id="bets-settled-only" type="checkbox"> Завершенные</label>
                    <label class="small text-muted" for="bets-all-time"><input id="bets-all-time" type="checkbox"> За всё время</label>
                  </div>
                </div>
                <div class="col-md-3">
                  <div class="form-label" aria-hidden="true">&nbsp;</div>
                  <button type="button" class="btn btn-primary" data-action="bets-apply">Применить</button>
                </div>
              </div>

              <div class="d-flex justify-content-between align-items-center mt-2">
                <div id="bets-history-task" class="text-muted small" role="status" aria-live="polite" aria-atomic="true"></div>
                <div class="btn-group">
                  <button type="button" class="btn-secondary btn-sm" data-action="bets-load-all">Загрузить все (макс 5000)</button>
                  <button type="button" class="btn-secondary btn-sm" data-action="bets-export-csv">Экспорт CSV</button>
                </div>
              </div>

              <div id="bets-history-summary" class="text-muted small mt-3" role="status" aria-live="polite" aria-atomic="true"></div>
              <div id="bets-history-result" class="mt-3" role="region" aria-label="Результаты истории ставок"></div>
              <div id="bets-history-page-hint" class="text-muted small mt-2" role="status" aria-live="polite" aria-atomic="true"></div>

              <div class="d-flex justify-content-between align-items-center mt-3">
                <button type="button" class="btn-secondary btn-sm" data-action="bets-prev" aria-label="Предыдущая страница истории ставок" title="Предыдущая страница истории ставок">← Назад</button>
                <button type="button" class="btn-secondary btn-sm" data-action="bets-next" aria-label="Следующая страница истории ставок" title="Следующая страница истории ставок">Далее →</button>
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
          syncBetsHistoryToggleA11y();
          if (betsHistoryInFlight) setBetsHistoryControlsPending(true);
          else updateBetsHistoryPagerAvailability();
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
          updateBetsHistoryPagerAvailability();
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
              <table class="table table-sm table-striped bets-history-table">
                <thead class="table-dark">
                  <tr>
                    <th>Дата</th>
                    <th>Матч</th>
                    <th>Выбор</th>
                    <th>Коэфф.</th>
                    <th>Статус</th>
                    <th class="text-end">Прибыль</th>
                    <th>Лига</th>
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
                    const statusText = translateBetStatus(statusRaw);
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
                      <tr
                        class="cursor-pointer"
                        data-action="fixture-details"
                        data-fixture-id="${escapeHtml(fid)}"
                        title="Открыть детали матча"
                        role="button"
                        tabindex="0"
                        aria-keyshortcuts="Enter Space"
                        aria-label="${escapeHtml(`Открыть детали матча: ${matchText}`)}"
                      >
                        <td data-label="Дата">${escapeHtml(kickoffText)}</td>
                        <td class="match-cell" data-label="Матч" title="${escapeHtml(matchText)}">
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
                        <td class="text-truncate table-cell-truncate" data-label="Выбор" title="${escapeHtml(pickText)}">${escapeHtml(pickText)}</td>
                        <td data-label="Коэфф.">${escapeHtml(oddText)}</td>
                        <td data-label="Статус"><span class="badge bg-${escapeHtml(badge)}">${escapeHtml(statusText)}</span></td>
                        <td class="text-end ${escapeHtml(profitCls)} fw-bold" data-label="Прибыль">${escapeHtml(profitText)}</td>
                        <td class="league-cell" data-label="Лига" title="${escapeHtml(leagueText)}">
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

        async function loadBetsHistoryPage({ resetOffset = false, actionButton = null, notifyOnBusy = false } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return false;

          const resultEl = el('bets-history-result');
          const summaryEl = el('bets-history-summary');
          const previousTotal = betsHistoryState.total;
          const previousRowCount = betsHistoryState.lastPageRows;

          return runBetsHistoryTask(async () => {
            readBetsHistoryFiltersFromDom();
            if (resetOffset) betsHistoryState.offset = 0;
            scheduleUiStateSave();
            betsHistoryState.viewMode = 'page';

            const periodEl = el('bets-period-hint');
            if (periodEl) {
              periodEl.textContent = betsHistoryState.allTime ? 'Период: всё время' : `Период: последние ${getDashboardDays()} дней`;
            }

            setBetsHistoryTaskState('');
            if (resultEl) resultEl.innerHTML = '<p class="text-muted">Загрузка...</p>';
            if (summaryEl) summaryEl.textContent = '';

            const sp = buildBetsHistorySearchParams({ limit: betsHistoryState.limit, offset: betsHistoryState.offset });
            const { data, totalCount } = await apiFetchJsonWithTotal(`/api/v1/bets/history?${sp.toString()}`);
            const rows = Array.isArray(data) ? data : [];
            betsHistoryState.total = totalCount;
            betsHistoryState.lastPageRows = rows.length;

            renderBetsHistoryRows(rows);

            const total = Number.isFinite(totalCount) ? totalCount : null;
            const from = rows.length ? betsHistoryState.offset + 1 : 0;
            const to = betsHistoryState.offset + rows.length;
            if (summaryEl) {
              summaryEl.textContent = total !== null ? `Показано ${from}-${to} из ${total}` : `Показано ${rows.length}`;
            }
            updateBetsHistoryPagerAvailability();
          }, {
            actionButton,
            busyText: 'Загрузка…',
            notifyOnBusy,
            onError: (error) => {
              betsHistoryState.total = previousTotal;
              betsHistoryState.lastPageRows = previousRowCount;
              if (resultEl) resultEl.innerHTML = `<div class="alert alert-danger" role="alert">${escapeHtml(error?.message || 'Ошибка загрузки истории')}</div>`;
              if (summaryEl) summaryEl.textContent = '';
              setBetsHistoryTaskState('Не удалось загрузить историю', 'error');
              updateBetsHistoryPagerAvailability();
            },
          });
        }

        async function loadBetsHistoryAll({ maxRows = 5000, actionButton = null, notifyOnBusy = false } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return false;

          const resultEl = el('bets-history-result');
          const summaryEl = el('bets-history-summary');
          const previousViewMode = betsHistoryState.viewMode;
          const previousTotal = betsHistoryState.total;
          const previousRowCount = betsHistoryState.lastPageRows;

          return runBetsHistoryTask(async () => {
            readBetsHistoryFiltersFromDom();
            betsHistoryState.viewMode = 'all';
            betsHistoryState.lastPageRows = 0;
            scheduleUiStateSave();

            const periodEl = el('bets-period-hint');
            if (periodEl) {
              periodEl.textContent = betsHistoryState.allTime ? 'Период: всё время' : `Период: последние ${getDashboardDays()} дней`;
            }

            if (resultEl) resultEl.innerHTML = '<p class="text-muted">Загрузка (пакетами)...</p>';
            if (summaryEl) summaryEl.textContent = '';

            const key = betsHistoryCacheKey();
            if (betsHistoryState.cacheKey === key && Array.isArray(betsHistoryState.cacheRows) && betsHistoryState.cacheRows.length) {
              renderBetsHistoryRows(betsHistoryState.cacheRows);
              const total = betsHistoryState.cacheTotal;
              const shown = betsHistoryState.cacheRows.length;
              betsHistoryState.total = Number.isFinite(total) ? total : null;
              if (summaryEl) summaryEl.textContent = total !== null ? `Показано ${shown} из ${total}` : `Показано ${shown}`;
            } else {
              const { rows, totalCount, truncated } = await fetchBetsHistoryAll({
                maxRows,
                onProgress: ({ loaded, total }) => {
                  setBetsHistoryTaskState(total !== null ? `Загружено ${loaded} из ${total}...` : `Загружено ${loaded}...`, 'info');
                },
              });
              betsHistoryState.cacheKey = key;
              betsHistoryState.cacheRows = rows;
              betsHistoryState.cacheTotal = totalCount;
              betsHistoryState.cacheTruncated = truncated;
              betsHistoryState.total = Number.isFinite(totalCount) ? totalCount : null;

              renderBetsHistoryRows(rows);
              if (summaryEl) {
                const total = Number.isFinite(totalCount) ? totalCount : null;
                const note = truncated ? ` (лимит ${clampInt(maxRows, 1, 20000, 5000)})` : '';
                summaryEl.textContent = total !== null ? `Показано ${rows.length} из ${total}${note}` : `Показано ${rows.length}${note}`;
              }
            }

            setBetsHistoryTaskState(betsHistoryState.cacheTruncated ? '⚠️ Ограничено лимитом выгрузки' : '', betsHistoryState.cacheTruncated ? 'warning' : 'info');
            updateBetsHistoryPagerAvailability();
          }, {
            actionButton,
            busyText: 'Загрузка…',
            notifyOnBusy,
            onError: (error) => {
              betsHistoryState.viewMode = previousViewMode;
              betsHistoryState.total = previousTotal;
              betsHistoryState.lastPageRows = previousRowCount;
              if (resultEl) resultEl.innerHTML = `<div class="alert alert-danger" role="alert">${escapeHtml(error?.message || 'Ошибка загрузки истории')}</div>`;
              if (summaryEl) summaryEl.textContent = '';
              setBetsHistoryTaskState('Не удалось загрузить историю', 'error');
              updateBetsHistoryPagerAvailability();
            },
          });
        }

        async function exportBetsHistoryCsv({ maxRows = 5000, actionButton = null, notifyOnBusy = true } = {}) {
          const panel = el('bets-history-panel');
          if (!panel || panel.classList.contains('is-hidden')) return false;

          return runBetsHistoryTask(async () => {
            readBetsHistoryFiltersFromDom();
            scheduleUiStateSave();

            const key = betsHistoryCacheKey();
            let rows = betsHistoryState.cacheKey === key && Array.isArray(betsHistoryState.cacheRows) ? betsHistoryState.cacheRows : null;
            let totalCount = betsHistoryState.cacheKey === key ? betsHistoryState.cacheTotal : null;

            if (!rows) {
              setBetsHistoryTaskState('Готовлю CSV (пакетная загрузка)...', 'info');
              const res = await fetchBetsHistoryAll({
                maxRows,
                onProgress: ({ loaded, total }) => {
                  setBetsHistoryTaskState(total !== null ? `CSV: загружено ${loaded} из ${total}...` : `CSV: загружено ${loaded}...`, 'info');
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
            setBetsHistoryTaskState('✅ CSV готов', 'success');
          }, {
            actionButton,
            busyText: 'Экспорт…',
            notifyOnBusy,
            onError: () => {
              setBetsHistoryTaskState('Не удалось сформировать CSV', 'error');
            },
          });
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
            syncBetsHistoryToggleA11y();
            return;
          }

          setHidden(panel, false);
          renderBetsHistoryPanel();
          await loadBetsHistoryPage();
          syncBetsHistoryToggleA11y();
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
              text = anyAge
                ? `Обновление: ⚠️ последний ${String(lastAny.status).toUpperCase()} • ${anyAge} назад`
                : 'Обновление: ⚠️ последний запуск с ошибкой';
              if (lastError) text += ` • ошибка ${lastError}`;
            }

            const max = freshnessData?.max || {};
            const titleLines = [];
            if (lastOkTs) titleLines.push(`sync_data ok: ${String(lastOkTs)} (${ageLabel(lastOkTs) || '—'} назад)`);
            if (lastAny && lastAnyTs) titleLines.push(`sync_data последний: ${String(lastAny.status || '—')} в ${String(lastAnyTs)} (${ageLabel(lastAnyTs) || '—'} назад)`);
            if (lastError) titleLines.push(`sync_data ошибка: ${lastError}`);
            const maxPairs = [
              ['fixtures_updated_at', 'матчи'],
              ['odds_fetched_at', 'odds'],
              ['standings_updated_at', 'таблица'],
              ['injuries_created_at', 'травмы'],
              ['match_indices_updated_at', 'индексы'],
              ['predictions_created_at', 'прогнозы'],
              ['predictions_totals_created_at', 'тоталы'],
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
                const matchDisplayRaw = homeName && awayName ? `${homeName} vs ${awayName}` : 'Неизвестный матч';
                const matchDisplay = escapeHtml(matchDisplayRaw);
                const dateDisplay = escapeHtml(new Date(bet.kickoff || bet.created_at).toLocaleDateString('ru-RU'));
                const statusRaw = String(bet.status || '—');
                const statusText = escapeHtml(translateBetStatus(statusRaw));
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
                  <div
                    class="activity-item d-flex justify-content-between align-items-center py-2 border-bottom cursor-pointer"
                    data-action="fixture-details"
                    data-fixture-id="${escapeHtml(fid)}"
                    title="Открыть детали матча"
                    role="button"
                    tabindex="0"
                    aria-keyshortcuts="Enter Space"
                    aria-label="${escapeHtml(`Открыть детали матча: ${matchDisplayRaw}`)}"
                  >
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
                        : '<div class="text-muted small">Ожидает</div>'}
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

        function renderQualityTable(title, rows, columns, options = {}) {
          const showTitle = options?.showTitle !== false;
          if (!rows || rows.length === 0) {
            if (showTitle && title) return `<div class="text-muted mt-2">${escapeHtml(title)}: нет данных</div>`;
            return '<div class="text-muted mt-2">Нет данных</div>';
          }
          return `
            <div class="table-responsive mt-2">
              ${showTitle && title ? `<div class="small text-muted mb-1">${escapeHtml(title)}</div>` : ''}
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
              label: 'Лига',
              format: (row) => row?.league_name ? String(row.league_name) : `лига ${row?.league_id ?? '—'}`,
            },
            { label: 'Ставки', format: (row) => String(row?.bets ?? 0) },
            { label: 'ROI', format: (row) => formatPercent100(row?.roi, 1) },
            { label: 'Винрейт', format: (row) => formatPercent100(row?.win_rate, 1) },
            { label: 'CLV', format: (row) => (Number(row?.clv_cov || 0) > 0 ? formatPercent100(row?.clv_avg_pct, 1) : '—') },
            { label: 'CLV покрытие', format: (row) => `${row?.clv_cov ?? 0} (${formatPercent100(row?.clv_cov_pct, 1)})` },
          ];

          const bucketColumns = [
            { label: 'Бакет', format: (row) => String(row?.key ?? '—') },
            { label: 'Ставки', format: (row) => String(row?.bets ?? 0) },
            { label: 'ROI', format: (row) => formatPercent100(row?.roi, 1) },
            { label: 'Винрейт', format: (row) => formatPercent100(row?.win_rate, 1) },
            { label: 'CLV', format: (row) => (Number(row?.clv_cov || 0) > 0 ? formatPercent100(row?.clv_avg_pct, 1) : '—') },
          ];

          const binColumns = [
            { label: 'Бин', format: (row) => String(row?.bin ?? '—') },
            { label: 'Ставки', format: (row) => String(row?.bets ?? 0) },
            { label: 'Средняя вер.', format: (row) => formatPercent01(row?.avg_prob, 1) },
            { label: 'Винрейт', format: (row) => formatPercent01(row?.win_rate, 1) },
          ];

          const shadowColumns = [
            { label: 'Сценарий', format: (row) => String(row?.label || row?.id || '—') },
            { label: 'Ставки', format: (row) => String(row?.summary?.bets ?? 0) },
            { label: 'ROI', format: (row) => formatPercent100(row?.summary?.roi, 1) },
            { label: 'CLV', format: (row) => (Number(row?.summary?.clv_cov || 0) > 0 ? formatPercent100(row?.summary?.clv_avg_pct, 1) : '—') },
            { label: 'CLV покрытие', format: (row) => `${row?.summary?.clv_cov ?? 0} (${formatPercent100(row?.summary?.clv_cov_pct, 1)})` },
            { label: 'ΔROI', format: (row) => formatSignedPercent100(row?.delta?.roi, 1) },
            { label: 'ΔCLV', format: (row) => formatSignedPercent100(row?.delta?.clv_avg_pct, 1) },
          ];

          const shadowBlock = shadowFilters.length
            ? renderQualityTable('Теневые фильтры (сценарии)', shadowFilters, shadowColumns)
            : '';

          const leaguesBlock = byLeague.length
            ? `
              <details class="quality-leagues-details mt-2">
                <summary class="small">Лиги (топ 8)</summary>
                ${renderQualityTable('', byLeague, leagueColumns, { showTitle: false })}
              </details>
            `
            : '<div class="text-muted mt-2">Лиги (топ 8): нет данных</div>';

          const detailsBlock = (byOdds.length || byTime.length || bins.length || shadowFilters.length) ? `
            <details class="mt-2">
              <summary class="small">Детали: коэффициенты, время, калибровка</summary>
              ${renderQualityTable('Бакеты коэффициентов', byOdds, bucketColumns)}
              ${renderQualityTable('Время до матча', byTime, bucketColumns)}
              ${renderQualityTable('Бины калибровки', bins, binColumns)}
              ${shadowBlock}
            </details>
          ` : '';

          const roiRaw = hasBets ? Number(summary.roi ?? 0) : null;
          const clvAvgRaw = clvCov > 0 ? Number(summary.clv_avg_pct ?? 0) : null;
          const kpiRows = [
            { label: 'ROI', value: hasBets ? formatPercent100(summary.roi, 1) : '—', tone: roiRaw === null ? '' : (roiRaw > 0 ? 'is-positive' : roiRaw < 0 ? 'is-negative' : '') },
            { label: 'Винрейт', value: hasBets ? formatPercent100(summary.win_rate, 1) : '—', tone: '' },
            { label: 'Ср. коэфф.', value: hasBets ? formatFixed(summary.avg_odd, 2) : '—', tone: '' },
            { label: 'CLV средний', value: clvAvgText, tone: clvAvgRaw === null ? '' : (clvAvgRaw > 0 ? 'is-positive' : clvAvgRaw < 0 ? 'is-negative' : '') },
            { label: 'CLV покрытие', value: clvCovText, tone: '' },
            { label: 'Brier / LogLoss', value: hasBets ? `${formatFixed(calib.brier, 3)} / ${formatFixed(calib.logloss, 3)}` : '—', tone: '' },
          ];

          return `
            <div class="border rounded p-3">
              <div class="d-flex justify-content-between align-items-center mb-1">
                <h4 class="mb-0">${escapeHtml(label)}</h4>
                <span class="badge bg-${escapeHtml(badgeClass)}">${bets} ставок</span>
              </div>
              <div class="quality-kpi-grid">
                ${kpiRows.map((row) => `
                  <div class="quality-kpi-item ${escapeHtml(row.tone || '')}">
                    <div class="quality-kpi-label">${escapeHtml(row.label)}</div>
                    <div class="quality-kpi-value">${escapeHtml(String(row.value))}</div>
                  </div>
                `).join('')}
              </div>
              ${leaguesBlock}
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
            alerts.push('CLV покрытие 0% (нет pre-kickoff снапшотов)');
            level = Math.max(level, 1);
          } else if (clvCov > 0 && clvCov < 30) {
            alerts.push(`CLV покрытие низкое (${formatPercent100(clvCov, 1)})`);
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
            metaParts.push(`букмекер ${String(report.bookmaker_id)}`);
          }
          if (payload?.cron) metaParts.push(`cron ${String(payload.cron)}`);
          if (payload?.cache_ttl_seconds) metaParts.push(`ttl ${Math.round(Number(payload.cache_ttl_seconds) / 3600)}ч`);
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

          try {
            const nowTs = Date.now();
            const lookbackMs = 8 * 60 * 60 * 1000;
            const dateFrom = new Date(nowTs - lookbackMs).toISOString();
            const dateTo = new Date(nowTs + 7 * 24 * 60 * 60 * 1000).toISOString();
            const params = new URLSearchParams({
              date_from: dateFrom,
              date_to: dateTo,
              limit: '200',
              offset: '0',
              sort: 'kickoff_desc',
            });

            const [picks1x2Res, picksTotalsRes] = await Promise.allSettled([
              apiFetchJson(`/api/v1/picks?${params.toString()}`),
              apiFetchJson(`/api/v1/picks/totals?${params.toString()}`),
            ]);

            const picks1x2 = picks1x2Res.status === 'fulfilled' ? picks1x2Res.value : [];
            const picksTotals = picksTotalsRes.status === 'fulfilled' ? picksTotalsRes.value : [];
            const hasPartialFailure = picks1x2Res.status === 'rejected' || picksTotalsRes.status === 'rejected';
            if (hasPartialFailure && !livePartialFetchWarned) {
              livePartialFetchWarned = true;
              notify('Часть лайв-пиков временно недоступна. Показаны доступные данные.', 'warning');
            } else if (!hasPartialFailure) {
              livePartialFetchWarned = false;
            }
            if (picks1x2Res.status === 'rejected' && picksTotalsRes.status === 'rejected') {
              throw (picks1x2Res.reason || picksTotalsRes.reason || new Error('Ошибка загрузки лайв-пиков'));
            }

            const merged = [
              ...(Array.isArray(picks1x2) ? picks1x2.map((p) => ({ ...p, market: '1X2' })) : []),
              ...(Array.isArray(picksTotals) ? picksTotals.map((p) => ({ ...p, market: 'TOTAL' })) : []),
            ];
            const finalStatuses = new Set(['FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO']);
            const liveStatuses = new Set(['LIVE', '1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT']);

            const marketFilter = String(liveState.market || 'all').toLowerCase();
            const needle = String(liveState.league || '').trim().toLowerCase();
            const filtered = merged.filter((p) => {
              const status = String(p?.fixture_status || '').toUpperCase();
              if (finalStatuses.has(status)) return false;
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
                fixture_minute: pick.fixture_minute ?? null,
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
              if (!existing.fixture_status && pick.fixture_status) existing.fixture_status = pick.fixture_status;
              if (existing.fixture_minute === null || existing.fixture_minute === undefined) {
                if (pick.fixture_minute !== null && pick.fixture_minute !== undefined) existing.fixture_minute = pick.fixture_minute;
              }
              existing.picks.push(pick);
              groups.set(key, existing);
            }

            const fixtures = Array.from(groups.values()).sort(
              (a, b) => new Date(a.kickoff || 0).getTime() - new Date(b.kickoff || 0).getTime(),
            );

            const kickoffTs = (raw) => {
              const ts = raw ? new Date(raw).getTime() : NaN;
              return Number.isFinite(ts) ? ts : NaN;
            };
            const fixtureStatus = (fixture) => String(fixture?.fixture_status || '').toUpperCase();
            const nsLiveFallbackWindowMs = 5 * 60 * 60 * 1000;
            const isLikelyLiveByKickoff = (fixture) => {
              const status = fixtureStatus(fixture);
              if (!['NS', 'UNK', 'TBD'].includes(status)) return false;
              const ts = kickoffTs(fixture?.kickoff);
              return Number.isFinite(ts) && ts <= nowTs && ts >= (nowTs - nsLiveFallbackWindowMs);
            };
            const isLiveFixture = (fixture) => liveStatuses.has(fixtureStatus(fixture)) || isLikelyLiveByKickoff(fixture);
            const isUpcomingFixture = (fixture) => {
              const status = fixtureStatus(fixture);
              if (finalStatuses.has(status) || liveStatuses.has(status)) return false;
              const ts = kickoffTs(fixture?.kickoff);
              return Number.isFinite(ts) && ts >= nowTs;
            };

            const liveFixtures = fixtures
              .filter((fixture) => isLiveFixture(fixture))
              .sort((a, b) => {
                const minuteA = Number.isFinite(Number(a?.fixture_minute)) ? Number(a.fixture_minute) : -1;
                const minuteB = Number.isFinite(Number(b?.fixture_minute)) ? Number(b.fixture_minute) : -1;
                if (minuteA !== minuteB) return minuteB - minuteA;
                return kickoffTs(b?.kickoff) - kickoffTs(a?.kickoff);
              });
            const upcomingFixtures = fixtures
              .filter((fixture) => isUpcomingFixture(fixture))
              .sort((a, b) => kickoffTs(a?.kickoff) - kickoffTs(b?.kickoff));

            const shownLiveFixtures = liveFixtures.slice(0, 8);
            const shownUpcomingFixtures = upcomingFixtures.slice(0, 8);
            const totalLiveMatches = liveFixtures.length;
            const totalUpcomingMatches = upcomingFixtures.length;
            const picksCount = filtered.length;
            const livePicksCount = liveFixtures.reduce((acc, fixture) => acc + (Array.isArray(fixture?.picks) ? fixture.picks.length : 0), 0);
            const upcomingPicksCount = upcomingFixtures.reduce((acc, fixture) => acc + (Array.isArray(fixture?.picks) ? fixture.picks.length : 0), 0);

            if (!container) return;

            const renderFixtureCards = (fixtureList, emptyText) => {
              if (!Array.isArray(fixtureList) || fixtureList.length === 0) {
                return `<p class="text-center text-muted">${escapeHtml(emptyText)}</p>`;
              }
              return `
                <div class="picks-grid">
                  ${fixtureList.map((fixture) => {
                    const fallbackNames = splitTeamsText(fixture.teams);
                    const homeName = fixture.home || fallbackNames.home || 'Домашняя';
                    const awayName = fixture.away || fallbackNames.away || 'Гостевая';
                    const kickoffText = fixture.kickoff
                      ? escapeHtml(new Date(fixture.kickoff).toLocaleString('ru-RU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }))
                      : 'Время уточняется';
                    const leagueText = fixture.league ? escapeHtml(fixture.league) : '—';
                    const statusMetaBase = formatFixtureStatusLabel(fixture.fixture_status, fixture.fixture_minute);
                    const statusMeta = { ...statusMetaBase };
                    if (!statusMeta.isLive && isLikelyLiveByKickoff(fixture)) {
                      statusMeta.isLive = true;
                      statusMeta.label = "LIVE*";
                    }
                    const statusText = statusMeta.label ? escapeHtml(statusMeta.label) : '';
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
                      <div
                        class="fixture-card cursor-pointer"
                        data-action="fixture-details"
                        data-fixture-id="${escapeHtml(String(fixture.fixture_id))}"
                        title="Открыть детали матча"
                        role="button"
                        tabindex="0"
                        aria-keyshortcuts="Enter Space"
                        aria-label="${escapeHtml(`Открыть детали матча: ${homeName} vs ${awayName}`)}"
                      >
                        <div class="fixture-head">
                          <div class="fixture-league">
                            ${leagueLogo}
                            <span class="league-name">${leagueText}</span>
                          </div>
                          <div class="fixture-meta">
                            <span class="meta-pill">${kickoffText}</span>
                            ${statusText ? `<span class="meta-pill${statusMeta.isLive ? ' meta-pill-live' : ''}">${statusText}</span>` : ''}
                            ${scoreText ? `<span class="meta-pill meta-score">${scoreText}</span>` : ''}
                          </div>
                          <div class="fixture-actions">
                            <span class="pill pill-primary">${escapeHtml(String(picksSorted.length))} пиков</span>
                            <button type="button" class="btn-secondary btn-sm" data-action="publish-open" data-fixture-id="${escapeHtml(String(fixture.fixture_id))}" title="Превью публикации" aria-label="Открыть превью публикации">📣</button>
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
                            const primaryMetrics = [
                              `@${oddText}`,
                              `EV ${evText}`,
                            ];
                            const secondaryMetrics = [];
                            if (confText !== '—') secondaryMetrics.push(`Вер ${confText}`);
                            if (valueText !== null) secondaryMetrics.push(`Вэлью ${valueText}`);
                            if (signalText !== null) secondaryMetrics.push(`Сиг ${signalText}`);
                            const secondaryHint = secondaryMetrics.length ? `Доп.: ${secondaryMetrics.join(' • ')}` : '';
                            const metricTitleAttr = secondaryHint ? ` title="${escapeHtml(secondaryHint)}"` : '';

                            return `
                              <div class="pick-line">
                                <div class="pick-line-left">
                                  <span class="badge bg-${marketBadge}">${escapeHtml(marketLabel)}</span>
                                  <span class="fw-bold text-truncate table-cell-truncate" title="${pickLabel}">${pickLabel}</span>
                                </div>
                                <div class="pick-line-right">
                                  <div class="metric-line"${metricTitleAttr}>${escapeHtml(primaryMetrics.join(' • '))}</div>
                                </div>
                              </div>
                            `;
                          }).join('')}
                        </div>
                      </div>
                    `;
                  }).join('')}
                </div>
              `;
            };

            container.innerHTML = `
            <div class="live-summary">
              <div class="live-summary-left">
                <div class="live-title">Лайв и ближайшие матчи</div>
                <div class="live-subtitle">Следующие 7 дней • поток обновляется автоматически</div>
              </div>
              <div class="live-summary-right">
                <span class="badge bg-danger">Лайв: ${totalLiveMatches}</span>
                <span class="badge bg-primary">Ближайшие: ${totalUpcomingMatches}</span>
                <span class="badge bg-secondary">${picksCount} пиков</span>
              </div>
            </div>

            <div class="live-filters">
              <div class="row">
                <div class="col-md-3">
                  <label class="form-label" for="live-market">Рынок</label>
                  <select id="live-market" class="form-select">
                    <option value="all">все</option>
                    <option value="1x2">1X2</option>
                    <option value="totals">TOTAL</option>
                  </select>
                </div>
                <div class="col-md-6">
                  <label class="form-label" for="live-search">Поиск (лига/команды)</label>
                  <input id="live-search" class="form-input" placeholder="например Premier / Arsenal">
                </div>
                <div class="col-md-3">
                  <div class="form-label" aria-hidden="true">&nbsp;</div>
                  <div class="btn-group">
                    <button type="button" class="btn-secondary btn-sm" data-action="live-apply">Применить</button>
                    <button type="button" class="btn-secondary btn-sm" data-action="live-reset">Сброс</button>
                  </div>
                </div>
              </div>
            </div>

            <div class="live-sections">
              <section class="live-section">
                <div class="live-section-head">
                  <div class="live-section-title">Лайв сейчас</div>
                  <div class="live-section-subtitle">Матчи в игре: ${totalLiveMatches} • Пиков: ${livePicksCount}</div>
                </div>
                ${renderFixtureCards(shownLiveFixtures, 'Сейчас нет матчей в лайве')}
              </section>
              <section class="live-section">
                <div class="live-section-head">
                  <div class="live-section-title">Ближайшие матчи</div>
                  <div class="live-section-subtitle">Ещё не начались: ${totalUpcomingMatches} • Пиков: ${upcomingPicksCount}</div>
                </div>
                ${renderFixtureCards(shownUpcomingFixtures, 'Нет ближайших матчей с пиками')}
              </section>
            </div>
          `;
            applyLogoFallbacks(container);

            const marketEl = el('live-market');
            if (marketEl) marketEl.value = liveState.market;
            const searchEl = el('live-search');
            if (searchEl) searchEl.value = liveState.league;
          } catch (e) {
            const authHandled = handleScopedApiError(e, { showGenericNotify: false });
            if (!authHandled && container) {
              container.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'Ошибка загрузки лайв-пиков')}</div>`;
            }
          }
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
          { id: 'INFO_BTTS', label: 'Обе забьют (BTTS)', selections: ['BTTS_YES', 'BTTS_NO'] },
          { id: 'INFO_OU_1_5', label: 'Тотал 1.5 (O/U)', selections: ['OVER_1_5', 'UNDER_1_5'] },
          { id: 'INFO_OU_2_5', label: 'Тотал 2.5 (O/U)', selections: ['OVER_2_5', 'UNDER_2_5'] },
          { id: 'INFO_OU_3_5', label: 'Тотал 3.5 (O/U)', selections: ['OVER_3_5', 'UNDER_3_5'] },
        ];

        function infoSelectionShort(sel) {
          const raw = String(sel || '');
          if (!raw) return '—';
          if (raw.startsWith('OVER_')) return `O${raw.replace('OVER_', '').replace('_', '.')}`;
          if (raw.startsWith('UNDER_')) return `U${raw.replace('UNDER_', '').replace('_', '.')}`;
          if (raw === 'BTTS_YES') return 'Да';
          if (raw === 'BTTS_NO') return 'Нет';
          if (raw.startsWith('BTTS_')) return raw.replace('BTTS_', '');
          return raw.replaceAll('_', ' ');
        }

        function infoTier(prob) {
          if (!Number.isFinite(prob)) return { label: '—', cls: 'info-tier-muted', bar: 'info-bar-muted' };
          if (prob >= 0.66) return { label: 'сильный', cls: 'info-tier-strong', bar: 'info-bar-strong' };
          if (prob >= 0.58) return { label: 'уклон', cls: 'info-tier-lean', bar: 'info-bar-lean' };
          if (prob >= 0.53) return { label: 'край', cls: 'info-tier-edge', bar: 'info-bar-edge' };
          return { label: 'близко', cls: 'info-tier-close', bar: 'info-bar-close' };
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
          if (raw === 'HOME_WIN') return home || 'Домашняя';
          if (raw === 'AWAY_WIN') return away || 'Гостевая';
          if (raw === 'DRAW') return 'Ничья';
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
          const parts = [`Пик: ${label}`];
          if (prob !== null) parts.push(formatPercent01(prob, 1));
          if (odd !== null) parts.push(`@${odd.toFixed(2)}`);
          if (ev !== null) parts.push(`EV ${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%`);
          const market = decision.market ? String(decision.market) : '';
          if (market.startsWith('INFO_')) parts.push('без коэффициентов');
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
          if (picksPanel) {
            setHidden(picksPanel, next !== 'picks');
            picksPanel.setAttribute('role', 'tabpanel');
            picksPanel.setAttribute('aria-labelledby', 'info-tab-picks');
            picksPanel.setAttribute('aria-hidden', next === 'picks' ? 'false' : 'true');
          }
          if (statsPanel) {
            setHidden(statsPanel, next !== 'stats');
            statsPanel.setAttribute('role', 'tabpanel');
            statsPanel.setAttribute('aria-labelledby', 'info-tab-stats');
            statsPanel.setAttribute('aria-hidden', next === 'stats' ? 'false' : 'true');
          }
          document.querySelectorAll('.info-tab').forEach((btn) => {
            const btnTab = btn?.dataset?.tab || 'picks';
            const isActive = btnTab === next;
            btn.classList.toggle('is-active', isActive);
            btn.setAttribute('role', 'tab');
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
            btn.setAttribute('tabindex', isActive ? '0' : '-1');
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
                  : 'Время уточняется';
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
                    label: home || 'Домашняя',
                    prob: numOrNull(map1x2?.HOME_WIN?.prob),
                    odd: numOrNull(map1x2?.HOME_WIN?.odd),
                    ev: numOrNull(map1x2?.HOME_WIN?.ev),
                  },
                  {
                    selection: 'DRAW',
                    label: 'Ничья',
                    prob: numOrNull(map1x2?.DRAW?.prob),
                    odd: numOrNull(map1x2?.DRAW?.odd),
                    ev: numOrNull(map1x2?.DRAW?.ev),
                  },
                  {
                    selection: 'AWAY_WIN',
                    label: away || 'Гостевая',
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
                  <div
                    class="fixture-card info-card cursor-pointer"
                    data-action="fixture-details"
                    data-fixture-id="${escapeHtml(String(row.fixture_id))}"
                    role="button"
                    tabindex="0"
                    aria-keyshortcuts="Enter Space"
                    aria-label="${escapeHtml(`Открыть детали матча: ${home} vs ${away}`)}"
                  >
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
                      ${renderInfoPrimaryBlock('Тотал 2.5', dt, itemsTotal, bestTotal.sel, picksMetaTotal)}
                    </div>
                    <details class="info-markets-wrap">
                      <summary class="small">Доп. рынки</summary>
                      <div class="info-markets">
                        ${INFO_MARKETS.map((m) => renderInfoMarketBlock(m, decisions[m.id])).join('')}
                      </div>
                    </details>
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
                    <th>Ставки</th>
                    <th>Винрейт</th>
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
              const upcomingNote = onlyUpcoming ? ' • только предстоящие' : '';
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
                    <h5 class="mb-0">🎛️ Управление пайплайном</h5>
                  </div>
                  <div class="card-body">
                    <div class="job-controls job-controls-primary">
                      <button type="button" class="btn btn-danger" data-action="run-job" data-job="full">🚀 Полный пайплайн</button>
                      <button type="button" class="btn btn-success" data-action="run-job" data-job="maintenance">🧹 Обслуживание</button>
                    </div>
                    <details class="job-stage-details mt-2">
                      <summary class="small">Точечные этапы пайплайна</summary>
                      <div class="job-controls job-controls-stages mt-2">
                        <button type="button" class="btn btn-primary" data-action="run-job" data-job="sync_data">📥 Синхронизация данных</button>
                        <button type="button" class="btn btn-info" data-action="run-job" data-job="compute_indices">📊 Расчет индексов</button>
                        <button type="button" class="btn btn-warning" data-action="run-job" data-job="build_predictions">🔮 Расчет прогнозов</button>
                        <button type="button" class="btn btn-success" data-action="run-job" data-job="evaluate_results">📈 Оценка результатов</button>
                      </div>
                    </details>
                    <div class="small text-muted mt-2">
                      <code>run-now</code> ограничен по частоте: повторный запуск может вернуться как «пропуск».
                    </div>
                    <div id="job-execution-log" class="mt-3" role="log" aria-live="polite" aria-atomic="false" aria-relevant="additions text"></div>
                  </div>
                </div>
              </div>
            </div>

            <div class="row">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header">
                    <h6 class="mb-0">⚡ Статус</h6>
                  </div>
                  <div class="card-body">
                    <div class="job-status-grid">
                      ${[['full', pipelineRow], ...JOB_NAMES.map((n) => [n, jobs[n] || null])].map(([name, row]) => {
                        const label = formatStatusLabel(row?.status);
                        const nameText = escapeHtml(formatJobLabel(name));
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
                    <h6 class="mb-0">📋 Последние запуски</h6>
                  </div>
                  <div class="card-body">
                    ${Array.isArray(runs) && runs.length ? `
                      <div class="recent-jobs">
                        ${runs.slice(0, 7).map((job) => {
                          const jobName = escapeHtml(formatJobLabel(job.job_name || '—'));
                          const statusRaw = String(job.status || '—');
                          const statusText = escapeHtml(translateRunStatus(statusRaw));
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
                              const errText = errors ? `, ошибок ${errors}` : '';
                              return `API промахи ${misses}/${reqs}${errText}`;
                            })()
                            : '';
                          const skipReason = String(skipReasonRaw || '').trim();
                          const skipInfo = skipped ? `ПРОПУСК${skipReason ? ` ${skipReason}` : ''}` : '';
                          const errorInfo = statusRaw.toLowerCase() === 'failed'
                            ? compactError(job?.error || '', 140)
                            : '';
                          const parts = [startedAtRaw];
                          if (apiInfo) parts.push(apiInfo);
                          if (skipInfo) parts.push(skipInfo);
                          if (errorInfo) parts.push(`ошибка ${errorInfo}`);
                          const startedAt = escapeHtml(parts.join(' • '));
                          return `
                            <div class="job-item">
                              <div class="job-name">${jobName}</div>
                              <div class="job-time ${quotaExhausted ? 'text-danger' : ''}">${startedAt}</div>
                              <div class="job-status ${jobRunStatusClass(statusRaw)}">${statusText}</div>
                            </div>
                          `;
                        }).join('')}
                      </div>
                    ` : '<p class="text-muted">Нет недавних запусков</p>'}
                  </div>
                </div>
              </div>
            </div>
          `;
        }

        async function runJob(jobType, actionButton = null) {
          const logDiv = el('job-execution-log');
          const jobLabel = formatJobLabel(jobType);
          setRunJobButtonsPending(true, actionButton);
          if (logDiv) {
            logDiv.innerHTML = `
              <div class="alert alert-info">
                <strong>🚀 Запускаю ${escapeHtml(jobLabel)}...</strong>
                <div class="spinner-border spinner-border-sm ms-2" aria-hidden="true"></div>
                <span class="sr-only">Выполняется запуск задания</span>
              </div>
            `;
          }

          try {
            const res = await apiFetch(`/api/v1/run-now?job=${encodeURIComponent(jobType)}`, {
              method: 'POST',
              headers: { 'X-Admin-Actor': 'ui' },
            });

            const payload = res.ok ? await res.json() : null;
            if (!res.ok) throw new Error(`run-now вернул ошибку: ${res.status}`);

            const started = payload?.started || jobType;
            const skipped = Boolean(payload?.skipped);
            const startedLabel = formatJobLabel(started);

            if (logDiv) {
              logDiv.innerHTML = skipped
                ? `<div class="alert alert-warning"><strong>⏳ Уже выполняется: ${escapeHtml(startedLabel)}</strong></div>`
                : `<div class="alert alert-success"><strong>✅ Запущено: ${escapeHtml(startedLabel)}</strong></div>`;
            }

            notify(skipped ? `⏳ Уже выполняется: ${startedLabel}` : `✅ Запущено: ${startedLabel}`, skipped ? 'warning' : 'success');
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
                  <strong>❌ Не удалось запустить задачу</strong>
                  <div class="mt-2"><small>${escapeHtml(e?.message || 'неизвестная ошибка')}</small></div>
                </div>
              `;
            }
            notify('❌ Не удалось запустить задачу', 'error');
          } finally {
            setRunJobButtonsPending(false, actionButton);
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
                <h5 class="mb-0">💾 Обзор базы данных</h5>
              </div>
              <div class="card-body">
                <div class="db-stats-grid">
                  ${[
                    { label: 'матчи', value: counts.fixtures, table: 'fixtures' },
                    { label: 'коэффициенты', value: counts.odds, table: 'odds' },
                    { label: 'индексы', value: counts.indices, table: 'match_indices' },
                    { label: 'прогнозы', value: counts.predictions, table: 'predictions' },
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
                        aria-label="Открыть таблицу ${escapeHtml(c.label)}"
                        title="Открыть таблицу ${escapeHtml(c.label)}"
                      >👁️ Открыть</button>
                    </div>
                  `).join('')}
                </div>

                <div class="mt-3">
                  <div class="row">
                    <div class="col-md-6">
                      <label class="form-label" for="db-table">Таблица</label>
                      <label class="form-label small mt-1" for="db-table-search">Фильтр таблиц</label>
                      <input id="db-table-search" class="form-input mb-1" placeholder="фильтр таблиц...">
                      <select id="db-table" class="form-select">
                        ${tables.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('')}
                      </select>
                    </div>
                    <div class="col-md-3">
                      <label class="form-label" for="db-limit">Лимит (макс 200)</label>
                      <input id="db-limit" type="number" min="1" max="200" step="1" class="form-input" value="20" inputmode="numeric">
                    </div>
                    <div class="col-md-3">
                      <label class="form-label" for="db-offset">Смещение</label>
                      <input id="db-offset" type="number" min="0" step="1" class="form-input" value="0" inputmode="numeric">
                    </div>
                  </div>

                  <div class="row mt-2">
                    <div class="col-md-4">
                      <label class="form-label" for="db-fixture-id">fixture_id (необяз.)</label>
                      <input id="db-fixture-id" class="form-input" inputmode="numeric" placeholder="например 123">
                    </div>
                    <div class="col-md-4">
                      <label class="form-label" for="db-league-id">league_id (необяз.)</label>
                      <input id="db-league-id" class="form-input" inputmode="numeric" placeholder="например 39">
                    </div>
                    <div class="col-md-4">
                      <label class="form-label" for="db-status">status (необяз.)</label>
                      <input id="db-status" class="form-input" placeholder="например NS / PENDING / ok">
                    </div>
                  </div>

                  <div class="mt-3">
                    <div class="btn-group">
                      <button type="button" class="btn btn-primary" data-action="db-browse-form" aria-label="Открыть таблицу по выбранным фильтрам" title="Открыть таблицу по выбранным фильтрам">Открыть</button>
                      <button type="button" class="btn-secondary btn-sm" data-action="db-prev" aria-label="Предыдущая страница результатов базы данных" title="Предыдущая страница результатов базы данных">← Назад</button>
                      <button type="button" class="btn-secondary btn-sm" data-action="db-next" aria-label="Следующая страница результатов базы данных" title="Следующая страница результатов базы данных">Далее →</button>
                    </div>
                    <div id="db-page-hint" class="small text-muted mt-2" role="status" aria-live="polite" aria-atomic="true"></div>
                  </div>
                </div>

                <details class="mt-3">
                  <summary class="text-muted">Отладочное окружение</summary>
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
          if (dbBrowseInFlight) setDbBrowseControlsPending(true);
          else updateDbBrowsePagerAvailability();
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
          const lastStatusText = translateRunStatus(lastStatus);
          const lastJob = chosenRun?.job_name ? formatJobLabel(chosenRun.job_name) : '—';
          const lastSkipped = Boolean(chosenRun?.skipped);
          const lastSkipReason = chosenRun?.skip_reason ? String(chosenRun.skip_reason) : '';
          const lastReq = lastMetrics ? Number(lastMetrics.requests || 0) : 0;
          const lastMiss = lastMetrics ? Number(lastMetrics.cache_misses || 0) : 0;
          const lastHit = lastMetrics ? Number(lastMetrics.cache_hits || 0) : 0;
          const lastErr = lastMetrics ? Number(lastMetrics.errors || 0) : 0;
          const lastBudget = lastMetrics && typeof lastMetrics.budget === 'object' ? lastMetrics.budget : null;
          const lastBudgetLimit = lastBudget ? Number(lastBudget.cache_misses_limit || 0) : 0;
          const lastBudgetUsed = lastBudget ? Number(lastBudget.cache_misses_used || 0) : 0;
          const lastBudgetText = lastBudgetLimit > 0 ? ` • бюджет ${lastBudgetUsed.toLocaleString()}/${lastBudgetLimit.toLocaleString()}` : '';

          const leagueNameById = new Map(
            leagues
              .filter((l) => l && l.league_id !== undefined && l.league_id !== null)
              .map((l) => [String(l.league_id), String(l.league_name || `лига ${l.league_id}`)])
          );
          const byEndpoint = lastMetrics && typeof lastMetrics.by_endpoint === 'object' ? lastMetrics.by_endpoint : null;
          const byLeague = lastMetrics && typeof lastMetrics.by_league === 'object' ? lastMetrics.by_league : null;
          const endpointRows = byEndpoint && Object.keys(byEndpoint).length
            ? Object.entries(byEndpoint)
              .map(([ep, v]) => [ep, v && typeof v === 'object' ? v : {}])
              .sort((a, b) => Number(b[1]?.cache_misses || 0) - Number(a[1]?.cache_misses || 0))
            : [];
          const leagueBreakdownRows = byLeague && Object.keys(byLeague).length
            ? Object.entries(byLeague)
              .map(([lid, v]) => {
                const metrics = v && typeof v === 'object' ? v : {};
                return [String(lid), leagueNameById.get(String(lid)) || `лига ${lid}`, metrics];
              })
              .sort((a, b) => Number(b[2]?.cache_misses || 0) - Number(a[2]?.cache_misses || 0))
            : [];
          const leagueParamRows = leagues
            .map((row) => {
              const lid = row?.league_id ?? '';
              const name = row?.league_name ? String(row.league_name) : `лига ${lid}`;
              return {
                name,
                dateKey: row?.date_key ? String(row.date_key) : '—',
                drawFreq: row?.draw_freq,
                dcRho: row?.dc_rho,
                calibAlpha: row?.calib_alpha,
                finishedTotal: Number(row?.finished_total || 0),
                xgTotal: Number(row?.xg_total || 0),
                dec1: Number(row?.decisions_1x2 || 0),
                decT: Number(row?.decisions_total || 0),
              };
            })
            .sort((a, b) => b.decT - a.decT);

          const warning = rebuildNeeded
            ? `
              <div class="alert alert-warning mb-3">
                <strong>⚠️ Требуется пересборка Elo</strong>
                <div class="small mt-1">min_unprocessed_kickoff < max_processed_kickoff → нажми «Пересобрать Elo»</div>
              </div>
            `
            : '';
          const apiWarning = apiFootball && apiBlocked
            ? `
              <div class="alert alert-warning mb-3">
                <strong>⚠️ Лимит API‑Football</strong>
                <div class="small mt-1">sync_data будет пропускаться до сброса лимита (UTC).${apiBlockedReason ? ` причина: ${escapeHtml(apiBlockedReason)}` : ''}</div>
              </div>
            `
            : '';

          return `
            ${warning}
            ${apiWarning}
            <div class="job-status-grid mb-3">
              <div class="status-item">
                <div class="status-label">Elo обработано</div>
                <div class="status-value ${statusCls}">${processed.toLocaleString()} / ${finished.toLocaleString()}</div>
              </div>
              <div class="status-item">
                <div class="status-label">Elo не обработано</div>
                <div class="status-value ${unprocessed > 0 ? 'text-danger' : 'status-idle'}">${unprocessed.toLocaleString()}</div>
              </div>
              <div class="status-item">
                <div class="status-label">Команды (elo / матчи)</div>
                <div class="status-value">${teamsWithElo.toLocaleString()} / ${teamsInFixtures.toLocaleString()}</div>
              </div>
              <div class="status-item">
                <div class="status-label">Нужна пересборка</div>
                <div class="status-value ${rebuildCls}">${rebuildNeeded ? 'да' : 'нет'}</div>
              </div>
              ${apiFootball ? `
                <div class="status-item">
                  <div class="status-label">Промахи API сегодня (cache_misses)</div>
                  <div class="status-value ${apiBlocked ? 'text-danger' : 'status-active'}">${apiMisses.toLocaleString()}${apiLimit ? ` / ${apiLimit.toLocaleString()}` : ''}</div>
                </div>
                ${apiRunBudget > 0 ? `
                  <div class="status-item">
                    <div class="status-label">API бюджет запуска</div>
                    <div class="status-value">${apiRunBudget.toLocaleString()} промахов/запуск${lastBudgetLimit > 0 ? ` • последний ${lastBudgetUsed.toLocaleString()}/${lastBudgetLimit.toLocaleString()}` : ''}</div>
                  </div>
                ` : ''}
              ` : ''}
            </div>

            <div class="small text-muted mb-2">
              Обновлено: ${escapeHtml(formatDateTime(data?.generated_at))} • сезон ${cfgSeason} • источник вер. ${cfgProb} • лиги ${cfgLeagues}
            </div>

            <details class="model-tech-details mb-3">
              <summary class="text-muted">Технические детали модели и API</summary>
              <div class="small text-muted mt-2">
                Elo: последний обработанный матч (last_processed_at) ${escapeHtml(lastProcessed)} • максимальный обработанный старт (max_processed_kickoff) ${escapeHtml(maxKickoff)} • минимальный необработанный старт (min_unprocessed_kickoff) ${escapeHtml(minUnprocessedKickoff)}
                ${apiFootball ? `
                  <br>
                  API‑Football сегодня (UTC): промахи кэша (cache_misses) ${apiMisses.toLocaleString()}${apiLimit ? ` / ${apiLimit.toLocaleString()}` : ''} • попадания кэша (cache_hits) ${apiHits.toLocaleString()} • ошибки ${apiErrors.toLocaleString()} • успешных запусков ${apiRunsOk.toLocaleString()}, неуспешных ${apiRunsFailed.toLocaleString()} • сброс ${escapeHtml(apiResetAt)}${apiRunBudget > 0 ? ` • бюджет/запуск ${apiRunBudget.toLocaleString()}` : ''}
                  ${chosenRun ? `<br>Последний API-запуск: ${escapeHtml(`${lastJob}/${lastStatusText}`)} • ${escapeHtml(lastStarted)}${lastSkipped ? ` • ПРОПУСК${lastSkipReason ? ` ${escapeHtml(lastSkipReason)}` : ''}` : ''} • промахов ${lastMiss.toLocaleString()}/${lastReq.toLocaleString()} • попаданий ${lastHit.toLocaleString()} • ошибок ${lastErr.toLocaleString()}${lastBudgetText}` : ''}
                ` : ''}
              </div>
            </details>

            ${lastMetrics && (endpointRows.length || leagueBreakdownRows.length) ? `
              <details class="mb-3">
                <summary class="text-muted">Разбивка API (последний запуск)</summary>
                ${endpointRows.length ? `
                  <div class="small text-muted mt-2">Эндпоинты: топ по промахам</div>
                  <div class="model-mini-grid mt-2">
                    ${endpointRows.slice(0, 5).map(([ep, v]) => `
                      <div class="model-mini-item">
                        <div class="model-mini-title text-truncate" title="${escapeHtml(ep)}">${escapeHtml(ep)}</div>
                        <div class="model-mini-meta">
                          промахи ${Number(v?.cache_misses || 0).toLocaleString()} • запросы ${Number(v?.requests || 0).toLocaleString()} • ошибки ${Number(v?.errors || 0).toLocaleString()}
                        </div>
                      </div>
                    `).join('')}
                  </div>
                  <details class="model-subdetails mt-2">
                    <summary class="small">Полная таблица эндпоинтов (${endpointRows.length})</summary>
                    <div class="table-responsive mt-2">
                      <table class="table table-sm table-striped model-status-table">
                        <thead class="table-dark">
                          <tr>
                            <th>Эндпоинт</th>
                            <th>Промах</th>
                            <th>Попадание</th>
                            <th>Запросы</th>
                            <th>Ошибки</th>
                          </tr>
                        </thead>
                        <tbody>
                          ${endpointRows.map(([ep, v]) => `
                            <tr>
                              <td class="text-truncate table-cell-truncate" title="${escapeHtml(ep)}">${escapeHtml(ep)}</td>
                              <td>${Number(v?.cache_misses || 0).toLocaleString()}</td>
                              <td>${Number(v?.cache_hits || 0).toLocaleString()}</td>
                              <td>${Number(v?.requests || 0).toLocaleString()}</td>
                              <td>${Number(v?.errors || 0).toLocaleString()}</td>
                            </tr>
                          `).join('')}
                        </tbody>
                      </table>
                    </div>
                  </details>
                ` : '<div class="text-muted mt-2">Нет детализации по эндпоинтам</div>'}

                ${leagueBreakdownRows.length ? `
                  <div class="small text-muted mt-3">Лиги: топ по промахам</div>
                  <div class="model-mini-grid mt-2">
                    ${leagueBreakdownRows.slice(0, 6).map(([lid, name, v]) => `
                      <div class="model-mini-item">
                        <div class="model-mini-title text-truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                        <div class="model-mini-meta">
                          промахи ${Number(v?.cache_misses || 0).toLocaleString()} • запросы ${Number(v?.requests || 0).toLocaleString()} • ошибки ${Number(v?.errors || 0).toLocaleString()}
                        </div>
                      </div>
                    `).join('')}
                  </div>
                  <details class="model-subdetails mt-2">
                    <summary class="small">Полная таблица лиг (${leagueBreakdownRows.length})</summary>
                    <div class="table-responsive mt-2">
                      <table class="table table-sm table-striped model-status-table">
                        <thead class="table-dark">
                          <tr>
                            <th>Лига</th>
                            <th>Промах</th>
                            <th>Попадание</th>
                            <th>Запросы</th>
                            <th>Ошибки</th>
                          </tr>
                        </thead>
                        <tbody>
                          ${leagueBreakdownRows.map(([lid, name, v]) => `
                            <tr>
                              <td class="text-truncate table-cell-truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</td>
                              <td>${Number(v?.cache_misses || 0).toLocaleString()}</td>
                              <td>${Number(v?.cache_hits || 0).toLocaleString()}</td>
                              <td>${Number(v?.requests || 0).toLocaleString()}</td>
                              <td>${Number(v?.errors || 0).toLocaleString()}</td>
                            </tr>
                          `).join('')}
                        </tbody>
                      </table>
                    </div>
                  </details>
                ` : '<div class="text-muted mt-2">Нет детализации по лигам</div>'}
              </details>
            ` : ''}

            ${leagues.length ? `
              <details class="model-leagues-details">
                <summary class="text-muted">Лиги и параметры (${leagues.length})</summary>
                <div class="small text-muted mt-2">Топ лиг по решениям тоталов (TOTAL)</div>
                <div class="model-mini-grid mt-2">
                  ${leagueParamRows.slice(0, 6).map((row) => `
                    <div class="model-mini-item">
                      <div class="model-mini-title text-truncate" title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</div>
                      <div class="model-mini-meta">
                        тотал ${row.decT.toLocaleString()} • исход ${row.dec1.toLocaleString()} • ничьи ${escapeHtml(formatPercent01(row.drawFreq, 1))}
                      </div>
                    </div>
                  `).join('')}
                </div>
                <details class="model-subdetails mt-2">
                  <summary class="small">Полная таблица лиг (${leagues.length})</summary>
                  <div class="table-responsive mt-2">
                    <table class="table table-sm table-striped model-status-table">
                      <thead class="table-dark">
                        <tr>
                          <th>Лига</th>
                          <th>Дата</th>
                          <th>Ничьи</th>
                          <th>ρ</th>
                          <th>α</th>
                          <th>Завершено</th>
                          <th>xG</th>
                        <th>Реш. исход</th>
                        <th>Реш. тотал</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${leagueParamRows.map((row) => `
                          <tr>
                            <td class="text-truncate table-cell-truncate" title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</td>
                            <td>${escapeHtml(row.dateKey)}</td>
                            <td>${escapeHtml(formatPercent01(row.drawFreq, 1))}</td>
                            <td>${escapeHtml(formatFixed(row.dcRho, 4))}</td>
                            <td>${escapeHtml(formatFixed(row.calibAlpha, 3))}</td>
                            <td>${row.finishedTotal.toLocaleString()}</td>
                            <td>${row.xgTotal.toLocaleString()}</td>
                            <td>${row.dec1.toLocaleString()}</td>
                            <td>${row.decT.toLocaleString()}</td>
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </details>
              </details>
            ` : '<div class="text-muted">Нет строк по лигам</div>'}
          `;
        }

        async function loadModelData() {
          const container = el('model-content');
          const updatedEl = el('model-updated');
          if (container) container.innerHTML = '<p class="text-muted">Загрузка...</p>';
          if (updatedEl) updatedEl.textContent = '—';

          const data = await apiFetchJson('/api/v1/model/status');
          if (updatedEl) updatedEl.textContent = `Обновлено: ${formatDateTime(data?.generated_at)}`;
          if (container) container.innerHTML = renderModelStatus(data);
        }

        async function browseTableFromForm(actionButton = null) {
          const { table, params } = syncDbBrowseStateFromDom();
          return browseTable(table, params, { actionButton });
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

          updateDbBrowsePagerAvailability();
          return { table, params };
        }

        async function browseTable(tableName, params = {}, options = {}) {
          const actionButton = options?.actionButton instanceof HTMLButtonElement ? options.actionButton : null;
          if (dbBrowseInFlight) {
            notify('Дождитесь завершения текущей загрузки таблицы', 'warning');
            return false;
          }
          dbBrowseInFlight = true;
          setDbBrowseControlsPending(true, actionButton);

          const resultDiv = el('database-result');
          if (resultDiv) {
            resultDiv.innerHTML = '<div class="text-center"><div class="spinner-border" aria-hidden="true"></div> Загрузка данных таблицы...</div>';
          }

          const sp = new URLSearchParams({ table: tableName });
          for (const [k, v] of Object.entries(params || {})) {
            if (v === undefined || v === null || String(v).trim() === '') continue;
            sp.set(k, String(v));
          }

          try {
            const data = await apiFetchJson(`/api/v1/db/browse?${sp.toString()}`);
            const rows = Array.isArray(data?.rows) ? data.rows : [];
            if (!resultDiv) return true;

            if (rows.length === 0) {
              dbLastResult = { table: tableName, query: sp.toString(), rows: [] };
              resultDiv.innerHTML = `
                <div class="alert alert-info">
                  Нет данных
                  <div class="text-muted small">Проверь фильтры / уменьши смещение</div>
                </div>
              `;
              return true;
            }

            const columns = Object.keys(rows[0] || {});
            const safeTable = escapeHtml(tableName);
            dbLastResult = { table: tableName, query: sp.toString(), rows };

            resultDiv.innerHTML = `
              <div class="card">
                <div class="card-header">
                  <h6 class="mb-0">📊 ${safeTable} (${rows.length} строк)</h6>
                  <div class="btn-group">
                    <button type="button" class="btn-secondary btn-sm" data-action="db-copy-json">Копировать JSON</button>
                  </div>
                </div>
                <div class="card-body">
                  <div class="small text-muted mb-1">Колонки: ${columns.length} • Строки: ${rows.length}</div>
                  <div class="table-responsive">
                    <table class="table table-sm table-striped db-result-table">
                      <thead class="table-dark">
                        <tr>${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join('')}</tr>
                      </thead>
                      <tbody>
                        ${rows.map((row) => `
                          <tr>
                            ${columns.map((col) => {
                              const v = row[col];
                              const val = v === null || v === undefined ? '' : String(v);
                              return `<td class="text-truncate table-cell-truncate" data-label="${escapeHtml(col)}">${escapeHtml(val || '—')}</td>`;
                            }).join('')}
                          </tr>
                        `).join('')}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            `;
            return true;
          } catch (e) {
            handleApiError(e);
            dbLastResult = null;
            if (resultDiv) resultDiv.innerHTML = `<div class="alert alert-danger">${escapeHtml(e?.message || 'ошибка')}</div>`;
            return false;
          } finally {
            dbBrowseInFlight = false;
            setDbBrowseControlsPending(false, actionButton);
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
          setSectionBusy(sectionId, true);
          try {
            if (sectionId === 'dashboard') {
              await Promise.all([loadDashboardData(), loadLiveData()]);
            } else if (sectionId === 'info') {
              await Promise.all([loadInfoData()]);
            } else if (sectionId === 'system') {
              await Promise.all([loadJobsData(), loadDatabaseData(), loadModelData()]);
            }
            setConnectionStatus('Онлайн', true);
            lastRefreshAt = new Date();
            renderPageMeta();
          } catch (e) {
            handleApiError(e);
          } finally {
            setSectionBusy(sectionId, false);
          }
        }

        function isMobileViewport() {
          return window.matchMedia('(max-width: 768px)').matches;
        }

        function getSidebarToggleButton() {
          const btn = document.querySelector('[data-action="toggle-sidebar"]');
          return btn instanceof HTMLButtonElement ? btn : null;
        }

        function sidebarFocusableElements() {
          const sidebar = el('sidebar');
          if (!sidebar) return [];
          const selectors = [
            'a[href]',
            'button:not([disabled])',
            'input:not([disabled])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            '[tabindex]:not([tabindex="-1"])',
          ].join(', ');
          return Array.from(sidebar.querySelectorAll(selectors)).filter((node) => isElementTabbable(node));
        }

        function trapSidebarFocus(event) {
          if (!isMobileViewport() || event.key !== 'Tab') return false;
          const sidebar = el('sidebar');
          if (!sidebar || sidebar.classList.contains('mobile-hidden')) return false;
          const focusable = sidebarFocusableElements();
          if (!focusable.length) return false;

          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          const active = document.activeElement;
          if (event.shiftKey) {
            if (active === first || !sidebar.contains(active)) {
              event.preventDefault();
              last.focus();
              return true;
            }
            return false;
          }
          if (active === last || !sidebar.contains(active)) {
            event.preventDefault();
            first.focus();
            return true;
          }
          return false;
        }

        function syncSidebarA11y() {
          const sidebar = el('sidebar');
          const toggleBtn = getSidebarToggleButton();
          const mobile = isMobileViewport();
          const hidden = mobile ? Boolean(sidebar?.classList.contains('mobile-hidden')) : false;
          document.body.classList.toggle('sidebar-open', mobile && !hidden);
          if (sidebar) sidebar.setAttribute('aria-hidden', hidden ? 'true' : 'false');
          if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', hidden ? 'false' : 'true');
            const label = hidden ? 'Открыть меню' : 'Скрыть меню';
            toggleBtn.setAttribute('aria-label', label);
            toggleBtn.setAttribute('title', label);
          }
        }

        function syncSidebarForViewport() {
          const sidebar = el('sidebar');
          if (!sidebar) return;
          const mobile = isMobileViewport();
          if (wasMobileViewport === null) {
            if (mobile) sidebar.classList.add('mobile-hidden');
            else sidebar.classList.remove('mobile-hidden');
          } else if (mobile !== wasMobileViewport) {
            if (mobile) sidebar.classList.add('mobile-hidden');
            else sidebar.classList.remove('mobile-hidden');
          } else if (!mobile) {
            sidebar.classList.remove('mobile-hidden');
          }
          wasMobileViewport = mobile;
          syncSidebarA11y();
        }

        function closeSidebarOnMobile(options = {}) {
          const { returnFocus = false } = options;
          if (!isMobileViewport()) {
            syncSidebarA11y();
            return;
          }
          const sidebar = el('sidebar');
          if (!sidebar || sidebar.classList.contains('mobile-hidden')) {
            syncSidebarA11y();
            return;
          }
          sidebar.classList.add('mobile-hidden');
          syncSidebarA11y();
          if (returnFocus) {
            const toggleBtn = getSidebarToggleButton();
            if (toggleBtn) toggleBtn.focus();
          }
        }

        function toggleSidebar() {
          const sidebar = el('sidebar');
          if (!sidebar) return;
          if (!isMobileViewport()) {
            sidebar.classList.remove('mobile-hidden');
            syncSidebarA11y();
            return;
          }
          const wasHidden = sidebar.classList.contains('mobile-hidden');
          sidebar.classList.toggle('mobile-hidden');
          syncSidebarA11y();
          if (wasHidden) {
            const first = sidebarFocusableElements()[0];
            if (first) first.focus();
          }
        }

        function updatePageHeader(sectionId) {
          const titles = {
            dashboard: { title: 'Панель', subtitle: 'Метрики, лайв-пики, недавние ставки' },
            info: { title: 'Инфо', subtitle: 'Полные вероятности по всем рынкам' },
            system: { title: 'Система', subtitle: 'Управление заданиями и просмотр БД' },
          };
          const pageInfo = titles[sectionId] || titles.dashboard;
          const titleEl = el('page-title');
          const subtitleEl = el('page-subtitle');
          if (titleEl) titleEl.textContent = pageInfo.title;
          if (subtitleEl) subtitleEl.textContent = pageInfo.subtitle;
        }

        function confirmRunJob(job) {
          const jobName = String(job || '').trim().toLowerCase();
          if (jobName === 'rebuild_elo') {
            return window.confirm('Пересборка Elo может занять продолжительное время. Продолжить?');
          }
          return true;
        }

        function showSection(sectionId, options = {}) {
          const { focusContent = false } = options;
          document.querySelectorAll('.section').forEach((section) => {
            section.classList.remove('active');
            section.setAttribute('aria-hidden', 'true');
          });
          document.querySelectorAll('.nav-item').forEach((item) => {
            item.classList.remove('active');
            item.removeAttribute('aria-current');
          });

          const targetSection = el(sectionId);
          if (targetSection) {
            targetSection.classList.add('active');
            targetSection.setAttribute('aria-hidden', 'false');
          }
          const navItem = document.querySelector(`.nav-item[data-section="${CSS.escape(sectionId)}"]`);
          if (navItem) {
            navItem.classList.add('active');
            navItem.setAttribute('aria-current', 'page');
          }

          updatePageHeader(sectionId);
          if (focusContent) {
            const contentRoot = el('main-content-root');
            if (contentRoot && typeof contentRoot.focus === 'function') contentRoot.focus();
          }
          syncBetsHistoryToggleA11y();
          void loadSectionData(sectionId);
          scheduleUiStateSave();
        }

        async function authenticateUser() {
          setAuthError('');
          const token = (el('admin-token')?.value || '').trim();
          if (!token) {
            setAuthError('Введите ADMIN_TOKEN', { focus: true });
            return;
          }

          setAuthPending(true);
          setConnectionStatus('Проверка…', true);
          try {
            const ok = await validateToken(token);
            if (!ok) {
              setConnectionStatus('Доступ запрещен', false);
              setAuthError('Неверный ADMIN_TOKEN (403)', { focus: true });
              return;
            }

            tokenState = token;
            storeToken(token);
            showApp();
            initializeApp();
          } catch (e) {
            console.error(e);
            setConnectionStatus('Офлайн', false);
            setAuthError('Не удалось подключиться к API (см. консоль)', { focus: true });
          } finally {
            setAuthPending(false);
          }
        }

        function initializeApp() {
          const { initialSection, openBetsHistory } = applyUiStateFromStorage(loadStoredUiState());
          const section = initialSection === 'system' ? 'system' : initialSection === 'info' ? 'info' : 'dashboard';
          showSection(section);
          syncBetsHistoryToggleA11y();
          void loadMeta();
          if (openBetsHistory && section === 'dashboard') {
            window.setTimeout(() => {
              const panel = el('bets-history-panel');
              if (panel && panel.classList.contains('is-hidden') && !betsHistoryState.expanded) void toggleBetsHistory();
            }, 0);
          }
          if (dashboardRefreshTimer) clearInterval(dashboardRefreshTimer);
          dashboardRefreshTimer = window.setInterval(() => {
            if (isFixtureModalOpen()) return;
            if (isTextInputLike(document.activeElement)) return;
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
            if (isPublishControlsBusy()) {
              notifyPublishBusyContext();
              return;
            }
            const fid = resolvePublishFixtureId(actionEl);
            if (!fid) {
              setPublishLog('Не найден ID матча для обновления publish-данных', 'error');
              notify('Не удалось определить fixture_id', 'error');
              return;
            }
            const btn = actionEl instanceof HTMLButtonElement ? actionEl : null;
            setPublishControlsPending(true, btn, 'Обновление…');
            const requestSeq = fixtureModalState.requestSeq;
            try {
              await refreshPublishPanels(fid, { requestSeq, announce: true, notifyUser: true, notifyOnSuccess: false });
            } finally {
              setPublishControlsPending(false, btn);
              if (btn && isFixtureModalContextCurrent({ fixtureId: fid, requestSeq }) && !btn.disabled) {
                try {
                  btn.focus();
                } catch (e) {
                  // ignore
                }
              }
            }
            return;
          }
          if (action === 'publish-post-preview') {
            if (isPublishControlsBusy()) {
              notifyPublishBusyContext();
              return;
            }
            const fid = resolvePublishFixtureId(actionEl);
            if (!fid) {
              setPublishLog('Не найден ID матча для пост-превью', 'error');
              notify('Не удалось определить fixture_id', 'error');
              return;
            }
            const btn = actionEl instanceof HTMLButtonElement ? actionEl : null;
            const requestSeq = fixtureModalState.requestSeq;
            setPublishControlsPending(true, btn, 'Генерация…');
            try {
              await loadPublishPostPreview(fid, { requestSeq, silentLog: false });
            } finally {
              setPublishControlsPending(false, btn);
              if (btn && isFixtureModalContextCurrent({ fixtureId: fid, requestSeq }) && !btn.disabled) {
                try {
                  btn.focus();
                } catch (e) {
                  // ignore
                }
              }
            }
            return;
          }
          if (action === 'publish-now') {
            if (isPublishControlsBusy()) {
              notifyPublishBusyContext();
              return;
            }
            const fid = resolvePublishFixtureId(actionEl);
            if (!fid) {
              setPublishLog('Не найден ID матча для публикации', 'error');
              notify('Не удалось определить fixture_id', 'error');
              return;
            }
            const force = actionEl.dataset.force === '1';
            if (force) {
              const ok = window.confirm('Принудительная публикация обходит защитные проверки. Продолжить?');
              if (!ok) return;
            }
            await publishNow(fid, force, actionEl instanceof HTMLButtonElement ? actionEl : null);
            return;
          }
          if (action === 'publish-copy-result') {
            await copyPublishResult();
            return;
          }
          if (action === 'publish-toggle-issues') {
            togglePublishIssuesView();
            return;
          }
          if (action === 'publish-history-toggle-issues') {
            togglePublishHistoryIssuesView();
            return;
          }
          if (action === 'publish-history-refresh') {
            if (publishHistoryUiState.loading) {
              notifyPublishHistoryLoading();
              return;
            }
            if (isPublishControlsBusy()) {
              notifyPublishBusyContext();
              return;
            }
            const fid = resolvePublishFixtureId(actionEl);
            if (!fid) {
              notify('Не удалось определить fixture_id', 'error');
              return;
            }
            await loadPublishHistory(fid, { requestSeq: fixtureModalState.requestSeq, focusAction: 'publish-history-refresh' });
            return;
          }
          if (action === 'publish-history-limit') {
            if (publishHistoryUiState.loading) {
              notifyPublishHistoryLoading();
              return;
            }
            if (isPublishControlsBusy()) {
              notifyPublishBusyContext();
              return;
            }
            await applyPublishHistoryLimit(actionEl.dataset.limit);
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
            const actionButton = actionEl instanceof HTMLButtonElement ? actionEl : null;
            betsHistoryState.allTime = true;
            if (!betsHistoryState.expanded) {
              await toggleBetsHistory();
              return;
            }
            const allTimeEl = el('bets-all-time');
            if (allTimeEl) allTimeEl.checked = true;
            await loadBetsHistoryPage({ resetOffset: true, actionButton, notifyOnBusy: true });
            return;
          }
          if (action === 'bets-refresh') {
            await loadBetsHistoryPage({ actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null, notifyOnBusy: true });
            return;
          }
          if (action === 'bets-apply') {
            await loadBetsHistoryPage({ resetOffset: true, actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null, notifyOnBusy: true });
            return;
          }
          if (action === 'bets-load-all') {
            await loadBetsHistoryAll({ maxRows: 5000, actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null, notifyOnBusy: true });
            return;
          }
          if (action === 'bets-export-csv') {
            await exportBetsHistoryCsv({ maxRows: 5000, actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null, notifyOnBusy: true });
            return;
          }
          if (action === 'bets-prev') {
            readBetsHistoryFiltersFromDom();
            const previousOffset = betsHistoryState.offset;
            betsHistoryState.offset = Math.max(0, betsHistoryState.offset - betsHistoryState.limit);
            const loaded = await loadBetsHistoryPage({ actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null, notifyOnBusy: true });
            if (!loaded) {
              betsHistoryState.offset = previousOffset;
              scheduleUiStateSave();
              updateBetsHistoryPagerAvailability();
            }
            return;
          }
          if (action === 'bets-next') {
            readBetsHistoryFiltersFromDom();
            const previousOffset = betsHistoryState.offset;
            const total = betsHistoryState.total;
            if (total === null || betsHistoryState.offset + betsHistoryState.limit < total) {
              betsHistoryState.offset += betsHistoryState.limit;
            }
            const loaded = await loadBetsHistoryPage({ actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null, notifyOnBusy: true });
            if (!loaded) {
              betsHistoryState.offset = previousOffset;
              scheduleUiStateSave();
              updateBetsHistoryPagerAvailability();
            }
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
            if (job) {
              const allowed = confirmRunJob(job);
              if (!allowed) return;
              await runJob(job, actionEl instanceof HTMLButtonElement ? actionEl : null);
            }
            return;
          }
          if (action === 'db-browse-form') {
            if (dbBrowseInFlight) {
              notify('Дождитесь завершения текущей загрузки таблицы', 'warning');
              return;
            }
            await browseTableFromForm(actionEl instanceof HTMLButtonElement ? actionEl : null);
            return;
          }
          if (action === 'db-prev') {
            if (dbBrowseInFlight) {
              notify('Дождитесь завершения текущей загрузки таблицы', 'warning');
              return;
            }
            const { table, params } = syncDbBrowseStateFromDom();
            const previousOffset = dbBrowseState.offset;
            dbBrowseState.offset = Math.max(0, dbBrowseState.offset - dbBrowseState.limit);
            const offsetEl = el('db-offset');
            if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
            scheduleUiStateSave();
            const loaded = await browseTable(table, { ...params, offset: dbBrowseState.offset }, { actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null });
            if (!loaded) {
              dbBrowseState.offset = previousOffset;
              if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
              scheduleUiStateSave();
              updateDbBrowsePagerAvailability();
            }
            return;
          }
          if (action === 'db-next') {
            if (dbBrowseInFlight) {
              notify('Дождитесь завершения текущей загрузки таблицы', 'warning');
              return;
            }
            const { table, params } = syncDbBrowseStateFromDom();
            const previousOffset = dbBrowseState.offset;
            dbBrowseState.offset = dbBrowseState.offset + dbBrowseState.limit;
            const offsetEl = el('db-offset');
            if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
            scheduleUiStateSave();
            const loaded = await browseTable(table, { ...params, offset: dbBrowseState.offset }, { actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null });
            if (!loaded) {
              dbBrowseState.offset = previousOffset;
              if (offsetEl) offsetEl.value = String(dbBrowseState.offset);
              scheduleUiStateSave();
              updateDbBrowsePagerAvailability();
            }
            return;
          }
          if (action === 'db-browse') {
            if (dbBrowseInFlight) {
              notify('Дождитесь завершения текущей загрузки таблицы', 'warning');
              return;
            }
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
            await browseTable(table, params, { actionButton: actionEl instanceof HTMLButtonElement ? actionEl : null });
            return;
          }
          if (action === 'db-copy-json') {
            await copyDbJson();
            return;
          }
        }

        document.addEventListener('click', (e) => {
          const targetEl = e.target instanceof Element ? e.target : null;
          if (targetEl && isMobileViewport()) {
            const sidebar = el('sidebar');
            const sidebarOpen = Boolean(sidebar && !sidebar.classList.contains('mobile-hidden'));
            if (sidebarOpen) {
              const clickedInsideSidebar = Boolean(targetEl.closest('#sidebar'));
              const clickedSidebarToggle = Boolean(targetEl.closest('[data-action="toggle-sidebar"]'));
              if (!clickedInsideSidebar && !clickedSidebarToggle) closeSidebarOnMobile();
            }
          }

          const navLink = targetEl ? targetEl.closest('.nav-item[data-section]') : null;
          if (navLink instanceof HTMLElement) {
            e.preventDefault();
            const sectionId = navLink.dataset.section;
            if (sectionId) showSection(sectionId, { focusContent: true });
            closeSidebarOnMobile();
            return;
          }

          const actionEl = targetEl ? targetEl.closest('[data-action]') : null;
          if (!actionEl) return;
          e.preventDefault();
          void handleAction(actionEl);
        });

        document.addEventListener('change', (e) => {
          const target = e.target;
          if (target instanceof HTMLInputElement && target.id === 'publish-dry-run') {
            publishUiState.dryRun = Boolean(target.checked);
            scheduleUiStateSave();
            return;
          }
          if (target instanceof HTMLSelectElement && target.id === 'publish-image-theme') {
            publishUiState.imageTheme = normalizePublishImageTheme(target.value);
            target.value = publishUiState.imageTheme;
            scheduleUiStateSave();
          }
        });

        document.addEventListener('keydown', (e) => {
          if (isFixtureModalOpen()) {
            if (e.key === 'Escape') {
              e.preventDefault();
              closeFixtureModal();
              return;
            }
            if (e.key === 'Tab') {
              trapFixtureModalFocus(e);
            }
          }

          if (e.key === 'Escape' && isMobileViewport()) {
            const sidebar = el('sidebar');
            if (sidebar && !sidebar.classList.contains('mobile-hidden')) {
              e.preventDefault();
              closeSidebarOnMobile({ returnFocus: true });
              return;
            }
          }

          if (trapSidebarFocus(e)) return;

          const tabTarget = e.target instanceof HTMLElement ? e.target.closest('.info-tab') : null;
          if (tabTarget instanceof HTMLElement) {
            const key = e.key;
            if (key === 'ArrowLeft' || key === 'ArrowRight' || key === 'Home' || key === 'End') {
              const tabs = Array.from(document.querySelectorAll('.info-tab')).filter((btn) => btn instanceof HTMLElement);
              if (tabs.length) {
                const currentIndex = Math.max(0, tabs.indexOf(tabTarget));
                let nextIndex = currentIndex;
                if (key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
                if (key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
                if (key === 'Home') nextIndex = 0;
                if (key === 'End') nextIndex = tabs.length - 1;
                const nextBtn = tabs[nextIndex];
                const nextTab = nextBtn?.dataset?.tab || 'picks';
                e.preventDefault();
                applyInfoTab(nextTab);
                scheduleUiStateSave();
                nextBtn.focus();
                return;
              }
            }
          }

          if ((e.key === 'Enter' || e.key === ' ') && !isTextInputLike(e.target)) {
            const targetEl = e.target instanceof HTMLElement ? e.target : null;
            const actionEl = targetEl ? targetEl.closest('[data-action]') : null;
            if (actionEl && actionEl.getAttribute('role') === 'button') {
              e.preventDefault();
              void handleAction(actionEl);
              return;
            }
          }

          if (e.key !== 'Enter') return;
          const target = e.target;
          if (!(target instanceof HTMLElement) || !target.id) return;
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

          applyAlertsA11y(document);
          initAlertsA11yObserver();

          syncSidebarForViewport();
          window.addEventListener('resize', syncSidebarForViewport);

          const tokenInput = el('admin-token');
          const stored = loadStoredToken();
          if (tokenInput) tokenInput.value = stored || '';
          if (tokenInput) {
            tokenInput.addEventListener('keydown', (e) => {
              if (e.key === 'Enter') void authenticateUser();
            });
            tokenInput.addEventListener('input', () => {
              const box = el('auth-error');
              if (box && !box.classList.contains('is-hidden')) setAuthError('');
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
            setConnectionStatus('Проверка…', true);
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
    
