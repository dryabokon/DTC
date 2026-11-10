@echo off
:: x86 laptop / PC (Windows): the app's virtual environment, torch with CUDA 12.1 (falls back to the CPU without an NVIDIA GPU).
:: Default location: <repo>\.venv  (set DTC_VENV to put it elsewhere; app_run_x86.bat and "dtc local" look in the same place).
:: The Raspberry Pi (ARM) has its own: infrastructure/build_me_arm.sh
SET "PATH_VENV=%DTC_VENV%"
if "%PATH_VENV%"=="" SET "PATH_VENV=%~dp0..\.venv"
SET "PYTHON_PATH=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
SET "REQ_FILE=%~dp0requirements_x86.txt"
::----------------------------------------------------------------------------------------------------------------------
if not exist "%PYTHON_PATH%" (
    echo Python 3.10 not found at %PYTHON_PATH%
    exit /b 1
)
if not exist "%PATH_VENV%\Scripts\python.exe" (
    "%PYTHON_PATH%" -m venv "%PATH_VENV%"
)
::----------------------------------------------------------------------------------------------------------------------
SET "PY=%PATH_VENV%\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip setuptools wheel packaging
"%PY%" -m pip install --prefer-binary -r "%REQ_FILE%"
"%PY%" -m pip uninstall -y torch torchvision torchaudio >NUL 2>&1
"%PY%" -m pip install --index-url https://download.pytorch.org/whl/cu121 torch==2.2.2 torchvision==0.17.2 torchaudio==2.2.2
"%PY%" -m pip install av
"%PY%" -m pip install boxmot==15.0.2 --no-deps
"%PY%" -c "import torch; print('PyTorch', torch.__version__, '| CUDA:', 'OK' if torch.cuda.is_available() else 'not available (CPU)')"
echo -----------------------------------------------
echo Virtual environment: %PATH_VENV%
echo Run the app:  app_run_x86.bat --source campus      (or: dtc local start --source campus)
echo -----------------------------------------------
