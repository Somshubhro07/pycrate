#!/bin/bash
# -----------------------------------------------------------------------------
# PyCrate EC2 Bootstrap Script
# -----------------------------------------------------------------------------
# Run this on a fresh Ubuntu 22.04 EC2 instance to set up the PyCrate daemon.
#
# Usage:
#   chmod +x setup-ec2.sh
#   sudo ./setup-ec2.sh
#
# Prerequisites:
#   - Ubuntu 22.04 LTS (cgroups v2 enabled by default)
#   - t2.micro or larger
#   - Security group: port 8000 open to Vercel IPs, port 22 open to your IP
# -----------------------------------------------------------------------------

set -euo pipefail

PYCRATE_DIR="/opt/pycrate"
PYCRATE_DATA="/var/lib/pycrate"
REPO_URL="https://github.com/Somshubhro07/pycrate.git"

echo "=== PyCrate EC2 Setup ==="

# --- System packages ---
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3.11 \
    python3.11-venv \
    python3-pip \
    git \
    iptables \
    iproute2 \
    curl \
    jq

# --- Verify cgroups v2 ---
echo "[2/8] Verifying cgroups v2..."
if mount | grep -q "cgroup2"; then
    echo "  cgroups v2: OK"
else
    echo "  ERROR: cgroups v2 not found. PyCrate requires cgroups v2."
    echo "  Ubuntu 22.04+ should have this by default."
    exit 1
fi

# --- Clone repository ---
echo "[3/8] Cloning PyCrate repository..."
if [ -d "$PYCRATE_DIR" ]; then
    echo "  Directory exists, pulling latest..."
    cd "$PYCRATE_DIR"
    git pull origin main
else
    git clone "$REPO_URL" "$PYCRATE_DIR"
fi

# --- Python virtual environment ---
echo "[4/8] Setting up Python environment..."
cd "$PYCRATE_DIR"
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# --- Data directories ---
echo "[5/8] Creating data directories..."
mkdir -p "$PYCRATE_DATA/containers"
mkdir -p "$PYCRATE_DATA/images"

# --- Environment file ---
echo "[6/8] Checking environment configuration..."
if [ ! -f "$PYCRATE_DIR/.env" ]; then
    cp "$PYCRATE_DIR/.env.example" "$PYCRATE_DIR/.env"
    echo "  Created .env from template."
    echo "  IMPORTANT: Edit /opt/pycrate/.env with your MongoDB URI and API key."
else
    echo "  .env already exists, skipping."
fi

# --- Enable cgroup controllers ---
echo "[7/8] Enabling cgroup controllers..."
if [ -f /sys/fs/cgroup/cgroup.subtree_control ]; then
    echo "+cpu +memory +pids" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
    echo "  cgroup controllers enabled."
fi

# --- systemd service ---
echo "[8/8] Installing systemd service..."
cp "$PYCRATE_DIR/infrastructure/pycrate.service" /etc/systemd/system/pycrate.service
systemctl daemon-reload
systemctl enable pycrate

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/pycrate/.env with your MongoDB URI and API key"
echo "  2. Start the service:  sudo systemctl start pycrate"
echo "  3. Check status:       sudo systemctl status pycrate"
echo "  4. View logs:          sudo journalctl -u pycrate -f"
echo "  5. Test the API:       curl http://localhost:8000/api/health"
echo ""
