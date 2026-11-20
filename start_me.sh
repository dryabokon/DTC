#!/bin/bash
# x86 PC / laptop (Ubuntu): run DTC here, in the foreground. The same as start_me.bat on Windows.
# usage: ./start_me.sh [--source campus|camera|oxford|/dev/video0|<file>|<url>] [--<config_field> value ...]
#        UI on http://localhost:8509 (--ui_port 8510 when 8509 is taken)
# venv: $DTC_PYTHON, else $DTC_VENV/bin/python, else <repo>/.venv  (built by infrastructure/build_me_x86.sh)
# Stop: Ctrl+C here, or ./stop_me.sh from another terminal (saves the video and the report first).
# Detection: yolov8n.pt (downloaded on first use; CUDA when available) - the Pi uses the NCNN export instead.
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$DTC_PYTHON"
if [ -z "$PY" ] && [ -n "$DTC_VENV" ]; then PY="$DTC_VENV/bin/python"; fi
if [ -z "$PY" ]; then PY="$ROOT/.venv/bin/python"; fi
if [ ! -x "$PY" ]; then
    echo "No x86 venv at $PY - run infrastructure/build_me_x86.sh first, or set DTC_PYTHON / DTC_VENV"
    exit 1
fi
DEFAULTS=()
case " $* " in
    *" --source "*|*" --source="*|*" --list-sources "*) ;;
    *) DEFAULTS=(--source campus) ;;
esac
cd "$ROOT/src" || exit 1
exec "$PY" main.py "${DEFAULTS[@]}" "$@"
