import os
import platform
import subprocess
import sys
import time
# ----------------------------------------------------------------------------------------------------------------------
# Where the app runs: the Raspberry Pi 5 (ARM, Linux, CPU only) or an x86 laptop / PC (Windows or Linux, CUDA if present).
# The app is the same code on both; what differs at run time lives here (process checks, detaching a child, the name).
# What differs at start-up (venv, default detector) lives in the launchers: app_run_pi.sh (ARM) / app_run_x86.bat (x86).
# ----------------------------------------------------------------------------------------------------------------------
IS_WINDOWS = sys.platform == 'win32'
MACHINE = platform.machine().lower()
IS_ARM = MACHINE in ('aarch64', 'arm64') or MACHINE.startswith('arm')
# ----------------------------------------------------------------------------------------------------------------------
def is_raspberry_pi():
    try:
        with open('/proc/device-tree/model') as f:
            return 'raspberry pi' in f.read().lower()
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
def name():
    """'pi' | 'arm' | 'x86' - written into run provenance and /status."""
    return 'pi' if is_raspberry_pi() else ('arm' if IS_ARM else 'x86')
# ----------------------------------------------------------------------------------------------------------------------
def replace_file(src, dst, tries=50, wait=0.02):
    """os.replace that survives a reader: on Windows the rename fails (PermissionError) while another process or thread has
    dst open - the Runs tab polling progress.json, a test reading it - so it tries again for about a second."""
    for k in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not IS_WINDOWS or k == tries - 1:
                raise
            time.sleep(wait)
# ----------------------------------------------------------------------------------------------------------------------
def pid_alive(pid):
    """Is the process running? Not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT sent to process group pid
    (a worker started in its own group, e.g. the benchmark's, takes it as Ctrl+C = cancel), not a check."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259              # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
def detached_kwargs():
    """Popen kwargs for a child that outlives the parent's console / session (Ctrl+C in the app does not reach it)."""
    if IS_WINDOWS:
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}
# ----------------------------------------------------------------------------------------------------------------------
