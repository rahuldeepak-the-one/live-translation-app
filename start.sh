#!/bin/bash
# ============================================================
# Live Translation Server — Startup Script
# Runs on the laptop with RTX 3060 GPU
# Tablet connects to http://<laptop-ip>:8080
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "======================================"
echo "  Live Translation Server"
echo "  Whisper + NLLB on Local GPU"
echo "======================================"
echo ""

# --- Create venv if needed ---
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# --- Activate venv ---
source "$VENV_DIR/bin/activate"

# --- Install dependencies ---
echo "📦 Checking dependencies..."
pip install --quiet --upgrade pip

# PyTorch with CUDA (adjust cu121 to match your CUDA version)
if ! python -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
    echo "🔧 Installing PyTorch with CUDA support..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
fi

pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

# --- Show GPU info ---
echo ""
python -c "
import torch
if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f'🖥️  GPU: {gpu} ({mem:.1f} GB)')
else:
    print('⚠️  No CUDA GPU found — running on CPU (will be slower)')
"

# --- Operator control token ---
# New every run, so a token seen once is useless next Sunday. Exported so the
# banner below and the server itself agree on it.
export CONTROL_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(3))')"

# --- Show the URLs people should open ---
# Not `hostname -I | awk '{print $1}'`: this machine has a dozen addresses
# (docker bridges, k3s/flannel, a VPN tunnel) and that would happily print an
# unreachable one. netinfo.py picks the interface carrying the default route
# and names the WiFi that phones have to be on.
echo ""
python "$SCRIPT_DIR/netinfo.py"
echo ""
echo "🎤 Starting server..."
echo "======================================"
echo ""

# --- Start server ---
python "$SCRIPT_DIR/server.py"
