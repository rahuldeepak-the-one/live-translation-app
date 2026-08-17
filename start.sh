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

# --- Show local IP for tablet connection ---
echo ""
LOCAL_IP=$(hostname -I | awk '{print $1}')
echo "📺 Projector/tablet:  http://${LOCAL_IP}:8080/display"
echo "📱 Personal phones:   http://${LOCAL_IP}:8080/view"
echo "🎤 Microphone page:   http://${LOCAL_IP}:8080/mic"
echo ""
echo "🎤 Starting server..."
echo "======================================"
echo ""

# --- Start server ---
python "$SCRIPT_DIR/server.py"
