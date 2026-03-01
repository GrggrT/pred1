# Card Gen v2 — Migration Notes

**Источник**: `app/services/html_image.py` (1 593 строки)
**Дата анализа**: 2026-02-26

---

## 1. Полная сигнатура текущей публичной функции

```python
def render_headline_image_html(
    text: str,                              # Позиционный. Многострочный текст прогноза
    width: int = 1280,                      # Позиционный. CSS-ширина холста (мин. 800)
    *,
    style_variant: str = "pro",             # "pro" | "viral"
    home_logo: bytes | None = None,         # PNG/JPEG/WEBP байты
    away_logo: bytes | None = None,
    league_logo: bytes | None = None,
    league_label: str | None = None,        # Название лиги
    market_label: str | None = None,        # "1X2" | "TOTAL" и т.д.
    bet_label: str | None = None,           # Заголовок рекомендации
    league_country: str | None = None,
    league_round: str | None = None,
    venue_name: str | None = None,
    venue_city: str | None = None,
    home_rank: int | None = None,
    away_rank: int | None = None,
    home_points: int | None = None,
    away_points: int | None = None,
    home_played: int | None = None,
    away_played: int | None = None,
    home_goal_diff: int | None = None,
    away_goal_diff: int | None = None,
    home_form: str | None = None,           # "WWDLW" (макс 8 символов)
    away_form: str | None = None,
    home_win_prob: float | None = None,     # 0.0-1.0 или 0-100 (авто-нормализация)
    draw_prob: float | None = None,
    away_win_prob: float | None = None,
    signal_title: str | None = None,        # "VALUE INDICATORS"
    signal_line_1: str | None = None,       # Поддержка "(+X.X% edge)" delta-подсветки
    signal_line_2: str | None = None,
    signal_line_3: str | None = None,
) -> bytes  # PNG-байты (retina 2x, ~2 MB)
```

**Возвращает**: PNG bytes (device_scale_factor=2, реальная ширина 2560px при width=1280).

---

## 2. Все места вызова

### Прямые вызовы (production)

| Файл | Строка | Контекст | Паттерн вызова |
|-------|--------|----------|---------------|
| `app/services/publishing.py` | 3777 | `build_post_preview()` | `await asyncio.to_thread(render_headline_image_html, image_text, **html_image_kwargs)` |
| `app/services/publishing.py` | 4260 | `publish_fixture()` | `await asyncio.to_thread(render_headline_image_html, image_text, **html_image_kwargs)` |

### Тестовые вызовы

| Файл | Строка | Контекст |
|-------|--------|----------|
| `tests/test_html_image_snapshots.py` | 57 | `render_headline_image_html(case["text"], **(case.get("kwargs") or {}))` |
| `scripts/generate_html_snapshots.py` | 39 | `render_headline_image_html(text, **kwargs)` |

### Моки в тестах

| Файл | Строки | Контекст |
|-------|--------|----------|
| `tests/test_publishing_image_flow.py` | 119, 172, 212, 246 | `monkeypatch.setattr(publishing, "render_headline_image_html", fake_...)` |

### Import

| Файл | Строка | Способ |
|-------|--------|--------|
| `app/services/publishing.py` | 24-26 | `try: from ... import render_headline_image_html / except: = None` |
| `tests/test_html_image_snapshots.py` | 16 | Direct import |
| `scripts/generate_html_snapshots.py` | 16 | Direct import |

---

## 3. Как вызывается из publishing.py

Оба вызова идентичны по структуре kwargs:

