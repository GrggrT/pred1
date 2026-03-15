#!/bin/bash
###############################################################################
# Backup current database and restore to remote Oracle Cloud VM
#
# Usage:
#   ./deploy/backup-and-restore.sh backup              # Export local DB
#   ./deploy/backup-and-restore.sh restore <server-ip> [ssh-key]  # Restore to remote
#   ./deploy/backup-and-restore.sh full <server-ip> [ssh-key]     # Backup + restore
###############################################################################
set -euo pipefail

BACKUP_FILE="deploy/db_backup.sql.gz"
SSH_OPTS="-o StrictHostKeyChecking=no"

do_backup() {
  echo "=== Backing up local database ==="
  docker compose exec -T db pg_dump -U postgres -d fc_mvp \
    --no-owner --no-privileges --clean --if-exists \
    | gzip > "${BACKUP_FILE}"
  SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
  echo "  Backup saved: ${BACKUP_FILE} (${SIZE})"
}

do_restore() {
  local SERVER_IP="${1:?Usage: $0 restore <server-ip> [ssh-key]}"
  local SSH_KEY="${2:-~/.ssh/id_rsa}"
  local REMOTE_DIR="/home/ubuntu/pred1"

  echo "=== Restoring database to ${SERVER_IP} ==="

  if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: ${BACKUP_FILE} not found. Run '$0 backup' first."
    exit 1
  fi

  echo "  Uploading backup..."
  scp ${SSH_OPTS} -i "${SSH_KEY}" "${BACKUP_FILE}" "ubuntu@${SERVER_IP}:/tmp/db_backup.sql.gz"

  echo "  Restoring into remote PostgreSQL..."
  ssh ${SSH_OPTS} -i "${SSH_KEY}" "ubuntu@${SERVER_IP}" bash -c "'
    cd ${REMOTE_DIR}
    gunzip -c /tmp/db_backup.sql.gz | docker compose -f deploy/docker-compose.prod.yml exec -T db psql -U postgres -d fc_mvp
    rm -f /tmp/db_backup.sql.gz
  '"

  echo "  Running migrations on remote..."
  ssh ${SSH_OPTS} -i "${SSH_KEY}" "ubuntu@${SERVER_IP}" \
    "cd ${REMOTE_DIR} && docker compose -f deploy/docker-compose.prod.yml exec -T app alembic upgrade head"

  echo "=== Restore complete ==="
}

case "${1:-}" in
  backup)
    do_backup
    ;;
  restore)
    do_restore "${2:-}" "${3:-~/.ssh/id_rsa}"
    ;;
  full)
    do_backup
    do_restore "${2:-}" "${3:-~/.ssh/id_rsa}"
    ;;
  *)
    echo "Usage: $0 {backup|restore|full} [server-ip] [ssh-key]"
    exit 1
    ;;
esac
