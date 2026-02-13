#!/bin/bash
set -e
# ----------------------------------------------------------------------------------------------------------------------
PATH_VENV="$HOME/venv/p311_dtc"
PYTHON_PATH="python3"
REQ_FILE="$(cd "$(dirname "$0")" && pwd)/requirements.txt"
# ----------------------------------------------------------------------------------------------------------------------
if [ ! -d "$PATH_VENV" ]; then
    echo "Creating virtual environment at $PATH_VENV ..."
    $PYTHON_PATH -m venv "$PATH_VENV"
fi
# ----------------------------------------------------------------------------------------------------------------------
source "$PATH_VENV/bin/activate"
pip install --upgrade pip setuptools wheel packaging
pip install --prefer-binary -r "$REQ_FILE"
pip uninstall -y torch torchvision torchaudio 2>/dev/null || true
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
python -c "import torch; print('PyTorch:', torch.__version__, '| CPU only (RPi4)')"
pip install av
# ----------------------------------------------------------------------------------------------------------------------
echo "-----------------------------------------------"
echo "Virtual environment created and configured at: $PATH_VENV"
echo "To activate, use the following command:"
echo "  source $PATH_VENV/bin/activate"
echo "-----------------------------------------------"