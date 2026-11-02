"""Find the Raspberry Pi on whatever network it is on, and talk to it.

    python infrastructure/pi.py find            # print the address that works (and how it was found)
    python infrastructure/pi.py run "<cmd>"     # run a command on the Pi over SSH
    python infrastructure/pi.py push <local> <remote>   /  pull <remote> <local>
    python infrastructure/pi.py url             # web UI address, e.g. http://169.254.93.37:8509/

Settings come from .env in the repo root: PI_USER, PI_PASSWORD, PI_HOST (default pi.local),
PI_HOSTKEY (SSH host key fingerprint, SHA256:...), PI_MACS (comma separated, eth0 and wlan0).

Search order, first hit wins:
  1. last address that worked (.pi_last_host)
  2. PI_HOST through the system resolver (mDNS: works at home, over a direct cable, on a phone hotspot)
  3. ARP table lookup by PI_MACS
  4. scan of every local /24 for an open port 22
Every candidate must present PI_HOSTKEY, so a different device on a foreign network is never used.
Needs PuTTY's plink/pscp on PATH (Windows); on Linux/macOS the same flow works with ssh/sshpass replaced.
"""
import concurrent.futures
import ipaddress
import os
import re
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
def plink_args(host):
    args = ['plink', '-batch', '-pw', ENV.get('PI_PASSWORD', '')]
    if ENV.get('PI_HOSTKEY'):
        args += ['-hostkey', ENV['PI_HOSTKEY']]
    return args + [f"{ENV['PI_USER']}@{host}"]
# ----------------------------------------------------------------------------------------------------------------------
def is_our_pi(host, timeout=15):
    """True only if SSH works and the host presents PI_HOSTKEY."""
    if not port_open(host):
        return False
    try:
        r = subprocess.run(plink_args(host) + ['echo PI_OK'], capture_output=True, text=True, timeout=timeout)
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
                if verbose: log(f'[pi] {host}  (found by {how})')
                return host
    if os.path.isfile(CACHE):
        os.remove(CACHE)
    log('[pi] not found. Tried: ' + (', '.join(sorted(tried)) or 'nothing'))
    log('[pi] is it powered, on the same Wi-Fi / cable as this laptop? PI_HOST=' + ENV['PI_HOST'])
    return None
# ----------------------------------------------------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('find', 'run', 'push', 'pull', 'url'):
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
        return subprocess.run(plink_args(host) + [' '.join(sys.argv[2:])]).returncode
    pscp = ['pscp', '-batch', '-pw', ENV.get('PI_PASSWORD', '')] + (['-hostkey', ENV['PI_HOSTKEY']] if ENV.get('PI_HOSTKEY') else [])
    remote = lambda p: f"{ENV['PI_USER']}@{host}:{p}"
    if cmd == 'push':
        return subprocess.run(pscp + [sys.argv[2], remote(sys.argv[3])]).returncode
    return subprocess.run(pscp + [remote(sys.argv[2]), sys.argv[3]]).returncode
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    sys.exit(main())
