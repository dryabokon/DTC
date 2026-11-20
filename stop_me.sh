#!/bin/bash
# Stop DTC started by start_me.sh: the app writes its video and report, then exits. The same as stop_me.bat on Windows.
# usage: ./stop_me.sh [port]      (default 8509, the port given to start_me.sh with --ui_port)
PORT="${1:-8509}"
if ! curl -s -f -X POST "http://127.0.0.1:$PORT/shutdown" >/dev/null 2>&1; then
    echo "No DTC answers on port $PORT."
    exit 1
fi
echo "DTC on port $PORT is stopping: the video and the report are being written."