```python
# Первый аргумент (positional)
image_text = _strip_image_probability_line(headline)

# Kwargs (одинаковые в обоих местах)
html_image_kwargs = {
    # Логотипы (bytes | None, загружены ранее через _fetch_logo_bytes)
    "home_logo": home_logo_bytes,
    "away_logo": away_logo_bytes,
    "league_logo": league_logo_bytes,

    # Текст
    "league_label": str(getattr(fixture, "league_name", "") or ""),
    "market_label": "1X2" | "TOTAL" | market_name,
    "bet_label": bet_label,

    # Тема
    "style_variant": image_theme_norm,  # нормализовано через _normalize_image_theme()

    # Контекст из API-Football (ImageVisualContext)
    "league_country": image_visual_context.league_country,
    "league_round": image_visual_context.league_round,
    "venue_name": image_visual_context.venue_name,
    "venue_city": image_visual_context.venue_city,
    "home_rank": image_visual_context.home_rank,
    "away_rank": image_visual_context.away_rank,
    "home_points": image_visual_context.home_points,
    "away_points": image_visual_context.away_points,
    "home_played": image_visual_context.home_played,
    "away_played": image_visual_context.away_played,
    "home_goal_diff": image_visual_context.home_goal_diff,
    "away_goal_diff": image_visual_context.away_goal_diff,
    "home_form": image_visual_context.home_form,
    "away_form": image_visual_context.away_form,

    # Вероятности (из prediction_decisions payload)
    "home_win_prob": home_win_prob,
    "draw_prob": draw_prob,
    "away_win_prob": away_win_prob,

    # Signal-индикаторы (сгенерированы в publishing.py)
    "signal_title": indicator_title,
    "signal_line_1": indicator_line_1,
    "signal_line_2": indicator_line_2,
    "signal_line_3": indicator_line_3,
}
```

**Важно**: `width` не передаётся явно (используется default=1280).

---

## 4. Внутренние функции и их ответственности

### Dataclass

| Класс | Поля | Назначение |
|-------|------|------------|
| `_CardData` | title, league, date_line, match_line, home_team, away_team, recommendation_title, recommendation_lines, recommendation_main, recommendation_odd, recommendation_extra, market | Структурированные данные, спарсенные из freeform text |

### Парсинг текста

| Функция | Строки | Назначение |
|---------|--------|------------|
| `_strip_tags(text)` | 46-47 | Удаление HTML-тегов regex |
| `_strip_emojis(text)` | 50-54 | Удаление emoji (Unicode ranges + ZWJ/VS) |
| `_normalize_line(line)` | 57-58 | Compose: unescape → strip_tags → strip_emojis → trim |
| `_is_datetime_line(line)` | 61-66 | Детекция строк с "UTC" или HH:MM |
| `_split_match_line(match_line)` | 69-84 | Парсинг "Home vs Away" / "Home - Away" → (home, away) |
| `_split_recommendation_lines(lines)` | 87-111 | Разделение рекомендации на (main, odd, extras) |
| `_parse_card_data(text, *, league_label, market_label, bet_label)` | 243-308 | Полный парсинг текста → _CardData |

### Типографика и адаптивные шрифты

| Функция | Строки | Назначение |
|---------|--------|------------|
| `_initial_letter(text, fallback)` | 114-116 | Первая буква (Latin/Cyrillic/digit) |
| `_clean_meta_text(value)` | 119-120 | Нормализация метаданных |
| `_normalize_form(value)` | 123-126 | Извлечение W/D/L, макс 8 символов |
| `_normalize_style_variant(value)` | 129-131 | Валидация темы → "pro"/"viral" |
| `_title_color(title)` | 134-146 | Маппинг ключевых слов → hex-цвет |
| `_odds_display(value)` | 149-157 | Извлечение числового коэффициента |
| `_odds_font_size_px(display_value)` | 160-169 | Размер шрифта для коэффициента (38-64px) |
| `_team_font_size_px(team_name)` | 172-183 | Размер шрифта для команды (36-56px) |
| `_signal_title_font_size_px(title)` | 186-197 | Размер шрифта для заголовка сигнала (24-35px) |
| `_text_width_units(text)` | 200-218 | Оценка ширины текста (посимвольные веса) |
| `_fit_font_size_px(text, available_px, *, min_px, max_px)` | 221-224 | Подбор размера шрифта под доступную ширину |
| `_normalize_probability(value)` | 227-240 | Нормализация вероятности (0-1 или 0-100 → 0-1) |

### Ассеты (логотипы, шрифты)

