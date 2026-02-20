#!/usr/bin/env bash
set -euo pipefail

echo "[secret-scan] scanning repo (excluding .env)"
rg -n --hidden \
  --glob '!.git/*' \
  --glob '!.venv/*' \
  --glob '!.env' \
  --glob '!.env.*' \
  --glob '!.pytest_cache/*' \
  --glob '!**/__pycache__/*' \
  '(?i)(api[_-]?key|secret|password|token|private[_-]?key)' \
  . || true
