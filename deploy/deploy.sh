#!/bin/bash
###############################################################################
# deploy.sh — Deploy pred1 to Oracle Cloud ARM VM
#
# Prerequisites:
#   1. Oracle Cloud account created (cloud.oracle.com)
#   2. ARM VM created (VM.Standard.A1.Flex, Ubuntu 22.04, cloud-init applied)
#   3. SSH key configured
#   4. Security List: ingress rule for TCP port 8000 from 0.0.0.0/0
#
# Usage:
#   ./deploy/deploy.sh <server-ip> [ssh-key-path]
#
# Example:
#   ./deploy/deploy.sh 129.153.47.123
#   ./deploy/deploy.sh 129.153.47.123 ~/.ssh/oracle_key
###############################################################################
set -euo pipefail

SERVER_IP="${1:?Usage: $0 <server-ip> [ssh-key-path]}"
SSH_KEY="${2:-~/.ssh/id_rsa}"
SSH_USER="ubuntu"
REMOTE_DIR="/home/ubuntu/pred1"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

ssh_cmd() {
  ssh $SSH_OPTS -i "$SSH_KEY" "${SSH_USER}@${SERVER_IP}" "$@"
}

scp_cmd() {
  scp $SSH_OPTS -i "$SSH_KEY" "$@"
}

echo "============================================"
echo " pred1 Deployment to Oracle Cloud ARM"
echo " Server: ${SERVER_IP}"
echo "============================================"

# --- Step 1: Check connectivity ---
echo ""
echo "[1/7] Checking SSH connectivity..."
if ! ssh_cmd "echo ok" >/dev/null 2>&1; then
  echo "ERROR: Cannot connect to ${SSH_USER}@${SERVER_IP}"
  echo "  - Check that the VM is running"
  echo "  - Check the SSH key: ${SSH_KEY}"
  echo "  - Check Security List: allow TCP 22 inbound"
  exit 1
fi
echo "  Connected OK."

# --- Step 2: Check Docker ---
echo ""
echo "[2/7] Checking Docker on remote..."
if ! ssh_cmd "docker --version" >/dev/null 2>&1; then
  echo "  Docker not found. Installing via cloud-init script..."
  scp_cmd "deploy/cloud-init.sh" "${SSH_USER}@${SERVER_IP}:/tmp/cloud-init.sh"
  ssh_cmd "chmod +x /tmp/cloud-init.sh && sudo /tmp/cloud-init.sh"
fi
DOCKER_VER=$(ssh_cmd "docker --version" 2>/dev/null)
echo "  ${DOCKER_VER}"

# --- Step 3: Sync project files ---
echo ""
echo "[3/7] Syncing project files..."
# Create .deployignore-style excludes
EXCLUDES=(
  --exclude='.git'
  --exclude='__pycache__'
  --exclude='.pytest_cache'
  --exclude='*.pyc'
  --exclude='.venv'
  --exclude='pgdata'
  --exclude='.claude'
  --exclude='memory'
  --exclude='.architect'
  --exclude='results'
)

# Use rsync if available, else fall back to scp
if command -v rsync >/dev/null 2>&1; then
  rsync -avz --delete "${EXCLUDES[@]}" \
    -e "ssh $SSH_OPTS -i $SSH_KEY" \
    ./ "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/"
else
  echo "  rsync not found, using scp (slower)..."
  ssh_cmd "mkdir -p ${REMOTE_DIR}"
  scp_cmd -r \
    app/ alembic/ alembic.ini requirements.txt requirements.lock \
    deploy/ Dockerfile .env.example \
    "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/"
fi
echo "  Files synced."

# --- Step 4: Ensure .env exists ---
echo ""
echo "[4/7] Checking .env configuration..."
if ! ssh_cmd "test -f ${REMOTE_DIR}/.env"; then
  echo "  .env not found — copying from .env.example"
  ssh_cmd "cp ${REMOTE_DIR}/.env.example ${REMOTE_DIR}/.env"
  echo ""
  echo "  !! IMPORTANT: Edit .env on the server before proceeding !!"
  echo "     ssh ${SSH_USER}@${SERVER_IP}"
  echo "     nano ${REMOTE_DIR}/.env"
  echo "     # Set: ADMIN_TOKEN, API_FOOTBALL_KEY, DB_PASSWORD"
  echo ""
  read -p "  Press Enter after editing .env (or Ctrl+C to abort)..."
fi
echo "  .env OK."

# --- Step 5: Build and start ---
echo ""
echo "[5/7] Building Docker images (ARM64)..."
ssh_cmd "cd ${REMOTE_DIR} && docker compose -f deploy/docker-compose.prod.yml build"

echo ""
echo "[6/7] Starting services..."
ssh_cmd "cd ${REMOTE_DIR} && docker compose -f deploy/docker-compose.prod.yml up -d"

echo ""
echo "[7/7] Running database migrations..."
sleep 5
ssh_cmd "cd ${REMOTE_DIR} && docker compose -f deploy/docker-compose.prod.yml exec -T app alembic upgrade head"

# --- Final check ---
echo ""
echo "============================================"
echo " Deployment complete!"
echo "============================================"
echo ""
ssh_cmd "cd ${REMOTE_DIR} && docker compose -f deploy/docker-compose.prod.yml ps"
echo ""
echo "Dashboard: http://${SERVER_IP}:8000/ui"
echo "Health:    http://${SERVER_IP}:8000/health"
echo ""
echo "Check logs: ssh ${SSH_USER}@${SERVER_IP} 'cd ${REMOTE_DIR} && docker compose -f deploy/docker-compose.prod.yml logs -f'"
