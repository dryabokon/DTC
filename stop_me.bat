@echo off
:: Stop DTC started by start_me.bat: the app writes its video and report, then exits.
:: usage: stop_me.bat [port]      (default 8509, the port given to start_me.bat with --ui_port)
setlocal
SET "PORT=%~1"
if "%PORT%"=="" SET "PORT=8509"
curl.exe -s -f -X POST "http://127.0.0.1:%PORT%/shutdown" >NUL 2>&1
if errorlevel 1 (
    echo No DTC answers on port %PORT%.
    exit /b 1
)
echo DTC on port %PORT% is stopping: the video and the report are being written.
exit /b 0
