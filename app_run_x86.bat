@echo off
:: x86 laptop / PC (Windows): run DTC here, in the foreground.  The Raspberry Pi (ARM) has its own launcher: app_run_pi.sh
:: usage: app_run_x86.bat [--source campus|camera|oxford|<file>|<url>] [--<config_field> value ...]
::        UI on http://localhost:8509 (--ui_port 8510 when 8509 is taken by the Pi tunnel or the Fly2 UI)
:: venv: DTC_PYTHON, else %DTC_VENV%\Scripts\python.exe, else <repo>\.venv  (built by infrastructure\build_me_x86.bat)
:: Detection: yolov8n.pt (downloaded on first use; CUDA when available) - the Pi uses the NCNN export instead.
setlocal
SET "PY=%DTC_PYTHON%"
if "%PY%"=="" if not "%DTC_VENV%"=="" SET "PY=%DTC_VENV%\Scripts\python.exe"
if "%PY%"=="" SET "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo No x86 venv at %PY% - run infrastructure\build_me_x86.bat first, or set DTC_PYTHON / DTC_VENV
    exit /b 1
)
SET "DEFAULTS="
echo.%* | findstr /C:"--source" >NUL || SET "DEFAULTS=--source campus"
cd /d "%~dp0src"
"%PY%" main.py %DEFAULTS% %*
exit /b %ERRORLEVEL%