| Функция | Строки | Назначение |
|---------|--------|------------|
| `_rgb_to_hex(color)` | 311-312 | RGB tuple → "#RRGGBB" |
| `_bytes_to_data_uri(data, fallback_svg)` | 315-326 | Bytes → data URI (auto-detect MIME: PNG/JPEG/WEBP) |
| `_fallback_logo_svg(letter, bg)` | 329-341 | SVG-заглушка (96×96 круг с инициалом) |
| `_embedded_font_css()` | 344-367 | @font-face CSS с base64 NotoSans (LRU cached) |

### Браузер (Playwright)

| Функция | Строки | Назначение |
|---------|--------|------------|
| `_ensure_browser()` | 370-386 | Singleton Chromium (double-checked locking + threading.Lock) |
| `_shutdown_browser()` | 389-403 | Cleanup (atexit.register) |

### HTML-сборка

| Функция | Строки | Назначение |
|---------|--------|------------|
| `_build_html(data, *, ...)` | 409-1489 | Полная сборка HTML-документа (CSS + HTML + JS palette) |

### Главная функция

| Функция | Строки | Назначение |
|---------|--------|------------|
| `render_headline_image_html(...)` | 1492-1593 | Оркестратор: parse → logos → html → browser → screenshot |

---

## 5. CSS-переменные и темы

### Базовые CSS-переменные (:root)

```css
--bg0: #040918;              /* Фон: тёмный край */
--bg1: #081a36;              /* Фон: середина */
--bg2: #071023;              /* Фон: другой край */
--ink: #f8fbff;              /* Основной цвет текста */
--muted: #b9c6e3;            /* Приглушённый текст */
--accent: #ffd739;           /* Акцент (odds, pick) */
--title: {dynamic};          /* Цвет заголовка (зависит от ключевого слова) */
--panel-line: #5a76ad;       /* Линия панели */
--home-rgb: 78, 134, 255;   /* RGB команды хозяев (динамические через JS) */
--away-rgb: 255, 94, 128;   /* RGB команды гостей (динамические через JS) */
--home: rgb(var(--home-rgb));
--away: rgb(var(--away-rgb));
--home-glow: rgba(var(--home-rgb), 0.34);
--away-glow: rgba(var(--away-rgb), 0.34);
--home-border: rgba(var(--home-rgb), 0.82);
--away-border: rgba(var(--away-rgb), 0.82);
--home-soft: rgba(var(--home-rgb), 0.25);
--away-soft: rgba(var(--away-rgb), 0.25);
--home-meter: rgba(var(--home-rgb), 0.78);
--away-meter: rgba(var(--away-rgb), 0.78);
--logo-shell-size: 124px;
--odds-shell-size: 170px;
```

### Title Color Mapping

| Ключевое слово | Цвет |
|----------------|------|
| STRONG / СИЛЬНЫЙ | `#ffd66b` |
| TOP / ТОП | `#59e2a5` |
| HIGH-CONFIDENCE | `#ffbf71` |
| STANDARD / СТАНДАРТ | `#9fb7ff` |
| HOT / ГОРЯЧ | `#ff9a4a` |
| (fallback) | `#ff9a4a` |

### Тема "pro" (по умолчанию)

- Фон: `radial-gradient(760px 520px ...)`, linear `#040918 → #081a36 → #071023`
- Noise overlay opacity: 0.45
- Kicker pill: border `rgba(152, 173, 214, 0.55)`, bg `rgba(13, 26, 52, 0.62)`, color `#d4def4`
- Kicker label: "AI BETTING SIGNAL"
- Card border: `rgba(141, 166, 211, 0.62)`
- Card bg: `rgba(12, 24, 50, 0.88) → rgba(10, 21, 43, 0.76)`

### Тема "viral" (overrides)

- Фон: `radial-gradient(820px 560px ...)`, linear `#081427 → #0b1a3f → #21102f`
- Noise overlay opacity: 0.56
- Kicker pill: border `rgba(255, 214, 94, 0.58)`, bg `rgba(30, 24, 10, 0.70)`, color `#ffe6a9`
- Kicker label: "MATCHDAY SIGNAL"
- Card border: `rgba(255, 186, 122, 0.52)`
- Card bg: `rgba(27, 29, 56, 0.90) → rgba(14, 23, 50, 0.80)`
- Extra box-shadow: `0 0 30px rgba(255, 158, 91, 0.10)`
- Odds shell: border `rgba(255, 198, 128, 0.78)`, shadow glow `rgba(255, 164, 80, 0.24)`
- Pick: border `rgba(255, 192, 76, 0.84)`, glow `rgba(255, 187, 71, 0.14)`

