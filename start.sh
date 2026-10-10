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
echo "  Whisper + IndicTrans2 on Local GPU"
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

# --- TLS certificate ---
# Optional since the capture page was deleted: nothing needs a secure browser
# context any more, so a certless boot is a complete service and the cert only
# keeps the caption stream off the clear. Regenerated only when absent; the SAN
# covers whatever addresses the machine had at generation time, so re-run
# make-cert.sh by hand after moving to a different WiFi.
if [ ! -f "$SCRIPT_DIR/certs/server.crt" ]; then
    "$SCRIPT_DIR/make-cert.sh"
    echo ""
fi

# --- Show the URLs people should open ---
# Not `hostname -I | awk '{print $1}'`: this machine has a dozen addresses
# (docker bridges, k3s/flannel, a VPN tunnel) and that would happily print an
# unreachable one. netinfo.py picks the interface carrying the default route
# and names the WiFi that phones have to be on.
echo ""
python "$SCRIPT_DIR/netinfo.py"
echo ""
# The server has no microphone of its own. feed-mic.py is the only ingress, and
# it is deliberately NOT launched from here: it wants its own terminal for the
# level meter, and coupling its lifetime to the server's would hide a mixer
# that got unplugged behind a wall of uvicorn logs. It reconnects on a 2s
# timer, so it can be started before or after this.
echo "🎤 In a second terminal, feed the mixer in:"
echo "      ./feed-mic.py yamaha"
echo ""
echo "Starting server..."
echo "======================================"
echo ""

# --- Start server ---
python "$SCRIPT_DIR/server.py"
