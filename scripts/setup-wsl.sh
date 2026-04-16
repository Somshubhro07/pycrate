#!/bin/bash
# ---------------------------------------------------------------------------
# PyCrate WSL2 Development Setup
# ---------------------------------------------------------------------------
# Sets up a WSL2 Ubuntu environment for PyCrate development and testing.
#
# Usage (from inside WSL2 Ubuntu):
#   bash scripts/setup-wsl.sh
# ---------------------------------------------------------------------------

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[setup]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
fail() { echo -e "${RED}[error]${NC} $1"; exit 1; }

# Check we're in WSL2
if [[ ! -f /proc/version ]] || ! grep -qi microsoft /proc/version; then
    fail "This script is intended for WSL2. Run from inside your WSL2 Ubuntu terminal."
fi

log "Setting up PyCrate development environment in WSL2"
echo ""

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

log "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3.11 \
    python3.11-venv \
    python3-pip \
    debootstrap \
    iproute2 \
    iptables \
    build-essential \
    curl

ok "System packages installed"

# ---------------------------------------------------------------------------
# cgroups v2 verification
# ---------------------------------------------------------------------------

log "Checking cgroups v2..."
if mount | grep -q "cgroup2"; then
    ok "cgroups v2 is available"
else
    fail "cgroups v2 not detected. Your WSL2 kernel may be too old. Update WSL2: wsl --update"
fi

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

log "Setting up Python virtual environment..."

PYCRATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PYCRATE_DIR"

if [[ ! -d ".venv" ]]; then
    python3.11 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip setuptools wheel -q
pip install -e ".[server,dev]" -q

ok "Python environment ready (.venv)"

# ---------------------------------------------------------------------------
# Data directory
# ---------------------------------------------------------------------------

sudo mkdir -p /var/lib/pycrate/images
sudo mkdir -p /var/lib/pycrate/containers

ok "Data directory created"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
log "Setup complete. To start developing:"
echo ""
echo "  cd $PYCRATE_DIR"
echo "  source .venv/bin/activate"
echo ""
echo "  # Run the CLI"
echo "  sudo .venv/bin/pycrate version"
echo "  sudo .venv/bin/pycrate pull alpine"
echo "  sudo .venv/bin/pycrate run alpine /bin/sh --name test"
echo ""
echo "  # Run the API server"
echo "  sudo .venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "  # Run tests"
echo "  sudo .venv/bin/pytest tests/ -v"
echo ""
