"""dtc bench: the click-to-track benchmark (src/utils_bench.py, src/bench_worker.py) on the Pi from the laptop.

    dtc bench run [--preset quick|standard|full] [--models NANO,VIT] [--seqs campus,drone_ir] [--tracks 3] [--frames 150]
                  [--order smallest|largest|longest|all] [--starts first|first+middle] [--loss-rules]
        starts it on the Pi (the same worker as the web UI's Benchmark tab), shows progress, copies the run to runs/bench/NNNN
        Ctrl+C cancels it on the Pi (partial results are kept)
    dtc bench list                  the Pi's benchmark runs
    dtc bench pull [NNNN|all]       copy run(s) from the Pi to runs/bench/
    dtc bench cancel                cancel the running benchmark
"""
import json
import os
import shlex
import time

from harness import pi_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, 'runs', 'bench')
REMOTE = pi_app.REPO + '/runs/bench'
VENV = 'source ~/venv/p311_dtc/bin/activate'
# ----------------------------------------------------------------------------------------------------------------------
def _py(pi, code, *args, timeout=120):
    cmd = f'cd {pi_app.REPO}/src && {VENV} && python -c {shlex.quote(code)} ' + ' '.join(shlex.quote(a) for a in args)
    rc, out, err = pi.sh(cmd, timeout=timeout)
    return rc, out.strip(), err
# ----------------------------------------------------------------------------------------------------------------------
def _fmt(s):
    s = int(s or 0)
    return f'{s // 60} min {s % 60:02d} s' if s >= 60 else f'{s} s'
# ----------------------------------------------------------------------------------------------------------------------
def run(a):
    cfg = {k: v for k, v in {'preset': a.preset, 'models': a.models, 'sequences': a.seqs, 'tracks': a.tracks, 'max_frames': a.frames,
                             'order': a.order, 'starts': a.starts, 'loss_rules': True if a.loss_rules else None}.items() if v is not None}
    pi = pi_app.Pi(verbose=False)
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
    print(f'benchmark {rid} started on the Pi (web UI: Benchmark tab)')
    try:
        while True:
            time.sleep(3)
            _, txt, _ = pi.sh(f'cat {REMOTE}/{rid}/progress.json 2>/dev/null')
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
        print('\ncancelling on the Pi ...')
        _py(pi, 'import utils_bench as B; print(B.cancel())')
        for _ in range(30):
            time.sleep(1)
            _, txt, _ = pi.sh(f'cat {REMOTE}/{rid}/progress.json 2>/dev/null')
            if '"RUNNING"' not in txt:
                break
    return pull_one(pi, rid, show=True)
# ----------------------------------------------------------------------------------------------------------------------
def pull_one(pi, rid, show=False):
    dst = os.path.join(LOCAL, rid)
    os.makedirs(dst, exist_ok=True)
    ok = pi.pull(f'{REMOTE}/{rid}/*', dst, recursive=True)
    rep = os.path.join(dst, 'report.md')
    if show and os.path.isfile(rep):
        txt = open(rep, encoding='utf-8').read()
        i = txt.find('## Models')
        print(txt[i:txt.find('## Tasks')] if i >= 0 else txt[:2000])
    print(f'-> {dst}' if ok else f'copy failed: {rid}')
    return 0 if ok else 1
# ----------------------------------------------------------------------------------------------------------------------
def main(a):
    if a.action == 'run':
        return run(a)
    pi = pi_app.Pi(verbose=False)
    if a.action == 'list':
        rc, out, err = _py(pi, 'import json, utils_bench as B; print(json.dumps(B.runs_summary()))')
        try:
            runs = json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            print(err or out); return 1
        for r in runs:
            head = '  '.join(f'{m} AUC {v["auc"]} {v["ms"]} ms' for m, v in (r.get('headline') or {}).items())
            print(f'{r["id"]}  {r.get("created") or "":19s}  {r.get("state") or "?":11s}  {r.get("tasks")} tasks  {head}')
        return 0
    if a.action == 'cancel':
        rc, out, _ = _py(pi, 'import utils_bench as B; print(B.cancel())')
        print('cancelled' if out.endswith('True') else 'nothing running'); return 0
    if a.action == 'pull':
        if a.id in (None, 'all'):
            _, out, _ = pi.sh(f'ls {REMOTE} 2>/dev/null')
            ids = [x for x in out.split() if x.isdigit()]
        else:
            ids = [a.id.zfill(4)]
        return max([pull_one(pi, r) for r in ids] or [0])
    return 1
# ----------------------------------------------------------------------------------------------------------------------
def add_parser(sub):
    s = sub.add_parser('bench', help='click-to-track benchmark on the Pi')
    s.add_argument('action', choices=['run', 'list', 'pull', 'cancel']); s.add_argument('id', nargs='?')
    s.add_argument('--preset', default='quick'); s.add_argument('--models'); s.add_argument('--seqs')
    s.add_argument('--tracks', type=int); s.add_argument('--frames', type=int); s.add_argument('--order'); s.add_argument('--starts')
    s.add_argument('--loss-rules', action='store_true')
    return s
