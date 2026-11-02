#!/bin/bash
# usage: ./run_me_arm.sh [--source campus|oxford|boats_a|boats_b|<file>|<url>] [--<config_field> value ...]
cd "$(dirname "$0")/src"
source ~/venv/p311_dtc/bin/activate
if [[ " $* " != *" --source "* && " $* " != *" --source="* ]]; then
    set -- --source campus "$@"            # Pi default: offline campus video (main.py's default config is an RTSP stream)
fi
python main.py "$@"
