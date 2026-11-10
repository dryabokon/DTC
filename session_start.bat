@echo off
:: Start a session. session_start.bat            -> DTC on the Raspberry Pi (source campus, or e.g. session_start.bat --source v05)
::                 session_start.bat unreal     -> mediamtx + Unreal game Night427 on this PC, then DTC on the Pi on its RTSP stream
:: Add local to run DTC on this PC (x86) instead of the Pi: session_start.bat local [unreal] (stop: session_stop.bat)
:: With unreal: --res 960x540 (default, 16:9), --borderless and -ManualYawRate=20 ... go to the game, anything else to the app on the Pi. Stop: session_stop.bat
python "%~dp0dtc.py" up %*
