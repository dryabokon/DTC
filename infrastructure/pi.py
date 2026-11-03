"""Find the Raspberry Pi on whatever network it is on, and talk to it.

    python infrastructure/pi.py find            # print the address that works (and how it was found)
    python infrastructure/pi.py run "<cmd>"     # run a command on the Pi over SSH
    python infrastructure/pi.py push <local> <remote>   /  pull <remote> <local>
    python infrastructure/pi.py url             # web UI address, e.g. http://169.254.93.37:8509/
    python infrastructure/pi.py sync            # put the Pi's DTC checkout and its tools/ submodule on this laptop's branch/commit

Settings come from .env in the repo root: PI_USER, PI_PASSWORD, PI_HOST (default pi.local),
PI_HOSTKEY (SSH host key fingerprint, SHA256:...), PI_MACS (comma separated, eth0 and wlan0).

Search order, first hit wins:
  1. last address that worked (.pi_last_host)
  2. PI_HOST through the system resolver (mDNS: works at home, over a direct cable, on a phone hotspot)
  3. ARP table lookup by PI_MACS
  4. scan of every local /24 for an open port 22
Every candidate must present PI_HOSTKEY, so a different device on a foreign network is never used.
Transport: PuTTY's plink/pscp from Windows; when Windows cannot reach the Pi but WSL can (a full-tunnel VPN such as
WireGuard blocks all non-tunnel traffic of Windows, not of WSL), ssh/scp/sshpass inside WSL - same host key check.
"""
import concurrent.futures
import ipaddress
import os
import re
import base64
import hashlib
import shutil
import socket
import subprocess
import sys
# ----------------------------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, '.pi_last_host')
UI_PORT = 8509
# ----------------------------------------------------------------------------------------------------------------------
def load_env(path=os.path.join(ROOT, '.env')):
    env = {}
    if os.path.isfile(path):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ('PI_USER', 'PI_PASSWORD', 'PI_HOST', 'PI_HOSTKEY', 'PI_MACS'):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.setdefault('PI_USER', 'pi')
    env.setdefault('PI_HOST', 'pi.local')
    return env
# ----------------------------------------------------------------------------------------------------------------------
ENV = load_env()
# ----------------------------------------------------------------------------------------------------------------------
def log(*a):
    print(*a, file=sys.stderr)