---

## 6. Все hardcoded значения

### Константы модуля

| Константа | Значение | Назначение |
|-----------|----------|------------|
| `_WIDTH` | 1280 | Default CSS width |
| `_MAX_VIEWPORT_H` | 2200 | Макс. высота viewport |
| `_DEFAULT_VIEWPORT_H` | 1400 | Начальная высота viewport |
| `_DEFAULT_STYLE_VARIANT` | `"pro"` | Тема по умолчанию |
| `_STYLE_VARIANTS` | `{"pro", "viral"}` | Допустимые темы |
| device_scale_factor | 2 | Retina-множитель |
| color_scheme | `"dark"` | Цветовая схема браузера |
| min width | 800 | Минимальная ширина (enforced) |

### Шрифты

| Параметр | Значение |
|----------|----------|
| Font family name | `"PredSans"` |
| Regular file | `NotoSans-Regular.ttf` (weight: 400) |
| Bold file | `NotoSans-Bold.ttf` (weight: 700) |
| Font path | `app/assets/fonts/` |
| HTML validation | `<html` in first 512 bytes → skip |

### Font size lookup tables

**Odds** (по длине цифр):
| Длина | Размер |
|-------|--------|
| ≤ 4 | 64px |
| 5 | 52px |
| 6 | 44px |
| ≥ 7 | 38px |

**Team names** (по длине имени):
| Длина | Размер |
|-------|--------|
| ≤ 13 | 56px |
| ≤ 17 | 50px |
| ≤ 21 | 44px |
| ≤ 25 | 40px |
| > 25 | 36px |

**Signal title** (по длине):
| Длина | Размер |
|-------|--------|
| ≤ 12 | 35px |
| ≤ 16 | 32px |
| ≤ 20 | 29px |
| ≤ 24 | 26px |
| > 24 | 24px |

### Text width weights (per character)

