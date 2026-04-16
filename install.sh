#!/bin/bash
# ---------------------------------------------------------------------------
# PyCrate Install Script
# ---------------------------------------------------------------------------
# Downloads and installs PyCrate on a Linux machine.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/Somshubhro07/pycrate/main/install.sh | sudo bash
#
# What it does:
#   1. Checks for Linux + Python 3.11+
#   2. Installs system dependencies (debootstrap for Ubuntu/Debian images)
#   3. Installs PyCrate via pip
#   4. Verifies installation
# ---------------------------------------------------------------------------

set -euo pipefail

REPO="https://github.com/Somshubhro07/pycrate.git"
MIN_PYTHON_VERSION="3.11"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "${CYAN}[pycrate]${NC} $1"; }
ok()    { echo -e "${GREEN}[ok]${NC} $1"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $1"; }
fail()  { echo -e "${RED}[error]${NC} $1"; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

log "PyCrate Installer"
echo ""

# Check OS
if [[ "$(uname -s)" != "Linux" ]]; then
    fail "PyCrate requires Linux. On Windows, use WSL2: wsl --install -d Ubuntu-22.04"
fi

# Check root
if [[ "$EUID" -ne 0 ]]; then
    fail "This script must be run as root (sudo)."
fi

# Check Python
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    fail "Python 3.11+ is required but not found. Install it first:\n  sudo apt-get install python3.11 python3.11-venv"
fi

ok "Python: $($PYTHON_CMD --version)"

# Check pip
if ! "$PYTHON_CMD" -m pip --version &>/dev/null; then
    log "Installing pip..."
    "$PYTHON_CMD" -m ensurepip --upgrade 2>/dev/null || apt-get install -y -qq python3-pip
fi

# ---------------------------------------------------------------------------
# Install system dependencies
# ---------------------------------------------------------------------------

log "Installing system dependencies..."

if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq \
        debootstrap \
        iproute2 \
        iptables \
        curl \
        2>/dev/null
    ok "System dependencies installed (apt)"
elif command -v dnf &>/dev/null; then
    dnf install -y -q \
        debootstrap \
        iproute \
        iptables \
        curl \
        2>/dev/null
    ok "System dependencies installed (dnf)"
elif command -v yum &>/dev/null; then
    yum install -y -q \
        debootstrap \
        iproute \
        iptables \
        curl \
        2>/dev/null
    ok "System dependencies installed (yum)"
else
    warn "Unknown package manager. Please install manually: debootstrap, iproute2, iptables"
fi

# ---------------------------------------------------------------------------
# Install PyCrate
# ---------------------------------------------------------------------------

log "Installing PyCrate..."

"$PYTHON_CMD" -m pip install --upgrade pip setuptools wheel -q

# Install from GitHub
"$PYTHON_CMD" -m pip install "git+${REPO}" -q

ok "PyCrate installed"

# ---------------------------------------------------------------------------
# Create data directory
# ---------------------------------------------------------------------------

mkdir -p /var/lib/pycrate/images
mkdir -p /var/lib/pycrate/containers

ok "Data directory created at /var/lib/pycrate"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

if command -v pycrate &>/dev/null; then
    ok "Installation verified"
    echo ""
    pycrate version
    echo ""
    log "Quick start:"
    echo ""
    echo "  sudo pycrate pull alpine"
    echo "  sudo pycrate run alpine /bin/sh --name test"
    echo "  sudo pycrate ps"
    echo "  sudo pycrate stop test"
    echo "  sudo pycrate rm test"
    echo ""
    log "For the web dashboard:"
    echo "  sudo pycrate dashboard"
    echo ""
else
    warn "pycrate command not found in PATH. You may need to add ~/.local/bin to PATH:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
fi
