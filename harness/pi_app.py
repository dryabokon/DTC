"""Control the DTC app on the Raspberry Pi: start / stop / status / logs / HTTP calls / pull artifacts.
Built on infrastructure/pi.py (finding the Pi, SSH, sync)."""
import atexit
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
# ----------------------------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'infrastructure'))
import pi as pi_ssh                                          # infrastructure/pi.py
# ----------------------------------------------------------------------------------------------------------------------
PORT = 8509
HARNESS_PORT = 18509                      # local end of the harness tunnel when the Pi is only reachable through WSL
APP_LOG = '/tmp/dtc_app.log'
REPO = '/home/pi/sources/DTC'
# ----------------------------------------------------------------------------------------------------------------------
class Pi:
    def __init__(self, verbose=True):
        self.host = pi_ssh.find(verbose=verbose)
        if self.host is None:
            raise RuntimeError('Raspberry Pi not found (see infrastructure/pi.py find)')
        self.base = f'http://{self.host}:{PORT}'
        self._tunnel = None
        if pi_ssh.WSL['on']:                 # Windows is blocked (VPN): HTTP to the app goes through an SSH tunnel in WSL
            self._tunnel = subprocess.Popen(['wsl', '-e', 'sshpass', '-e', 'ssh'] + pi_ssh.WSL_SSH_OPTS +
                                            ['-o', 'ExitOnForwardFailure=yes', '-N', '-L', f'{HARNESS_PORT}:127.0.0.1:{PORT}', f"{pi_ssh.ENV['PI_USER']}@{self.host}"],
                                            env=pi_ssh.ssh_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            atexit.register(self._tunnel.terminate)
            self.base = f'http://127.0.0.1:{HARNESS_PORT}'
            for _ in range(40):
                try:
                    socket.create_connection(('127.0.0.1', HARNESS_PORT), timeout=0.5).close(); break
                except OSError:
                    time.sleep(0.25)

    def browser_tunnel(self):
        """Behind a VPN: a lasting tunnel for the browser, http://localhost:8509 -> the Pi's web UI. Returns the URL."""
        if not pi_ssh.WSL['on']:
            return self.url()
        try:
            socket.create_connection(('127.0.0.1', PORT), timeout=0.5).close()
            return f'http://localhost:{PORT}/'                # already there
        except OSError:
            pass
        subprocess.run(['wsl', '-e', 'sshpass', '-e', 'ssh'] + pi_ssh.WSL_SSH_OPTS + ['-f', '-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
                        '-L', f'{PORT}:127.0.0.1:{PORT}', f"{pi_ssh.ENV['PI_USER']}@{self.host}"], env=pi_ssh.ssh_env(), timeout=30, stdin=subprocess.DEVNULL)
        return f'http://localhost:{PORT}/'

    # ---------------------------------------------------------------------------------------------------------- ssh
    def sh(self, cmd, timeout=120):
        """Run a shell command on the Pi -> (rc, stdout, stderr)."""
        return pi_ssh.pi_run(self.host, cmd, timeout=timeout)

    def sync(self):
        return pi_ssh.sync(self.host) == 0

    def commit(self):
        return self.sh(f'cd {REPO} && git rev-parse --short HEAD && git rev-parse --abbrev-ref HEAD && git -C tools rev-parse --short HEAD')[1].split()

    def pull(self, remote, local, recursive=False):
        os.makedirs(local if recursive else os.path.dirname(local) or '.', exist_ok=True)
        if recursive and remote.endswith('/*'):            # scp in WSL does not expand a remote glob into a local dir: copy the dir itself
            ok = pi_ssh.copy(self.host, remote[:-2], local, to_pi=False, recursive=True) if pi_ssh.WSL['on'] else None
            if ok is not None:
                inner = os.path.join(local, os.path.basename(remote[:-2].rstrip('/')))
                if os.path.isdir(inner):
                    for f in os.listdir(inner):
                        os.replace(os.path.join(inner, f), os.path.join(local, f))
                    os.rmdir(inner)
                return ok
        return pi_ssh.copy(self.host, remote, local, to_pi=False, recursive=recursive)

    def push_dir(self, local, remote):
        return pi_ssh.copy(self.host, local, remote, recursive=True)

    def push(self, local, remote):
        return pi_ssh.copy(self.host, local, remote)

    def laptop_ip(self):
        """This laptop's address as the Pi sees it (the interface that routes to the Pi)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((self.host, 9)); ip = s.getsockname()[0]; s.close()
        return ip

    # --------------------------------------------------------------------------------------------------------- http
    def url(self, path=''):
        return f'{self.base}/{path.lstrip("/")}'

    def get(self, path, timeout=5):
        try:
            with urllib.request.urlopen(self.url(path), timeout=timeout) as r:
                body = r.read()
                return r.status, (json.loads(body) if body[:1] in (b'{', b'[') else body.decode(errors='replace'))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors='replace')
        except Exception as e:
            return None, str(e)

    def post(self, path, form=None, js=None, timeout=5):
        if js is not None:
            data, ctype = json.dumps(js).encode(), 'application/json'
        else:
            data, ctype = urllib.parse.urlencode(form or {}).encode(), 'application/x-www-form-urlencoded'
        req = urllib.request.Request(self.url(path), data=data, headers={'Content-Type': ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return r.status, (json.loads(body) if body[:1] in (b'{', b'[') else body.decode(errors='replace'))
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, (json.loads(body) if body[:1] == b'{' else body.decode(errors='replace'))
        except Exception as e:
            return None, str(e)

    def status(self):
        code, st = self.get('status', timeout=3)
        return st if code == 200 and isinstance(st, dict) else None

    def key(self, k):
        return self.post('key_press', {'key': k})[0] == 200

    def click(self, x, y, box=None):
        form = {'x': int(x), 'y': int(y)}
        if box is not None:
            form.update(x1=int(box[0]), y1=int(box[1]), x2=int(box[2]), y2=int(box[3]))
        return self.post('mouse_click', form)[0] == 200

    # ------------------------------------------------------------------------------------------------------ app life
    def app_pid(self):
        rc, out, _ = self.sh("pgrep -f '[p]ython main.py' | head -1")
        return int(out) if out.strip().isdigit() else None

    def start(self, args='', log=APP_LOG, wait=90, replace=True):
        """Start DTC (run_me_arm.sh <args>) in the background; returns /status once the UI answers, else None."""
        if self.app_pid():
            if not replace:
                raise RuntimeError('DTC is already running on the Pi')
            self.stop()
        # fully detached (own session, no stdin/stdout of the SSH channel), else the SSH call waits for the app to end
        inner = f'cd {REPO} && PYTHONUNBUFFERED=1 ./run_me_arm.sh {args} > {log} 2>&1; echo "[ EXIT  ] $?" >> {log}'
        self.sh("setsid nohup bash -c '" + inner.replace("'", "'\''") + "' < /dev/null > /dev/null 2>&1 &", timeout=30)
        t0 = time.time()
        while time.time() - t0 < wait:
            st = self.status()
            if st is not None:
                return st
            if not self.app_pid() and time.time() - t0 > 5:
                return None                                   # died during start-up: see logs()
            time.sleep(1)
        return None

    def stop(self, timeout=40):
        """Graceful stop (/shutdown: video + report are written); kills after timeout. Returns the exit code or None."""
        if not self.app_pid():
            return self.exit_code()
        self.post('shutdown')
        t0 = time.time()
        while time.time() - t0 < timeout and self.app_pid():
            time.sleep(1)
        if self.app_pid():
            self.sh("pkill -f '[p]ython main.py'; sleep 2; pkill -9 -f '[p]ython main.py'")
            return None
        time.sleep(0.5)
        return self.exit_code()

    def exit_code(self, log=APP_LOG):
        rc, out, _ = self.sh(f"grep -a '\\[ EXIT  \\]' {log} | tail -1")
        return int(out.split()[-1]) if out.strip() and out.split()[-1].lstrip('-').isdigit() else None

    def logs(self, n=40, log=APP_LOG, raw=False):
        """Last n lines of the app log without tqdm progress noise."""
        # tqdm redraws with \r and app prints land on the same line: split before every '[ TAG ]' first
        flt = '' if raw else (" | tr '\\r' '\\n' | sed -E 's/(\\[ *[A-Za-z]{2,7} *[A-Za-z]* *\\])/\\n\\1/g'"
                              " | grep -av '^|' | grep -av 'it/s\\]' | grep -av '^\\s*$'")
        return self.sh(f'cat {log} 2>/dev/null{flt} | tail -{n}')[1]

    def wait_for(self, cond, timeout=10, every=0.3):
        """Poll /status until cond(status) is true; returns the last status (or None)."""
        t0, st = time.time(), None
        while time.time() - t0 < timeout:
            st = self.status()
            if st is not None and cond(st):
                return st
            time.sleep(every)
        return st
