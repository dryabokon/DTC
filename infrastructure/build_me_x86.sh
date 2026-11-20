#!/bin/bash
# x86 PC / laptop (Ubuntu 22.04 / 24.04): the app's virtual environment, torch with CUDA 12.1 (falls back to the CPU
# without an NVIDIA GPU). The same packages as infrastructure/build_me_x86.bat (Windows); the Pi has build_me_arm.sh.
# Default location: <repo>/.venv  (set DTC_VENV to put it elsewhere; start_me.sh and "dtc local" look in the same place).
# Python: $DTC_BUILD_PYTHON, else the first of python3.10 / python3.11 / python3.12 / python3 found.
# System packages it needs (not installed here, no sudo): python3-venv (python3.X-venv), libgl1, libglib2.0-0;
# for "dtc pi ..." from this machine also putty-tools (plink / pscp).
set -e
# ----------------------------------------------------------------------------------------------------------------------
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATH_VENV="${DTC_VENV:-$ROOT/.venv}"
REQ_FILE="$ROOT/infrastructure/requirements_x86.txt"
PYTHON_PATH="$DTC_BUILD_PYTHON"
if [ -z "$PYTHON_PATH" ]; then
    for p in python3.10 python3.11 python3.12 python3; do
        if command -v "$p" >/dev/null 2>&1; then PYTHON_PATH="$p"; break; fi
    done
fi
if [ -z "$PYTHON_PATH" ]; then
    echo "No python3 found: sudo apt install python3 python3-venv"
    exit 1
fi
# ----------------------------------------------------------------------------------------------------------------------
missing=""
for pkg in libgl1 libglib2.0-0; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done
if [ -n "$missing" ]; then
    echo "OpenCV needs:$missing  ->  sudo apt install$missing"
    exit 1
fi
# ----------------------------------------------------------------------------------------------------------------------
if [ -f "$PATH_VENV/bin/python" ] && [ ! -f "$PATH_VENV/pyvenv.cfg" ]; then
    echo "$PATH_VENV/bin/python exists but is not a venv"; exit 1
fi
if [ -d "$PATH_VENV" ] && [ ! -f "$PATH_VENV/bin/python" ]; then
    echo "$PATH_VENV exists but has no bin/python (a Windows venv?): set DTC_VENV to another folder"; exit 1
fi
if [ ! -f "$PATH_VENV/bin/python" ]; then
    echo "Creating the virtual environment at $PATH_VENV with $($PYTHON_PATH --version) ..."
    "$PYTHON_PATH" -m venv "$PATH_VENV" || { echo "venv failed: sudo apt install python3-venv (or python3.X-venv)"; exit 1; }
fi
# ----------------------------------------------------------------------------------------------------------------------
PY="$PATH_VENV/bin/python"
"$PY" -m pip install --upgrade pip setuptools wheel packaging
# torch first: then ultralytics finds it installed and pip does not fetch the newest torch with its CUDA libraries (GBs) only to replace it
"$PY" -m pip install --index-url https://download.pytorch.org/whl/cu121 torch==2.2.2 torchvision==0.17.2 torchaudio==2.2.2
"$PY" -m pip install --prefer-binary -r "$REQ_FILE"
"$PY" -m pip install av
"$PY" -m pip install boxmot==15.0.2 --no-deps
"$PY" -c "import torch; print('PyTorch', torch.__version__, '| CUDA:', 'OK' if torch.cuda.is_available() else 'not available (CPU)')"
echo "-----------------------------------------------"
echo "Virtual environment: $PATH_VENV"
echo "Run the app:  ./start_me.sh --source campus      (or: ./dtc.sh local start --source campus)"
echo "-----------------------------------------------"
