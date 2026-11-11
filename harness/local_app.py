"""Control the DTC app on this x86 laptop / PC: the same interface as harness/pi_app.Pi (start / stop / status / logs /
HTTP calls / files), so `dtc local ...`, `dtc test ... --local` and `dtc bench ... --local` run the suites here.

The app runs in the x86 venv (infrastructure/build_me_x86.bat): DTC_PYTHON, else DTC_VENV\\Scripts\\python.exe, else <repo>\\.venv.
Unlike the Pi, it runs this working tree (uncommitted changes included) - nothing is synced.
The app is started by a small detached wrapper (this file, --wrap) that writes the app's pid and, at the end, its exit code
into the log, so a later `dtc local stop` (another process) still finds both.
"""
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from harness.pi_app import AppClient                        # noqa: E402
from src import utils_platform                              # noqa: E402
# ----------------------------------------------------------------------------------------------------------------------
PORT = 8509
STATE = os.path.join(ROOT, 'runs', 'local')                 # app log + pid file (runs/ is not in git)
APP_LOG = os.path.join(STATE, 'dtc_app.log')
PID_FILE = os.path.join(STATE, 'dtc_app.pid')
PORT_FILE = os.path.join(STATE, 'dtc_app.port')     # the --ui_port it was started with: stop / status talk to THAT app,
                                                    # never to another one on 8509 (the user's own instance)
DEFAULT_ARGS = ['--source', 'campus']                       # as app_run_x86.bat / app_run_pi.sh: main.py's own default is an RTSP stream
# ----------------------------------------------------------------------------------------------------------------------
def venv_python():
    """The x86 venv's python.exe (see the module docstring), or None."""
    cands = [os.environ.get('DTC_PYTHON')]
    if os.environ.get('DTC_VENV'):
        cands.append(os.path.join(os.environ['DTC_VENV'], 'Scripts' if utils_platform.IS_WINDOWS else 'bin', 'python.exe' if utils_platform.IS_WINDOWS else 'python'))
    cands.append(os.path.join(ROOT, '.venv', 'Scripts', 'python.exe') if utils_platform.IS_WINDOWS else os.path.join(ROOT, '.venv', 'bin', 'python'))
    return next((c for c in cands if c and os.path.isfile(c)), None)
# ----------------------------------------------------------------------------------------------------------------------
def app_argv(args):
    """'--source x --flag y' -> argv for main.py with the launcher defaults (source campus if none given)."""
    argv = shlex.split(args.replace('\\', '/')) if isinstance(args, str) else list(args)
    if not any(a == '--source' or a.startswith('--source=') or a == '--list-sources' for a in argv):
        argv = DEFAULT_ARGS + argv
    return argv
# ----------------------------------------------------------------------------------------------------------------------
def _port_of(argv):
    for i, a in enumerate(argv):
        if a == '--ui_port' and i + 1 < len(argv):
            return int(argv[i + 1])
        if a.startswith('--ui_port='):
            return int(a.split('=', 1)[1])
    return PORT