| Тип символа | Вес |
|-------------|-----|
| Space | 0.32 |
| Wide (MW@#%&) | 1.04 |
| Narrow (ilIjtfr) | 0.45 |
| Uppercase | 0.84 |
| Digit | 0.72 |
| Other | 0.68 |

### Размеры элементов

| Элемент | Значение |
|---------|----------|
| Logo shell size | 124px (CSS var) |
| Logo img size | 90×90px |
| Odds shell size | 170px (CSS var) |
| League logo | 34×34px |
| Form token | 34×34px |
| Form token font | 18px |
| SVG fallback viewBox | 96×96 |
| SVG circle radius | 44 |
| SVG font size | 38 |
| Canvas padding | 44px 54px 50px |
| Match stage border-radius | 34px |
| Pick border-radius | 30px |
| Card border-radius | 18px |
| Kicker border-radius | 999px |

### Таймауты

| Таймаут | Значение |
|---------|----------|
| Palette JS wait | 2500ms |
| Image load (JS) | 900ms |

### Цвета fallback

| Элемент | RGB |
|---------|-----|
| Home default | (78, 134, 255) |
| Away default | (255, 94, 128) |
| League logo bg | #3f5b95 |

### Палитра JS — нормализация

| Параметр | Диапазон |
|----------|----------|
| Canvas size | max 42px side |
| Alpha threshold | < 40 → skip |
| Saturation threshold | < 20 → skip |
| Weight formula | sat + 10 |
| HSL saturation clamp | [0.36, 0.82] |
| HSL lightness clamp | [0.42, 0.66] |
| Luminance min | 95 |
| Luminance max | 210 |
| Luminance formula | 0.2126*R + 0.7152*G + 0.0722*B |

### Текст

| Элемент | Значение |
|---------|----------|
| Default odds | "2.00" |
| Form max length | 8 chars |
| Default recommendation | ("Total Under 2.5", "@ 2.10") |
| Default title | "HOT PREDICTION" |
| Default teams | ("HOME", "AWAY") |
| Disclaimer | "DISCLAIMER: This is an analytical prediction..." |

---

## 7. HTML-структура (layout)

```
#post-canvas .theme-{pro|viral}
├── header.top (grid: 1fr 1fr, gap 18px)
│   ├── .signal-card (132px height)
│   │   ├── [если signal_rows] .signal-metrics
│   │   │   ├── .signal-header-row (flex)
│   │   │   │   ├── .signal-metrics-title (dynamic font-size)
│   │   │   │   └── .signal-kicker "AI BETTING SIGNAL"
│   │   │   └── .signal-metric-line × N (с .signal-delta для edge)
│   │   └── [иначе] .signal-fallback
│   │       └── .signal-header-row
│   │           ├── h1.signal-title (заголовок прогноза)
│   │           └── .signal-kicker
│   └── .meta-stack
│       └── .meta-card (132px height)
│           ├── .meta-league (logo + name, flex right-aligned)
│           ├── .meta-date (text-align right)
│           └── .meta-secondary (country + round)
│
├── section.match-stage (border-radius 34px)
│   ├── .teams (grid: 1fr 220px 1fr)
│   │   ├── .team (home)
│   │   │   ├── .logo-shell.home > img (90×90)
│   │   │   └── p.team-name (dynamic font-size)
│   │   ├── .odds-core
│   │   │   └── .odds-shell (170px circle)
│   │   │       └── .odds-inner
│   │   │           ├── .odds-value (dynamic font-size)
│   │   │           └── .odds-label "ODDS"
│   │   └── .team (away)
│   │       ├── .logo-shell.away > img
│   │       └── p.team-name
│   ├── .table-strip (grid: 1fr 1fr)
│   │   ├── .table-team.home
│   │   │   ├── .table-main (rank + points)
│   │   │   ├── .table-sub (played + GD)
│   │   │   └── .form-track (W/D/L tokens)
│   │   └── .table-team.away (mirrored layout)
│   ├── .chance-wrap [опционально]
│   │   ├── .chance-labels (home% / draw% / away%)
│   │   └── .chance-bar (.chance-seg × 3)
│   └── .venue-row [опционально]
│
└── section.pick (border-radius 30px)
    ├── .pick-main (pick text, accent color)
    └── .pick-disclaimer
```

---

## 8. Chromium launch args

```python
args=[
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--font-render-hinting=none",
]
```

Browser context per render:
```python
viewport={"width": max(800, width), "height": 1400}
device_scale_factor=2
color_scheme="dark"
```

Dynamic viewport resize:
```python
if content_height > 1400:
    new_height = min(2200, content_height + 48)
    page.set_viewport_size({"width": width, "height": new_height})
```

Screenshot: `element.screenshot(type="png")` на `#post-canvas`.

---

## 9. Ключевые решения для миграции

1. **text параметр → структурированные данные**: Текущий `text` парсится через `_parse_card_data()`. В v2 данные уже структурированы в `PredictionCardData` — парсинг не нужен.

2. **JS palette → Python**: Текущий JS-алгоритм (canvas sampling + HSL normalization) нужно портировать в Python (colorthief + те же HSL clamps).

3. **Sync → Async Playwright**: Текущий `sync_playwright()` + `threading.Lock`. В v2 — `async_playwright()` + `asyncio.Lock`. Убирает необходимость `asyncio.to_thread()` в publishing.py.

4. **f-string HTML → Jinja2**: ~880 строк f-string → шаблоны с компонентами. CSS (~630 строк) выносится в отдельные файлы тем.

5. **PNG → JPEG**: Текущий output ~2 MB PNG. В v2 — optimize_for_telegram() → JPEG quality=82, <200KB.

6. **Два вызова в publishing.py**: Оба идентичны по kwargs. Adapter `_build_card_data_from_legacy_args()` конвертирует текущие аргументы в `PredictionCardData`.

7. **Feature flag**: `CARD_GEN_V2` env → переключение между v1 и v2 без удаления старого кода.
