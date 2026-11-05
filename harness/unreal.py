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
def start(res='800x600', game_args=(), wait=90, log=print):
    """mediamtx -> game -> stream check. Idempotent: a game that already streams is kept."""
    log('mediamtx: ' + ensure_mediamtx())
    if game_running():
        s = stream_ok()
        if s:
            log(f'game already running and streaming ({s})'); return s
        raise RuntimeError('Night427 runs but there is no stream (started before mediamtx?) - stop it first: session_stop.bat')
    if not os.path.isfile(GAME_EXE):
        raise RuntimeError(f'game not found: {GAME_EXE} (set NIGHT427_EXE in .env)')
    w, h = res.lower().split('x')
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
                log(f'stream {LOCAL_URL}: {s}  ({time.time() - t0:.0f} s)'); return s
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
