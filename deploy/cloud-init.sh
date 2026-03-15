#!/bin/bash
###############################################################################
# Cloud-init script for Oracle Cloud ARM VM
# Paste this into "Cloud-init script" field when creating the instance,
# or run manually on a fresh Ubuntu 22.04+ ARM VM.
###############################################################################
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "=== [1/5] System update ==="
apt-get update -qq
apt-get upgrade -y -qq

echo "=== [2/5] Install Docker ==="
apt-get install -y -qq ca-certificates curl gnupg lsb-release git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "=== [3/5] Docker post-install ==="
systemctl enable docker
systemctl start docker
# Allow default 'ubuntu' user to run docker without sudo
usermod -aG docker ubuntu || true

echo "=== [4/5] Open firewall port 8000 ==="
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4 2>/dev/null || true

echo "=== [5/5] Create project directory ==="
mkdir -p /home/ubuntu/pred1
chown ubuntu:ubuntu /home/ubuntu/pred1

echo "=== Cloud-init complete ==="
echo "Next steps:"
echo "  1. SSH into the VM: ssh ubuntu@<public-ip>"
echo "  2. Transfer the project: scp -r ./* ubuntu@<public-ip>:~/pred1/"
echo "  3. cd ~/pred1 && cp .env.example .env && nano .env"
echo "  4. docker compose -f deploy/docker-compose.prod.yml up -d"
echo "  5. docker compose -f deploy/docker-compose.prod.yml exec app alembic upgrade head"
