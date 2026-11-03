"""Start / stop the Fly2 camera simulator on this PC (harness/sim_fly2.py in Fly2's venv) and record reference clips.
Paths can be set in .env: FLY2_ROOT (default D:\\source\\digits\\Fly2), FLY2_PYTHON (default D:\\venv\\p310_ms_airsim_win\\Scripts\\python.exe)."""
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
# ----------------------------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'infrastructure'))
import pi as pi_ssh
# ----------------------------------------------------------------------------------------------------------------------
FLY2_ROOT = pi_ssh.ENV.get('FLY2_ROOT') or r'D:\source\digits\Fly2'
FLY2_PYTHON = pi_ssh.ENV.get('FLY2_PYTHON') or r'D:\venv\p310_ms_airsim_win\Scripts\python.exe'
UI = 'http://127.0.0.1:8509'
RTSP_PATH = 'sim'
LOG = os.path.join(ROOT, 'runs', 'sim.log')
CLIPS = os.path.join(ROOT, 'clips')                  # local reference clips, not in git
# ----------------------------------------------------------------------------------------------------------------------
def port_open(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
def running():
    return port_open(8509)
# ----------------------------------------------------------------------------------------------------------------------
def ensure_mediamtx():
    """RTSP server on :8554 (the mediamtx docker container on this PC)."""
    if port_open(8554):
        return True
    subprocess.run(['docker', 'start', 'mediamtx'], capture_output=True, stdin=subprocess.DEVNULL)
    for _ in range(20):
        if port_open(8554):
            return True
        time.sleep(0.5)
    return False
# ----------------------------------------------------------------------------------------------------------------------
def start(stream=True, record=None, maneuver='none', seconds=0, circle='20,0,8,6', speed=2.0, wait=True, log=LOG):
    """Launch the simulator. With record=dir and seconds>0 and wait=True it blocks until the clip is written."""
    if running():
        raise RuntimeError('something already listens on :8509 on this PC (the simulator? stop it first: dtc sim stop)')
    if stream and not ensure_mediamtx():
        raise RuntimeError('no RTSP server on :8554 - start the mediamtx docker container')
    args = [FLY2_PYTHON, '-u', os.path.join(ROOT, 'harness', 'sim_fly2.py'), '--maneuver', maneuver, '--circle', circle, '--speed', str(speed)]
    if stream:
        args += ['--rtsp', f'rtsp://127.0.0.1:8554/{RTSP_PATH}']
    if record:
        args += ['--record', os.path.abspath(record)]
    if seconds:
        args += ['--seconds', str(seconds)]
    os.makedirs(os.path.dirname(log), exist_ok=True)
    env = dict(os.environ, FLY2_ROOT=FLY2_ROOT, PYTHONUNBUFFERED='1')
    proc = subprocess.Popen(args, stdout=open(log, 'w'), stderr=subprocess.STDOUT, env=env, cwd=os.path.join(FLY2_ROOT, 'src'),
                            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0), stdin=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 120:                  # wait until frames flow (Fly2 takes ~30 s to load and take off)
        txt = open(log, encoding='utf-8', errors='replace').read()
        if 'Traceback' in txt or proc.poll() is not None and '[  SIM  ] stopped' not in txt:
            raise RuntimeError('simulator failed to start, see ' + log)
        if '[  SIM  ] airborne' in txt:
            break
        time.sleep(1)
    else:
        raise RuntimeError('simulator did not come up in 120 s, see ' + log)
    if record and seconds and wait:
        proc.wait(timeout=seconds + 120)
    return proc
# ----------------------------------------------------------------------------------------------------------------------
def stop(timeout=20):
    """Esc in the Fly2 UI ends its loop; the wrapper then closes the stream and exits."""
    if not running():
        return True
    try:
        urllib.request.urlopen(urllib.request.Request(UI + '/key_press', data=urllib.parse.urlencode({'key': 'Escape'}).encode()), timeout=3).read()
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout and running():
        time.sleep(0.5)
    return not running()
# ----------------------------------------------------------------------------------------------------------------------
def rtsp_url_for(pi):
    """The stream URL as the Pi must use it (this laptop's address on the Pi's network)."""
    return f'rtsp://{pi.laptop_ip()}:8554/{RTSP_PATH}'