# ----------------------------------------------------------------------------------------------------------------------
def _port_busy(port):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=0.3).close()
        return True
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
class Local(AppClient):
    """DTC on this laptop (x86)."""
    name = 'local'
    repo = ROOT.replace('\\', '/')
    host = 'localhost'
    bench_folder = 'bench_x86'                            # utils_bench.RUNS_DIR on x86: apart from the Pi runs pulled into runs/bench
    video_name = 'video.mp4' if utils_platform.IS_WINDOWS else 'video_large.avi'     # utils_video_saver renames it on Windows

    def __init__(self, verbose=True, port=None):
        self.python = venv_python()
        if self.python is None:
            raise RuntimeError('no x86 venv: run infrastructure\\build_me_x86.bat (or set DTC_PYTHON / DTC_VENV)')
        if port is None:                                  # the app the harness started, on the port it was started with
            port = PORT
            try:
                if self.app_pid():
                    port = int(open(PORT_FILE).read().split()[0])
            except (OSError, ValueError, IndexError):
                pass
        self.port = port
        self.base = f'http://127.0.0.1:{port}'
        os.makedirs(STATE, exist_ok=True)

    # ------------------------------------------------------------------------------------------------------ git / files
    def sync(self):
        return True                                       # the working tree itself runs here

    def commit(self):
        g = lambda *a, cwd=ROOT: subprocess.run(['git', '-C', cwd] + list(a), capture_output=True, text=True).stdout.strip()
        return [g('rev-parse', '--short', 'HEAD'), g('rev-parse', '--abbrev-ref', 'HEAD'), g('rev-parse', '--short', 'HEAD', cwd=os.path.join(ROOT, 'tools'))]

    def laptop_ip(self):
        return '127.0.0.1'                                # RTSP servers on this PC, as the local app sees them

    def ls(self, folder):
        return os.listdir(folder) if os.path.isdir(folder) else []

    def rm(self, path):
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else (os.path.isfile(path) and os.remove(path))

    def read_text(self, path):
        try:
            return open(path, encoding='utf-8', errors='replace').read()
        except OSError:
            return ''

    def save_log(self, dst):
        if os.path.isfile(APP_LOG):
            shutil.copyfile(APP_LOG, dst)

    def pull(self, remote, local, recursive=False):
        """Copy from the 'target' (this PC) into a harness run folder; 'dir/*' copies the folder's contents."""
        os.makedirs(local if recursive else os.path.dirname(local) or '.', exist_ok=True)
        src = remote[:-2] if remote.endswith('/*') else remote
        if not os.path.exists(src):
            return False
        if os.path.isdir(src):
            shutil.copytree(src, local, dirs_exist_ok=True)
        else:
            shutil.copy2(src, local)
        return True

    def push(self, local, remote):
        os.makedirs(os.path.dirname(remote), exist_ok=True)
        shutil.copy2(local, remote); return True

    def push_dir(self, local, remote):
        shutil.copytree(local, os.path.join(remote, os.path.basename(local.rstrip('/\\'))), dirs_exist_ok=True); return True

    # ------------------------------------------------------------------------------------------------------ python
    def _run(self, argv, cwd, timeout):
        try:
            r = subprocess.run([self.python] + argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                               env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, encoding='utf-8', errors='replace')
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            return None, (e.stdout or b'').decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or ''), 'timeout'

    def app_cli(self, args, timeout=90):
        rc, out, err = self._run(['main.py'] + app_argv(args), os.path.join(ROOT, 'src'), timeout)
        return rc, out + err

    def unit_tests(self, timeout=600):
        rc, out, err = self._run(['-m', 'unittest', 'discover', '-s', 'tests/unit', '-t', '.', '-v'], ROOT, timeout)
        return rc, '\n'.join(l for l in (out + err).splitlines() if 'SOT trk' not in l)

    def python_src(self, code, *args, timeout=120):
        return self._run(['-c', code] + list(args), os.path.join(ROOT, 'src'), timeout)

    # ------------------------------------------------------------------------------------------------------ app life
    def app_pid(self):
        try:
            pid = int(open(PID_FILE).read().split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return pid if utils_platform.pid_alive(pid) else None

    def start(self, args='', log=APP_LOG, wait=90, replace=True, idle_pause=False):
        """Start DTC here in the background; returns /status once the UI answers, else None (see logs())."""
        argv = app_argv(args)
        if not idle_pause and '--idle_pause_s' not in argv:
            argv += ['--idle_pause_s', '0']
        if self.app_pid():
            if not replace:
                raise RuntimeError('DTC is already running on this PC')
            self.stop()
        port = _port_of(argv)
        if _port_busy(port):
            raise RuntimeError(f'port {port} is taken (the Pi tunnel of "dtc pi open", the Fly2 UI, ...): add --ui_port 8510')
        self.port, self.base = port, f'http://127.0.0.1:{port}'
        for f in (PID_FILE,):
            if os.path.exists(f):
                os.remove(f)
        open(PORT_FILE, 'w').write(str(port))
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if utils_platform.IS_WINDOWS else 0
        subprocess.Popen([sys.executable, os.path.abspath(__file__), '--wrap', self.python, log, PID_FILE, '--'] + argv,
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=flags, start_new_session=not utils_platform.IS_WINDOWS)
        t0 = time.time()
        while time.time() - t0 < wait:
            st = self.status()
            if st is not None:
                return st
            if not self.app_pid() and time.time() - t0 > 5:
                return None
            time.sleep(1)
        return None

    def stop(self, timeout=40):
        """Graceful stop (/shutdown: video + report are written); kills after timeout. Returns the exit code or None."""
        pid = self.app_pid()
        if not pid:
            return self.exit_code()
        self.post('shutdown')
        t0 = time.time()
        while time.time() - t0 < timeout and self.app_pid():
            time.sleep(1)
        if self.app_pid():
            if utils_platform.IS_WINDOWS:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
            else:
                os.kill(pid, 9)
            return None
        time.sleep(1.0)                                   # the wrapper appends the exit code
        return self.exit_code()

    def exit_code(self, log=APP_LOG):
        m = re.findall(r'\[ EXIT  \] (-?\d+)', self.read_text(log))
        return int(m[-1]) if m else None

    def logs(self, n=40, log=APP_LOG, raw=False):
        """Last n lines of the app log without tqdm progress noise (as Pi.logs)."""
        txt = self.read_text(log)
        if not raw:
            txt = re.sub(r'(\[ *[A-Za-z]{2,7} *[A-Za-z]* *\])', r'\n\1', txt.replace('\r', '\n'))
            txt = '\n'.join(l for l in txt.splitlines() if l.strip() and not l.startswith('|') and 'it/s]' not in l)
        return '\n'.join(txt.splitlines()[-n:])
# ----------------------------------------------------------------------------------------------------------------------
def _wrap(python, log, pid_file, argv):
    """Detached: run main.py, record its pid, append '[ EXIT  ] rc' to the log when it ends."""
    with open(log, 'w', encoding='utf-8', errors='replace') as f:
        p = subprocess.Popen([python, '-u', 'main.py'] + argv, cwd=os.path.join(ROOT, 'src'), stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                             env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'})
        open(pid_file, 'w').write(str(p.pid))
        rc = p.wait()
    with open(log, 'a', encoding='utf-8') as f:
        f.write(f'\n[ EXIT  ] {rc}\n')
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__' and len(sys.argv) > 5 and sys.argv[1] == '--wrap':
    _wrap(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[sys.argv.index('--') + 1:])
