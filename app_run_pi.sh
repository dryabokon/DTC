#!/bin/bash
# Raspberry Pi (ARM): run DTC on the Pi (venv ~/venv/p311_dtc from infrastructure/build_me_arm.sh). x86 laptop / PC: app_run_x86.bat
# usage: ./app_run_pi.sh [--source campus|oxford|boats_a|boats_b|<file>|<url>] [--<config_field> value ...]
cd "$(dirname "$0")/src"
source ~/venv/p311_dtc/bin/activate
if [[ " $* " != *" --source "* && " $* " != *" --source="* ]]; then
    set -- --source campus "$@"            # Pi default: offline campus video (main.py's default config is an RTSP stream)
fi
if [[ " $* " != *" --detection_model "* ]]; then
    set -- --detection_model ../models/yolov8n_ncnn_model "$@"   # NCNN is ~3x faster than yolov8n.pt on the Pi CPU
fi
python main.py "$@"
