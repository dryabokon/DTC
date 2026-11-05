"""Unreal game Night427 on this PC as an RTSP source for the Pi: mediamtx (:8554) -> the game on Level03 (its RTSP actor starts
ffmpeg and publishes rtsp://127.0.0.1:8554/stream) -> check with ffprobe. Order matters: the game's ffmpeg connects once at
BeginPlay and never retries, so the RTSP server must listen first. Background: .claude/skills/unreal-rtsp/SKILL.md.
Paths can be set in .env: NIGHT427_EXE, MEDIAMTX_EXE, FFMPEG_DIR."""
import os
import socket
import subprocess
import sys
import time
# ----------------------------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'infrastructure'))
import pi as pi_ssh
# ----------------------------------------------------------------------------------------------------------------------
GAME_EXE = pi_ssh.ENV.get('NIGHT427_EXE') or r'D:\Epic Games\Projects\Night427\Saved\StagedBuilds_smooth\WindowsNoEditor\Night427.exe'
MEDIAMTX_EXE = pi_ssh.ENV.get('MEDIAMTX_EXE') or r'D:\source\digits2\infrastructure\mediamtx\mediamtx.exe'
FFMPEG_DIR = pi_ssh.ENV.get('FFMPEG_DIR') or r'D:\Projects\ffmpeg7'      # the game's RTSP actor runs ffmpeg.exe from here
DEFAULT_RES = '960x540'                     # 16:9 like the game camera (and the 1280x720 stream): no black bars in the window
LEVEL = '/Game/Level03'                                                  # the only level with the RTSP actor
RTSP_PATH = 'stream'
LOCAL_URL = f'rtsp://127.0.0.1:8554/{RTSP_PATH}'
MEDIAMTX_LOG = os.path.join(ROOT, 'runs', 'mediamtx.log')
DETACHED = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
# ----------------------------------------------------------------------------------------------------------------------
def port_open(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
def _ps(cmd):
    r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout.strip()
# ----------------------------------------------------------------------------------------------------------------------
def pids(name, under=None):
    """PIDs of processes NAME.exe, optionally only those whose exe lies under folder UNDER (not someone else's ffmpeg)."""
    flt = f" | Where-Object {{ $_.Path -like '{under}\\*' }}" if under else ''
    out = _ps(f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue{flt} | ForEach-Object {{ $_.Id }}")
    return [int(x) for x in out.split() if x.isdigit()]
# ----------------------------------------------------------------------------------------------------------------------
def game_running():
    return bool(pids('Night427'))
# ----------------------------------------------------------------------------------------------------------------------
def stream_ok(url=LOCAL_URL, timeout=10):
    """ffprobe sees an H.264 stream -> 'h264,1280,720', else None."""
    try:
        r = subprocess.run([os.path.join(FFMPEG_DIR, 'ffprobe.exe'), '-v', 'error', '-rtsp_transport', 'tcp', '-show_entries',
                            'stream=codec_name,width,height', '-of', 'csv=p=0', url], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None
    except (subprocess.TimeoutExpired, OSError):
        return None
# ----------------------------------------------------------------------------------------------------------------------
def ensure_mediamtx():
    """RTSP server on :8554: reuse whatever listens there (docker container or exe), else start the standalone exe."""
    if port_open(8554):
        return 'already listening'
    if not os.path.isfile(MEDIAMTX_EXE):
        raise RuntimeError(f'mediamtx not found: {MEDIAMTX_EXE} (set MEDIAMTX_EXE in .env)')
    os.makedirs(os.path.dirname(MEDIAMTX_LOG), exist_ok=True)
    subprocess.Popen([MEDIAMTX_EXE], cwd=os.path.dirname(MEDIAMTX_EXE), stdout=open(MEDIAMTX_LOG, 'w'), stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=DETACHED)
    for _ in range(20):
        if port_open(8554):
            return 'started'
        time.sleep(0.5)
    raise RuntimeError('mediamtx did not open :8554, see ' + MEDIAMTX_LOG)
# ----------------------------------------------------------------------------------------------------------------------
def make_borderless(width, height, timeout=30):
    """UE 4.27 has no flag for a borderless window of a given size (-windowed has a frame, windowed-fullscreen fills the
    screen), so strip caption + frame from the game's window (class UnrealWindow) and set its client area to width x height.
    Returns True when done. The window keeps its position; close it with session_stop.bat or Alt+F4."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    u32.GetWindowLongPtrW.restype = u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    GWL_STYLE, WS_CAPTION, WS_THICKFRAME, WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = -16, 0x00C00000, 0x00040000, 0x00080000, 0x00020000, 0x00010000
    SWP_NOZORDER, SWP_FRAMECHANGED, SWP_NOACTIVATE = 0x0004, 0x0020, 0x0010
    ours = set(pids('Night427'))
    t0 = time.time()
    while time.time() - t0 < timeout:
        found = []
        def cb(hwnd, _):
            pid, cls = wintypes.DWORD(), ctypes.create_unicode_buffer(64)
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)); u32.GetClassNameW(hwnd, cls, 64)
            if pid.value in ours and cls.value == 'UnrealWindow' and u32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        u32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        if found:
            hwnd = found[0]
            style = u32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            u32.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX))
            r = wintypes.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
            u32.SetWindowPos(hwnd, None, r.left, r.top, int(width), int(height), SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            return True
        time.sleep(0.5); ours = set(pids('Night427'))
    return False
# ----------------------------------------------------------------------------------------------------------------------
def start(res=None, game_args=(), wait=90, borderless=False, log=print):
    """mediamtx -> game -> stream check. Idempotent: a game that already streams is kept. borderless=True: no window frame."""
    log('mediamtx: ' + ensure_mediamtx())
    if game_running():
        s = stream_ok()
        if s:
            log(f'game already running and streaming ({s})'); return s
        raise RuntimeError('Night427 runs but there is no stream (started before mediamtx?) - stop it first: session_stop.bat')
    if not os.path.isfile(GAME_EXE):
        raise RuntimeError(f'game not found: {GAME_EXE} (set NIGHT427_EXE in .env)')
    w, h = (res or DEFAULT_RES).lower().split('x')
    args = [GAME_EXE, LEVEL, '-windowed', f'-ResX={int(w)}', f'-ResY={int(h)}'] + list(game_args)
    log('game: ' + ' '.join(args[1:]))
    subprocess.Popen(args, cwd=os.path.dirname(GAME_EXE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,   # the game has its
                     stderr=subprocess.DEVNULL, creationflags=DETACHED)                                           # own log file
    t0 = time.time()
    while time.time() - t0 < wait:              # ~15-30 s: load the level, BeginPlay starts ffmpeg
        time.sleep(3)
        if not game_running():
            raise RuntimeError('the game exited while loading')
        if pids('ffmpeg', FFMPEG_DIR):
            s = stream_ok()
            if s:
                log(f'stream {LOCAL_URL}: {s}  ({time.time() - t0:.0f} s)')
                if borderless:
                    log(f'window: borderless {w}x{h}' if make_borderless(w, h) else 'window: game window not found, frame kept')
                return s
    raise RuntimeError(f'no stream after {wait} s - look for LogRTSPPusher in the game log '
                       f'({os.path.join(os.path.dirname(GAME_EXE), "Night427", "Saved", "Logs", "Night427.log")})')
# ----------------------------------------------------------------------------------------------------------------------
def stop(log=print):
    """Close the game window (its EndPlay closes ffmpeg), then kill leftovers: the game, its ffmpeg, the standalone mediamtx.
    A mediamtx docker container is left alone (the Fly2 simulator uses it)."""
    if game_running():
        subprocess.run(['taskkill', '/IM', 'Night427.exe'], capture_output=True)          # WM_CLOSE: a clean exit
        for _ in range(20):
            if not game_running():
                break
            time.sleep(0.5)
        if game_running():
            subprocess.run(['taskkill', '/F', '/T', '/IM', 'Night427.exe'], capture_output=True)
        log('game: stopped')
    else:
        log('game: not running')
    time.sleep(1)
    left = pids('ffmpeg', FFMPEG_DIR)
    for pid in left:
        subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
    if left:
        log(f'ffmpeg: killed {len(left)} left over')
    if pids('mediamtx'):
        subprocess.run(['taskkill', '/F', '/IM', 'mediamtx.exe'], capture_output=True)
        log('mediamtx: stopped')
    elif port_open(8554):
        log('mediamtx: :8554 still served by something else (docker container?) - left alone')
    else:
        log('mediamtx: not running')
    return not game_running() and not pids('ffmpeg', FFMPEG_DIR) and not pids('mediamtx')
# ----------------------------------------------------------------------------------------------------------------------
def rtsp_url_for(pi):
    """The stream URL as the Pi must use it (this PC's address on the Pi's network)."""
    return f'rtsp://{pi.laptop_ip()}:8554/{RTSP_PATH}'
