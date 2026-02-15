@echo off
SET "PATH_VENV=%~dp0\venv\p310_dtc"
SET "PYTHON_PATH=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
SET "REQ_FILE=%~dp0requirements.txt"
::----------------------------------------------------------------------------------------------------------------------
if not exist "%PATH_VENV%" (
    "%PYTHON_PATH%" -m venv "%PATH_VENV%"
)
::----------------------------------------------------------------------------------------------------------------------
call "%PATH_VENV%\Scripts\activate.bat"
"%PATH_VENV%\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel packaging
"%PATH_VENV%\Scripts\python.exe" -m pip install --prefer-binary -r "%REQ_FILE%"
"%PATH_VENV%\Scripts\python.exe" -m pip uninstall -y torch torchvision torchaudio >NUL 2>&1
"%PATH_VENV%\Scripts\python.exe" -m pip install --index-url https://download.pytorch.org/whl/cu121 torch==2.2.2 torchvision==0.17.2 torchaudio==2.2.2
"%PATH_VENV%\Scripts\python.exe" -c "import torch; print('CUDA:', 'OK' if torch.cuda.is_available() else 'not available')"
"%PATH_VENV%\Scripts\python.exe" -m pip install av
echo -----------------------------------------------
echo Virtual environment created and configured at: %PATH_VENV%
echo To activate, use the following command:
echo call "%PATH_VENV%\Scripts\activate.bat"
echo -----------------------------------------------