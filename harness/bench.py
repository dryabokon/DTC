"""dtc bench: the click-to-track benchmark (src/utils_bench.py, src/bench_worker.py) on the Pi from the laptop.

    dtc bench run [--preset quick|standard|full] [--models NANO,VIT] [--seqs campus,drone_ir] [--objects 3 (0 = all)]
                  [--min-size 8]
        starts it on the Pi (the same worker as the web UI's Benchmark tab), shows progress, copies the run to runs/bench/NNNN
        Ctrl+C cancels it on the Pi (partial results are kept)
    dtc bench list                  the Pi's benchmark runs
    dtc bench pull [NNNN|all]       copy run(s) from the Pi to runs/bench/
    dtc bench cancel                cancel the running benchmark
    --local (run / list / cancel)   the same on this x86 PC (x86 venv); its runs are written straight into runs/bench_x86/ here
"""
import json
import os
import time

from harness import pi_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, 'runs', 'bench')
# ----------------------------------------------------------------------------------------------------------------------
def _target(a):
    if getattr(a, 'local', False):
        from harness import local_app
        return local_app.Local(verbose=False)
    return pi_app.Pi(verbose=False)
# ----------------------------------------------------------------------------------------------------------------------
def _remote(pi):
    return f'{pi.repo}/runs/{pi.bench_folder}'
# ----------------------------------------------------------------------------------------------------------------------
def _py(pi, code, *args, timeout=120):
    rc, out, err = pi.python_src(code, *args, timeout=timeout)
    return rc, (out or '').strip(), err or ''
# ----------------------------------------------------------------------------------------------------------------------
def _fmt(s):
    s = int(s or 0)
    return f'{s // 60} min {s % 60:02d} s' if s >= 60 else f'{s} s'
# ----------------------------------------------------------------------------------------------------------------------
def run(a):
    cfg = {k: v for k, v in {'preset': a.preset, 'models': a.models, 'sequences': a.seqs, 'tracks': a.objects,
                             'min_size': a.min_size}.items() if v is not None}
    pi = _target(a)
    where = 'on the Pi' if pi.name == 'pi' else 'on this PC'
    rc, out, err = _py(pi, 'import sys, json, utils_bench as B\n'
                           'c, p = B.readiness(json.loads(sys.argv[1]))\n'
                           'print(json.dumps({"checks": c}))\n'
                           'if not any(x["status"] == "fail" for x in c): print("ID", B.start(json.loads(sys.argv[1])))', json.dumps(cfg))
    for line in out.splitlines():
        if line.startswith('{'):
            for c in json.loads(line)['checks']:
                print(f'   [{c["status"]:4s}] {c["key"]}: {c["detail"]}')
    rid = next((l.split()[1] for l in out.splitlines() if l.startswith('ID ')), None)
    if rid is None:
        print('not started' + (f': {err.strip()[-300:]}' if err.strip() else '')); return 1
    print(f'benchmark {rid} started {where} (web UI: Benchmark tab)')
    try:
        while True:
            time.sleep(3)
            txt = pi.read_text(f'{_remote(pi)}/{rid}/progress.json')
            try:
                p = json.loads(txt)
            except ValueError:
                continue
            cur = p.get('current') or {}
            print(f'\r   {100 * p.get("done_frac", 0):5.1f}%  ~{_fmt(p.get("left_s"))} left  task {cur.get("task", p.get("tasks_done"))}/{p.get("tasks_total")}  '
                  f'{cur.get("label", p.get("stage", ""))[:60]:60s}', end='', flush=True)
            if p.get('state') != 'RUNNING':
                print(f'\n{p["state"]} in {_fmt(p.get("elapsed_s"))}')
                break
    except KeyboardInterrupt:
        print(f'\ncancelling {where} ...')
        _py(pi, 'import utils_bench as B; print(B.cancel())')
        for _ in range(30):
            time.sleep(1)
            txt = pi.read_text(f'{_remote(pi)}/{rid}/progress.json')
            if '"RUNNING"' not in txt:
                break
    return pull_one(pi, rid, show=True)
# ----------------------------------------------------------------------------------------------------------------------
def pull_one(pi, rid, show=False):
    dst = os.path.join(ROOT, 'runs', pi.bench_folder, rid)      # runs/bench (from the Pi) or runs/bench_x86 (written here already)
    if pi.name == 'local':
        ok = os.path.isdir(dst)
    else:
        os.makedirs(dst, exist_ok=True)
        ok = pi.pull(f'{_remote(pi)}/{rid}/*', dst, recursive=True)
    rep = os.path.join(dst, 'report.md')
    if show and os.path.isfile(rep):
        txt = open(rep, encoding='utf-8').read()
        i = txt.find('| model |')
        print(txt[i:] if i >= 0 else txt[:2000])
    print(f'-> {dst}' if ok else f'copy failed: {rid}')
    return 0 if ok else 1
# ----------------------------------------------------------------------------------------------------------------------
def main(a):
    if a.action == 'run':
        return run(a)
    if a.action == 'pull' and getattr(a, 'local', False):
        print(f'nothing to pull: local runs are in {LOCAL}_x86'); return 0
    pi = _target(a)
    if a.action == 'list':
        rc, out, err = _py(pi, 'import json, utils_bench as B; print(json.dumps(B.runs_summary()))')
        try:
            runs = json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            print(err or out); return 1
        for r in runs:
            head = '  '.join(f'{m} F1 {v.get("f1")} {v.get("fps")} fps' for m, v in (r.get('headline') or {}).items())
            print(f'{r["id"]}  {r.get("created") or "":19s}  {r.get("state") or "?":11s}  {r.get("tasks")} tasks  {head}')
        return 0
    if a.action == 'cancel':
        rc, out, _ = _py(pi, 'import utils_bench as B; print(B.cancel())')
        print('cancelled' if out.endswith('True') else 'nothing running'); return 0
    if a.action == 'pull':
        if a.id in (None, 'all'):
            ids = [x for x in pi.ls(_remote(pi)) if x.isdigit()]
        else:
            ids = [a.id.zfill(4)]
        return max([pull_one(pi, r) for r in ids] or [0])
    return 1
# ----------------------------------------------------------------------------------------------------------------------
def add_parser(sub):
    s = sub.add_parser('bench', help='click-to-track benchmark on the Pi')
    s.add_argument('action', choices=['run', 'list', 'pull', 'cancel']); s.add_argument('id', nargs='?')
    s.add_argument('--preset', default='quick'); s.add_argument('--models'); s.add_argument('--seqs')
    s.add_argument('--objects', type=int, help='objects per video, smallest first (0 = all)'); s.add_argument('--min-size', type=int)
    s.add_argument('--local', action='store_true', help='on this x86 PC instead of the Pi')
    return s