# ----------------------------------------------------------------------------------------------------------------------
def port_open(host, port=22, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
WSL = {'on': False}                 # True once find() reached the Pi only through WSL
WSL_KNOWN_HOSTS = '/tmp/dtc_pi_known_hosts'
# ----------------------------------------------------------------------------------------------------------------------
def wsl_available():
    if not shutil.which('wsl'):
        return False
    r = subprocess.run(['wsl', '-e', 'sh', '-c', 'command -v ssh scp sshpass nc >/dev/null && echo ok'], capture_output=True, text=True)
    return 'ok' in r.stdout
# ----------------------------------------------------------------------------------------------------------------------
def wsl_port_open(host, port=22):
    return subprocess.run(['wsl', '-e', 'nc', '-z', '-w', '2', host, str(port)], capture_output=True).returncode == 0
# ----------------------------------------------------------------------------------------------------------------------
def wsl_trust(host):
    """Scan the host key from WSL, accept it only if its fingerprint is PI_HOSTKEY, store it for ssh/scp in WSL."""
    r = subprocess.run(['wsl', '-e', 'sh', '-c', f'ssh-keyscan -T 5 -t ed25519 {host} 2>/dev/null'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == 'ssh-ed25519':
            fp = 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(parts[2])).digest()).decode().rstrip('=')
            if fp == ENV.get('PI_HOSTKEY'):
                subprocess.run(['wsl', '-e', 'sh', '-c', f'cat > {WSL_KNOWN_HOSTS}'], input=line + chr(10), text=True)
                return True
            log(f'[pi] {host}: host key {fp} is not PI_HOSTKEY - refusing')
    return False
# ----------------------------------------------------------------------------------------------------------------------
def ssh_env():
    """Environment for subprocesses: the WSL transport gets the password through SSHPASS (not the command line)."""
    if not WSL['on']:
        return None
    wslenv = os.environ.get('WSLENV', '')
    return dict(os.environ, SSHPASS=ENV.get('PI_PASSWORD', ''), WSLENV=(wslenv + ':' if wslenv else '') + 'SSHPASS/u')
# ----------------------------------------------------------------------------------------------------------------------
WSL_SSH_OPTS = ['-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={WSL_KNOWN_HOSTS}', '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR']
def plink_args(host):
    """argv prefix for a command on the Pi (plink on Windows, ssh in WSL behind a VPN)."""
    if WSL['on']:
        return ['wsl', '-e', 'sshpass', '-e', 'ssh'] + WSL_SSH_OPTS + [f"{ENV['PI_USER']}@{host}"]
    args = ['plink', '-batch', '-pw', ENV.get('PI_PASSWORD', '')]
    if ENV.get('PI_HOSTKEY'):
        args += ['-hostkey', ENV['PI_HOSTKEY']]
    return args + [f"{ENV['PI_USER']}@{host}"]
# ----------------------------------------------------------------------------------------------------------------------
def _wsl_path(p):
    return subprocess.run(['wsl', '-e', 'wslpath', '-a', os.path.abspath(p).replace('\\', '/')], capture_output=True, text=True).stdout.strip()
# ----------------------------------------------------------------------------------------------------------------------
def copy(host, src, dst, to_pi=True, recursive=False, quiet=True):
    """Copy a local path to the Pi (to_pi) or from it. Remote paths are plain paths on the Pi."""
    remote = lambda p: f"{ENV['PI_USER']}@{host}:{p}"
    if WSL['on']:
        argv = ['wsl', '-e', 'sshpass', '-e', 'scp'] + WSL_SSH_OPTS + (['-q'] if quiet else []) + (['-r'] if recursive else [])
        argv += [_wsl_path(src), remote(dst)] if to_pi else [remote(src), _wsl_path(dst)]
    else:
        argv = ['pscp', '-batch', '-pw', ENV.get('PI_PASSWORD', ''), '-hostkey', ENV['PI_HOSTKEY']] + (['-q'] if quiet else []) + (['-r'] if recursive else [])
        argv += [src, remote(dst)] if to_pi else [remote(src), dst]
    return subprocess.run(argv, capture_output=quiet, env=ssh_env()).returncode == 0
# ----------------------------------------------------------------------------------------------------------------------
def is_our_pi(host, timeout=15):
    """True only if SSH works and the host presents PI_HOSTKEY (directly, or through WSL when Windows is blocked)."""
    if port_open(host):
        WSL['on'] = False
    elif wsl_available() and wsl_port_open(host) and wsl_trust(host):
        WSL['on'] = True
    else:
        return False
    try:
        r = subprocess.run(plink_args(host) + ['echo PI_OK'], capture_output=True, text=True, timeout=timeout, env=ssh_env())
        return 'PI_OK' in r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
# ----------------------------------------------------------------------------------------------------------------------
def from_cache():
    if os.path.isfile(CACHE):
        h = open(CACHE).read().strip()
        return [h] if h else []
    return []
# ----------------------------------------------------------------------------------------------------------------------
def from_resolver(name):
    try:
        infos = socket.getaddrinfo(name, 22, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
    return list(dict.fromkeys(v4))         # IPv6 link-local needs a zone id (%N) that browsers do not accept: IPv4 only
# ----------------------------------------------------------------------------------------------------------------------
def from_arp():
    macs = [m.strip().lower().replace(':', '-') for m in ENV.get('PI_MACS', '').split(',') if m.strip()]
    if not macs:
        return []
    try:
        out = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=10).stdout.lower()
    except Exception:
        return []
    hits = []
    for line in out.splitlines():
        m = re.match(r'\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f:-]{17})', line)
        if m and m.group(2).replace(':', '-') in macs:
            hits.append(m.group(1))
    return list(dict.fromkeys(hits))
# ----------------------------------------------------------------------------------------------------------------------
def local_networks():
    """/24 networks of this machine's IPv4 addresses (link-local 169.254/16 is left to mDNS/ARP: too big to scan)."""
    addrs = set()
    try:
        for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(i[4][0])
    except socket.gaierror:
        pass
    try:                                    # the address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); addrs.add(s.getsockname()[0]); s.close()
    except OSError:
        pass
    nets = []
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_loopback or ip.is_link_local:
            continue
        net = ipaddress.ip_network(f'{a}/24', strict=False)
        if net not in nets:
            nets.append(net)
    return nets
# ----------------------------------------------------------------------------------------------------------------------
def from_scan():
    hosts = [str(h) for net in local_networks() for h in net.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        found = [h for h, ok in zip(hosts, ex.map(lambda h: port_open(h, 22, 0.4), hosts)) if ok]
    return found
# ----------------------------------------------------------------------------------------------------------------------
def find(verbose=True):
    if not ENV.get('PI_HOSTKEY'):
        # without the expected host key, every SSH server found by ARP/scan would be sent the Pi password
        log('[pi] PI_HOSTKEY is not set in .env - refusing to search (get it once with: plink pi@<known address>)')
        return None
    steps = [('cache', from_cache), (f"resolver ({ENV['PI_HOST']})", lambda: from_resolver(ENV['PI_HOST'])),
             ('arp', from_arp), ('scan of local /24', from_scan)]
    tried = set()
    for how, fn in steps:
        for host in fn():
            if host in tried:
                continue
            tried.add(host)
            if is_our_pi(host):
                open(CACHE, 'w').write(host)
                if verbose: log(f'[pi] {host}  (found by {how}' + (', through WSL: Windows is blocked, e.g. by a VPN)' if WSL['on'] else ')'))
                return host
    if os.path.isfile(CACHE):
        os.remove(CACHE)
    log('[pi] not found. Tried: ' + (', '.join(sorted(tried)) or 'nothing'))
    log('[pi] is it powered, on the same Wi-Fi / cable as this laptop? PI_HOST=' + ENV['PI_HOST'])
    return None
# ----------------------------------------------------------------------------------------------------------------------
PI_REPO = '~/sources/DTC'
# ----------------------------------------------------------------------------------------------------------------------
def redact(text):
    """Never let the Pi password reach logs or reports (plink gets it on its command line)."""
    pw = ENV.get('PI_PASSWORD')
    return str(text).replace(pw, '***') if pw else str(text)
# ----------------------------------------------------------------------------------------------------------------------
def pi_run(host, cmd, timeout=120):
    try:
        r = subprocess.run(plink_args(host) + [cmd], capture_output=True, text=True, timeout=timeout, env=ssh_env())
    except subprocess.TimeoutExpired:
        raise TimeoutError(f'command on the Pi did not finish in {timeout} s: {redact(cmd)[:200]}') from None
    return r.returncode, r.stdout.strip(), r.stderr.strip()
# ----------------------------------------------------------------------------------------------------------------------
def git(*args):
    return subprocess.run(['git', '-C', ROOT] + list(args), capture_output=True, text=True).stdout.strip()
# ----------------------------------------------------------------------------------------------------------------------
def sync(host):
    """Pi checkout -> laptop's current branch and commit (committed; pushing is not required).
    Tries GitHub first (fetch on the Pi); if the Pi has no internet (e.g. direct cable) or the commit is not pushed,
    ships the missing commits from this laptop as a git bundle over SSH. Never touches uncommitted changes on the Pi."""
    branch, head = git('rev-parse', '--abbrev-ref', 'HEAD'), git('rev-parse', 'HEAD')
    if git('status', '--porcelain', '--untracked-files=no'):
        log('[pi] note: uncommitted local changes are NOT synced, only commits')
    _, dirty, _ = pi_run(host, f'cd {PI_REPO} && git status --porcelain --untracked-files=no')
    if dirty:
        log('[pi] the Pi has uncommitted changes, not touching it: ' + dirty.replace('\n', '; ')); return 1

    def checkout(ref):      # switch the Pi to `branch` and fast-forward it to `ref`; returns the Pi's HEAD
        _, out, _ = pi_run(host, f'cd {PI_REPO} && (git checkout -q {branch} 2>/dev/null || git checkout -q -b {branch} {ref}) '
                                 f'&& git merge -q --ff-only {ref}; git rev-parse HEAD')
        return out.splitlines()[-1] if out else ''

    rc, _, _ = pi_run(host, f'cd {PI_REPO} && timeout 20 git fetch -q origin {branch}')
    if rc == 0 and checkout(f'origin/{branch}') == head:
        log(f'[pi] synced from GitHub: {branch} @ {head[:7]}'); return sync_tools(host)

    log('[pi] GitHub not reachable from the Pi (or commit not pushed) - shipping commits from this laptop')
    _, pi_head, _ = pi_run(host, f'cd {PI_REPO} && git rev-parse HEAD')
    if pi_head == head and checkout(head) == head:      # same commit: still put the Pi on `branch` (e.g. after a fast-forward merge)
        log(f'[pi] already at {head[:7]}, on {branch}'); return sync_tools(host)
    known = subprocess.run(['git', '-C', ROOT, 'merge-base', '--is-ancestor', pi_head, head], capture_output=True).returncode == 0
    bundle = os.path.join(ROOT, '.pi_sync.bundle')
    r = subprocess.run(['git', '-C', ROOT, 'bundle', 'create', bundle, f'{pi_head}..{branch}' if known else branch], capture_output=True, text=True)
    if r.returncode != 0:
        log('[pi] git bundle failed: ' + r.stderr); return 1
    if not copy(host, bundle, '/tmp/dtc_sync.bundle'):
        log('[pi] copying the bundle failed'); return 1
    os.remove(bundle)
    rc, _, err = pi_run(host, f'cd {PI_REPO} && git fetch -q /tmp/dtc_sync.bundle {branch}:refs/remotes/laptop/{branch} && rm -f /tmp/dtc_sync.bundle')
    if rc == 0 and checkout(f'laptop/{branch}') == head:
        log(f'[pi] synced from laptop: {branch} @ {head[:7]}'); return sync_tools(host)
    log(f'[pi] sync failed {err}'); return 1
# ----------------------------------------------------------------------------------------------------------------------
def sync_tools(host):
    """tools/ submodule on the Pi -> the commit pinned in the laptop's HEAD.
    The Pi gets the commit from GitHub if it can, else as a git bundle from the laptop's tools/ checkout."""
    pinned = git('rev-parse', 'HEAD:tools')
    if not pinned:
        return 0                                        # a commit without the submodule
    sub = f'{PI_REPO}/tools'
    def pi_has_commit():
        return pi_run(host, f'cd {sub} 2>/dev/null && git cat-file -e {pinned}^{{commit}}')[0] == 0
    if not pi_has_commit():
        pi_run(host, f'cd {sub} 2>/dev/null && timeout 20 git fetch -q origin')
    if not pi_has_commit():
        tools = os.path.join(ROOT, 'tools')
        if subprocess.run(['git', '-C', tools, 'cat-file', '-e', pinned + '^{commit}'], capture_output=True).returncode != 0:
            log(f'[pi] tools {pinned[:7]} is not in the laptop checkout either: git submodule update --init'); return 1
        rc, pi_tools, _ = pi_run(host, f'cd {sub} 2>/dev/null && git rev-parse HEAD')
        known = rc == 0 and subprocess.run(['git', '-C', tools, 'merge-base', '--is-ancestor', pi_tools, pinned], capture_output=True).returncode == 0
        bundle = os.path.join(ROOT, '.pi_sync_tools.bundle')
        subprocess.run(['git', '-C', tools, 'branch', '-f', 'pi_sync', pinned], capture_output=True)
        r = subprocess.run(['git', '-C', tools, 'bundle', 'create', bundle, f'{pi_tools}..pi_sync' if known else 'pi_sync'], capture_output=True, text=True)
        subprocess.run(['git', '-C', tools, 'branch', '-D', 'pi_sync'], capture_output=True)
        if r.returncode != 0:
            log('[pi] tools bundle failed: ' + r.stderr); return 1
        if not copy(host, bundle, '/tmp/dtc_tools.bundle'):
            log('[pi] copying the tools bundle failed'); return 1
        os.remove(bundle)
        url = git('config', '-f', '.gitmodules', 'submodule.tools.url')
        get = (f'cd {sub} && git fetch -q /tmp/dtc_tools.bundle pi_sync:refs/remotes/laptop/pi_sync' if rc == 0 else    # existing clone
               f'git clone -q /tmp/dtc_tools.bundle {sub} && git -C {sub} remote set-url origin {url}')               # fresh Pi
        rc, _, err = pi_run(host, f'{get} && rm -f /tmp/dtc_tools.bundle')
        if rc != 0:
            log('[pi] tools bundle fetch failed: ' + err); return 1
        log(f'[pi] tools {pinned[:7]} shipped from laptop')
    rc, out, err = pi_run(host, f'cd {PI_REPO} && git submodule sync -q && git submodule update --init -q tools && git -C tools rev-parse HEAD')
    if rc == 0 and out and out.splitlines()[-1] == pinned:
        log(f'[pi] tools @ {pinned[:7]}'); return 0
    log(f'[pi] tools sync failed: {err or out}'); return 1
# ----------------------------------------------------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('find', 'run', 'push', 'pull', 'url', 'sync'):
        print(__doc__); return 2
    cmd = sys.argv[1]
    host = find(verbose=(cmd == 'find' or cmd == 'url'))
    if host is None:
        return 1
    if cmd == 'find':
        print(host); return 0
    if cmd == 'url':
        print(f'http://{host}:{UI_PORT}/'); return 0
    if cmd == 'run':
        return subprocess.run(plink_args(host) + [' '.join(sys.argv[2:])], env=ssh_env()).returncode
    if cmd == 'sync':
        return sync(host)
    if cmd == 'push':
        return 0 if copy(host, sys.argv[2], sys.argv[3], quiet=False) else 1
    return 0 if copy(host, sys.argv[2], sys.argv[3], to_pi=False, quiet=False) else 1
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    sys.exit(main())
