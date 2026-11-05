@echo off
:: Stop everything: DTC on the Raspberry Pi, the Unreal game, its ffmpeg, mediamtx.
python "%~dp0dtc.py" down %*
