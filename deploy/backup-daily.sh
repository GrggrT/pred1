#!/bin/bash
###############################################################################
# Daily automatic PostgreSQL backup with 7-day retention
#
# Setup (run once on GCP):
#   sudo mkdir -p /home/deploy/backups
#   sudo chown deploy:deploy /home/deploy/backups
#   chmod +x /home/deploy/pred1/deploy/backup-daily.sh
#   crontab -e  # add:  0 2 * * * /home/deploy/pred1/deploy/backup-daily.sh
#
# Restore:
#   gzip -dc /home/deploy/backups/fc_mvp-2026-03-17.sql.gz | \
#     sudo docker compose -f /home/deploy/pred1/deploy/docker-compose.gcp.yml \
#     exec -T db psql -U postgres -d fc_mvp
###############################################################################
set -euo pipefail

BACKUP_DIR="/home/deploy/backups"
COMPOSE_FILE="/home/deploy/pred1/deploy/docker-compose.gcp.yml"
DB_NAME="fc_mvp"
DB_USER="postgres"
RETENTION_DAYS=7
DATE=$(date +%Y-%m-%d)
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}-${DATE}.sql.gz"
LOG_FILE="${BACKUP_DIR}/backup.log"

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "=== Backup started ==="

# Check if db container is running
if ! sudo docker compose -f "${COMPOSE_FILE}" ps db --format '{{.Status}}' 2>/dev/null | grep -qi "up\|healthy"; then
    log "ERROR: db container is not running"
    exit 1
fi

# Perform backup
if sudo docker compose -f "${COMPOSE_FILE}" exec -T db \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" \
    --no-owner --no-privileges \
    | gzip > "${BACKUP_FILE}"; then
    SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    log "Backup OK: ${BACKUP_FILE} (${SIZE})"
else
    log "ERROR: pg_dump failed"
    rm -f "${BACKUP_FILE}"
    exit 1
fi

# Verify backup is not empty
if [ ! -s "${BACKUP_FILE}" ]; then
    log "ERROR: backup file is empty"
    rm -f "${BACKUP_FILE}"
    exit 1
fi

# Cleanup old backups (keep last N days)
DELETED=$(find "${BACKUP_DIR}" -name "${DB_NAME}-*.sql.gz" -mtime +${RETENTION_DAYS} -print -delete | wc -l)
if [ "${DELETED}" -gt 0 ]; then
    log "Cleaned up ${DELETED} old backup(s)"
fi

# Trim log file (keep last 100 lines)
if [ -f "${LOG_FILE}" ]; then
    tail -100 "${LOG_FILE}" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
fi

log "=== Backup complete ==="
