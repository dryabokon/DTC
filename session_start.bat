@echo off
:: Start a session. session_start.bat            -> DTC on the Raspberry Pi (source campus, or e.g. session_start.bat --source v05)
::                 session_start.bat unreal     -> mediamtx + Unreal game Night427 on this PC, then DTC on the Pi on its RTSP stream
:: With unreal: --res 800x600 and -ManualYawRate=20 ... go to the game, anything else to the app on the Pi. Stop: session_stop.bat
python "%~dp0dtc.py" up %*
if "%DTC_NOPAUSE%"=="" pause
