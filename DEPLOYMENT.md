# Deployment — Production Environment

## PRODUCTION SERVER (LIVE!)

| | |
|---|---|
| **Instance** | GCP `pred1` (e2-micro, 1 vCPU, 1GB RAM + 2GB swap) |
| **IP** | `35.208.46.164` |
| **SSH** | `ssh deploy@35.208.46.164` |
| **Admin Token** | `grggrt` |
| **URL** | `http://35.208.46.164:8000` |
| **Dashboard** | `http://35.208.46.164:8000/` (public) |
| **Admin Panel** | `http://35.208.46.164:8000/admin` |
| **Health** | `http://35.208.46.164:8000/health` |
| **Code Path** | `/home/deploy/pred1/` |
| **Docker Compose** | `deploy/docker-compose.gcp.yml` |
| **Dockerfile** | `deploy/Dockerfile.prod` |

---

## Architecture: Production vs Local

### Production (GCP)
```
docker-compose.gcp.yml
├── db       — PostgreSQL 16 (256MB limit)
└── app      — API + Scheduler в ОДНОМ контейнере (512MB limit)
               SCHEDULER_ENABLED=true
               Uvicorn 1 worker
```
- **Один контейнер `app`** делает всё: API, scheduler (APScheduler), jobs
- Нет отдельного `scheduler` сервиса
- Нет `ai-office` сервиса (не задеплоен)

### Local (docker-compose.yml)
```
docker-compose.yml
├── db        — PostgreSQL 16
├── app       — API only (SCHEDULER_ENABLED=false)
├── scheduler — Jobs only (SCHEDULER_ENABLED=true)
└── ai-office — AI Office bot (отключен по умолчанию)
```
- **Три отдельных контейнера**: app, scheduler, ai-office
- `app` НЕ запускает scheduler (важно!)

---

## Как деплоить на GCP

### Быстрый деплой (код уже на сервере)
```bash
# 1. Собрать tarball (из корня проекта)
tar czf /tmp/pred1-deploy.tar.gz \
  --exclude='.venv' --exclude='__pycache__' \
  --exclude='.git' --exclude='node_modules' \
  --exclude='.env' .

# 2. Залить на сервер
cat /tmp/pred1-deploy.tar.gz | \
  ssh deploy@35.208.46.164 'sudo tar xzf - -C /home/deploy/pred1 --overwrite'

# 3. Пересобрать Docker-образ
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml build app'

# 4. Перезапустить
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml up -d app'

# 5. Проверить логи
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml logs app --tail=20'
```

### Миграция БД (если добавлены новые таблицы/колонки)
```bash
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml exec -T app alembic upgrade head'
```

---

## Важные отличия Production от Local

### 1. `.env` файл
- **НЕ перезатирается** при деплое (tar excludes `.env`)
- Находится: `/home/deploy/pred1/.env`
- Содержит боевые ключи: `API_FOOTBALL_KEY`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`
- `ADMIN_TOKEN=grggrt` (для API)

### 2. Docker Compose файлы
| | Production | Local |
|---|---|---|
| **Файл** | `deploy/docker-compose.gcp.yml` | `docker-compose.yml` |
| **Сервисы** | `db` + `app` | `db` + `app` + `scheduler` + `ai-office` |
| **Scheduler** | Внутри `app` (`SCHEDULER_ENABLED=true`) | Отдельный контейнер `scheduler` |
| **Memory limits** | db: 256M, app: 512M | Нет лимитов |
| **Порты БД** | НЕ выставлены наружу | `5432:5432` |

### 3. Cron-расписание (production)
| Job | Cron | Частота |
|---|---|---|
| `sync_data` | `*/5 * * * *` | Каждые 5 мин |
| `compute_indices` | `1-59/10 * * * *` | Каждые 10 мин |
| `build_predictions` | `3-59/10 * * * *` | Каждые 10 мин |
| `evaluate_results` | `2-59/5 * * * *` | Каждые 5 мин |
| `auto_publish` | `5-59/10 * * * *` | Каждые 10 мин |
| `fit_dixon_coles` | `5 6 * * *` | Раз в день 06:05 |
| `quality_report` | `30 6,23 * * *` | 2 раза в день |
| `maintenance` | `30 3 * * *` | Раз в день 03:30 |

### 4. Telegram Publishing (production)
- 8 каналов: EN, UK, RU, FR, DE, PL, PT, ES
- Groq AI генерирует аналитику (llama-3.3-70b)
- Картинки рендерятся через Playwright/Chromium
- Картинка + текст отправляются одним постом (caption)
- Auto-publish: drip-feed, один пост каждые ~50 мин

---

## API (production)

### Ручной запуск job-ов
```bash
# Синхронизация данных
curl -H "X-Admin-Token: grggrt" -X POST \
  "http://35.208.46.164:8000/api/v1/run-now?job=sync_data"

# Полный пайплайн
curl -H "X-Admin-Token: grggrt" -X POST \
  "http://35.208.46.164:8000/api/v1/run-now?job=full_pipeline"

# Принудительная публикация
curl -H "X-Admin-Token: grggrt" -X POST \
  "http://35.208.46.164:8000/api/v1/run-now?job=auto_publish"

# Оценка результатов
curl -H "X-Admin-Token: grggrt" -X POST \
  "http://35.208.46.164:8000/api/v1/run-now?job=evaluate_results"
```

### Превью поста
```bash
curl -H "X-Admin-Token: grggrt" \
  "http://35.208.46.164:8000/api/v1/publish/post_preview?fixture_id=1378147&lang=ru"
```

---

## Логи и отладка

```bash
# Все логи app (последние 50 строк)
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml logs app --tail=50'

# Только auto_publish
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml logs app --tail=200 2>&1 | grep auto_publish'

# База данных
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml exec -T db psql -U postgres -d fc_mvp'

# Статус контейнеров
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml ps'
```

---

## Чек-лист перед деплоем

1. [ ] `python -m compileall -q app` — синтаксис OK
2. [ ] Нет новых полей в `config.py` без дефолтов (иначе app упадёт на старте)
3. [ ] Если добавили Alembic миграцию — запустить после деплоя
4. [ ] Tar НЕ включает `.env` (исключён через `--exclude='.env'`)
5. [ ] После деплоя проверить `health` endpoint
6. [ ] Проверить логи на ошибки

---

## Восстановление после сбоя

```bash
# Перезапуск app
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml restart app'

# Полный перезапуск (с БД)
ssh deploy@35.208.46.164 'cd /home/deploy/pred1 && \
  sudo docker compose -f deploy/docker-compose.gcp.yml down && \
  sudo docker compose -f deploy/docker-compose.gcp.yml up -d'

# Очистка Docker (если диск забит)
ssh deploy@35.208.46.164 'sudo docker system prune -f && sudo docker image prune -a -f'
```
