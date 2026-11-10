"""Click-to-track (SOT) benchmark: sequences with ground truth, target selection, presets, time estimate, run folders.

Method (written into every report):
  one-pass evaluation (OPE) - every model is initialised ONCE with the ground-truth box of the chosen target on the start
  frame (the first one where it is fully visible) and then runs on its own (no re-initialisation) to the end of its track.
  Multi-object ground truth (MOT: PETS09, VisDrone, ...) is used as a set of single-object tasks: each chosen track ID is
  one task; the tracker is compared only with THAT track ID's box (jumping to a neighbour = lost).
  Frames where the chosen target has no annotation are not scored.
  Frames wider than max_width are downscaled like the app's grabber does (boxes scaled with them).

The executor is bench_worker.py (a separate process: the web UI and the CLI only start it, read progress.json and cancel).
Run folder runs/bench/NNNN/ (on x86: runs/bench_x86/NNNN/, apart from the Pi runs `dtc bench pull` copies into runs/bench/):  config.json  progress.json  results.json  frames.csv  report.md  log.txt
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import numpy as np

import utils_platform
import utils_sources
import utils_tracker_sot
# ----------------------------------------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RUNS_DIR = os.path.join(REPO, 'runs', 'bench' if utils_platform.IS_ARM else 'bench_x86')
CANCEL_FLAG = 'cancel'                       # runs/bench/NNNN/cancel: asks the worker to stop (partial results are kept)
CLIPS_DIR = os.path.join(REPO, 'clips')
DECODE_MS = 3.0                                 # per frame, for the estimate (decode + resize on the Pi)
LOSS_RULES_MS = 3.0                             # extra per update of the full Tracker_SOT (embedding, contrast)
CLIP_MS = 4.0                                   # per video frame and clip half: drawing + piping to ffmpeg (the encoders run in parallel)
# ----------------------------------------------------------------------------------------------------------------------
# tracks = objects per video, the smallest first (0 = all of them); max_frames = 0: to the end of the track (tests only cap it)
DEFAULTS = {'models': ['NANO', 'VIT'], 'sequences': ['campus', 'drone_ir'], 'tracks': 3, 'max_frames': 0,
            'min_frames': 50, 'min_size': 8, 'max_width': 1280}
PRESETS = {
    'quick':    {'label': 'Quick - NANO, VIT on campus + drone_ir, 3 objects per video', **DEFAULTS},
    'standard': {'label': 'Standard - NANO, VIT, SIAMBAN on every video, 5 objects per video',
                 **DEFAULTS, 'models': ['NANO', 'VIT', 'SIAMBAN'], 'sequences': '*', 'tracks': 5},
    'full':     {'label': 'Full - every model (SIAMFC is slow), every video, 10 objects per video',
                 **DEFAULTS, 'models': ['NANO', 'VIT', 'SIAMBAN', 'SIAMFC'], 'sequences': '*', 'tracks': 10},
}
OBJECTS = (3, 5, 10, 20, 0)                     # the choices offered in the UI (0 = all)
# ----------------------------------------------------------------------------------------------------------------------
def sequences():
    """[{id, label, kind, video, gt}]: every listed source with MOT ground truth, plus reference clips (one target)."""
    out = []
    for s in utils_sources.list_sources(os.path.join(REPO, 'images')):
        if s['gt'] and os.path.isfile(s['source']):
            out.append({'id': s['name'], 'label': s['label'], 'kind': 'mot', 'video': os.path.abspath(s['source']), 'gt': os.path.abspath(s['gt'])})
    if os.path.isdir(CLIPS_DIR):
        for n in sorted(os.listdir(CLIPS_DIR)):
            d = os.path.join(CLIPS_DIR, n)
            if os.path.isfile(os.path.join(d, 'video.mp4')) and os.path.isfile(os.path.join(d, 'gt.jsonl')):
                out.append({'id': 'clip:' + n, 'label': f'clip {n} (one target)', 'kind': 'clip',
                            'video': os.path.join(d, 'video.mp4'), 'gt': os.path.join(d, 'gt.jsonl')})
    return out
# ----------------------------------------------------------------------------------------------------------------------
def video_info(path):
    import cv2
    cap = cv2.VideoCapture(path)
    info = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.release()
    return info
# ----------------------------------------------------------------------------------------------------------------------
def load_tracks(seq):
    """{track_id: {frame_index (0-based): [x1, y1, x2, y2]}} in source pixels."""
    tracks = {}
    if seq['kind'] == 'clip':
        for line in open(seq['gt']):
            r = json.loads(line)
            if r.get('box'):
                tracks.setdefault(0, {})[int(r['frame'])] = [float(v) for v in r['box']]
        return tracks
    for line in open(seq['gt']):
        p = line.strip().split(',')
        if len(p) < 6:
            continue
        f, tid, x, y, w, h = int(float(p[0])), int(float(p[1])), *[float(v) for v in p[2:6]]
        if w > 0 and h > 0:
            tracks.setdefault(tid, {})[f - 1] = [x, y, x + w, y + h]
    return tracks
# ----------------------------------------------------------------------------------------------------------------------
def select_runs(seq, cfg, size=None):
    """The single-object tasks of one sequence, the smallest objects first: [{seq, track, start, end, frames, median_side}]
    (frame indices 0-based, end exclusive). An object qualifies when it is fully inside the frame, at least min_size px, for
    min_frames frames; its task starts on the first such frame and runs to the end of its track."""
    W, H, n = size or video_info(seq['video'])
    tracks = load_tracks(seq)
    cand = []
    for tid, fb in tracks.items():
        ok = sorted(f for f, b in fb.items() if b[0] > 2 and b[1] > 2 and b[2] < W - 2 and b[3] < H - 2
                    and min(b[2] - b[0], b[3] - b[1]) >= cfg['min_size'])
        if len(ok) < cfg['min_frames']:
            continue
        last = max(fb)
        side = float(np.median([np.sqrt((b[2] - b[0]) * (b[3] - b[1])) for b in fb.values()]))
        cand.append((side, tid, ok[0], last))
    cand.sort()
    if cfg['tracks']:
        cand = cand[:cfg['tracks']]
    runs = []
    for side, tid, s, last in cand:
        end = min(last + 1, n if n > 0 else 10 ** 9, s + 1 + cfg['max_frames'] if cfg['max_frames'] else 10 ** 9)
        if end - s - 1 >= 10:
            runs.append({'seq': seq['id'], 'track': int(tid), 'start': int(s), 'end': int(end), 'frames': int(end - s - 1),
                         'median_side': round(side, 1)})
    return runs
# ----------------------------------------------------------------------------------------------------------------------
def normalize(cfg):
    """Fill defaults, validate, expand sequences '*'. Raises ValueError with a readable message."""
    c = dict(DEFAULTS)
    if cfg.get('preset') in PRESETS:
        c.update({k: v for k, v in PRESETS[cfg['preset']].items() if k != 'label'})
    c.update({k: v for k, v in cfg.items() if v is not None})
    models = [m.upper() for m in (c['models'].split(',') if isinstance(c['models'], str) else c['models']) if m]
    bad = [m for m in models if m not in utils_tracker_sot.SOT_MODELS]
    if not models or bad:
        raise ValueError(f'models: pick from {", ".join(utils_tracker_sot.SOT_MODELS)}' + (f' (unknown {bad})' if bad else ''))
    known = {s['id']: s for s in sequences()}
    seqs = c['sequences']
    seqs = list(known) if seqs in ('*', ['*']) else (seqs.split(',') if isinstance(seqs, str) else list(seqs))
    bad = [s for s in seqs if s not in known]
    if not seqs or bad:
        raise ValueError(f'sequences: unknown {bad}' if bad else 'pick at least one sequence')
    for k in ('order', 'starts'):                    # settings of older versions
        c.pop(k, None)
    for k, lo, hi in (('tracks', 0, 1000), ('max_frames', 0, 100000), ('min_frames', 10, 10000), ('min_size', 2, 1000), ('max_width', 320, 4000)):
        c[k] = int(c[k])
        if not lo <= c[k] <= hi:
            raise ValueError(f'{k} must be in [{lo}, {hi}]')
    c.pop('loss_rules', None)                       # always on now: the benchmark runs the tracker as the app does
    c['models'], c['sequences'] = models, seqs
    c.pop('label', None)
    return c
# ----------------------------------------------------------------------------------------------------------------------
def plan(cfg):
    """normalize + every single-object task + the time estimate. {config, runs, frames, updates, estimate_s, per_sequence}"""
    c = normalize(cfg)
    known = {s['id']: s for s in sequences()}
    runs, per_seq = [], []
    for sid in c['sequences']:
        seq = known[sid]
        W, H, n = video_info(seq['video'])
        r = select_runs(seq, c, (W, H, n))
        runs += r
        per_seq.append({'seq': sid, 'size': [W, H], 'frames_in_video': n, 'runs': len(r), 'frames': sum(x['frames'] for x in r)})
    frames = sum(r['frames'] for r in runs)
    est = estimate_s(c, frames, sum(s['frames_in_video'] for s in per_seq if s['runs']))
    return {'config': c, 'runs': runs, 'frames': frames, 'updates': frames * len(c['models']),
            'estimate_s': round(est), 'per_sequence': per_seq}
# ----------------------------------------------------------------------------------------------------------------------
def model_ms(c, m):
    return utils_tracker_sot.SOT_MODELS[m]['pi_ms'] + LOSS_RULES_MS
# ----------------------------------------------------------------------------------------------------------------------
def render_ms(c):
    """Per video frame when the clips are made: decode + 2 clips (recall, precision) per model."""
    return DECODE_MS + 2 * len(c['models']) * CLIP_MS
# ----------------------------------------------------------------------------------------------------------------------
def estimate_s(c, frames, video_frames=0):
    return (frames * (DECODE_MS + sum(model_ms(c, m) for m in c['models'])) + video_frames * render_ms(c)) / 1000.0
# ----------------------------------------------------------------------------------------------------------------------
def readiness(cfg):
    """[{key, status: ok|warn|fail, detail}] - read only."""
    checks = []
    add = lambda k, st, d: checks.append({'key': k, 'status': st, 'detail': d})
    try:
        p = plan(cfg)
    except ValueError as e:
        add('config', 'fail', str(e)); return checks, None
    c = p['config']
    T = utils_tracker_sot.Tracker_SOT(models_dir=os.path.join(REPO, 'models'))
    for m in c['models']:
        miss = [f for f in T.weights(m) if not os.path.isfile(f)]
        add(f'weights {m}', 'fail' if miss else 'ok', f'missing {miss[0]}' if miss else 'present')
    if any(m in ('SIAMBAN', 'SIAMFC') for m in c['models']):
        try:
            import torch  # noqa: F401
            add('torch', 'ok', f'torch {torch.__version__}')
        except ImportError:
            add('torch', 'fail', 'not installed (needed by SIAMBAN / SIAMFC)')
    for s in p['per_sequence']:
        add(f'sequence {s["seq"]}', 'ok' if s['runs'] else 'warn',
            f'{s["runs"]} task(s), {s["frames"]} frames, video {s["size"][0]}x{s["size"][1]}' if s['runs'] else
            f'skipped: no target is at least {c["min_size"]} px for {c["min_frames"]} frames inside the frame')
    if not p['runs']:
        add('tasks', 'fail', 'nothing to run')
    free = shutil.disk_usage(REPO).free / 1e9
    add('disk', 'ok' if free > 0.5 else 'fail', f'{free:.1f} GB free')
    thr = _cmd(['vcgencmd', 'get_throttled'])
    if thr is not None:
        v = int(thr.split('=')[-1], 16) if '=' in thr else 0
        add('throttling', 'ok' if v == 0 else 'warn', 'none' if v == 0 else f'{thr} - timings may be slow (under-voltage / heat)')
    t = _cmd(['vcgencmd', 'measure_temp'])
    if t is not None:
        deg = float(re.findall(r'[\d.]+', t)[0])
        add('temperature', 'ok' if deg < 70 else 'warn', f'{deg:.0f} C' + ('' if deg < 70 else ' - the CPU may throttle'))
    cur = current()
    if cur:
        add('running', 'fail', f'benchmark {cur["id"]} is running')
    add('estimate', 'ok', f'{len(p["runs"])} tasks, {p["frames"]} frames x {len(c["models"])} model(s): about {fmt_s(p["estimate_s"])}')
    return checks, p
# ----------------------------------------------------------------------------------------------------------------------
def _cmd(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
# ----------------------------------------------------------------------------------------------------------------------
def fmt_s(s):
    s = int(round(s))
    return f'{s // 3600} h {s % 3600 // 60} min' if s >= 3600 else f'{s // 60} min {s % 60} s' if s >= 60 else f'{s} s'
# ----------------------------------------------------------------------------------------------------------------------
# run folders
# ----------------------------------------------------------------------------------------------------------------------
def run_ids():
    return sorted(n for n in os.listdir(RUNS_DIR) if re.fullmatch(r'\d{4}', n)) if os.path.isdir(RUNS_DIR) else []
# ----------------------------------------------------------------------------------------------------------------------
def new_run_dir():
    os.makedirs(RUNS_DIR, exist_ok=True)
    k = int(run_ids()[-1]) + 1 if run_ids() else 1
    while True:
        d = os.path.join(RUNS_DIR, f'{k:04d}')
        try:
            os.makedirs(d)
            return d
        except FileExistsError:
            k += 1
# ----------------------------------------------------------------------------------------------------------------------
def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
# ----------------------------------------------------------------------------------------------------------------------
def write_json(path, value):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)
# ----------------------------------------------------------------------------------------------------------------------
def _alive(pid):
    return utils_platform.pid_alive(pid)
# ----------------------------------------------------------------------------------------------------------------------
def progress(run_id):
    """progress.json of a run; a RUNNING run whose worker is gone is reported (and stored) as INTERRUPTED."""
    d = os.path.join(RUNS_DIR, run_id)
    p = read_json(os.path.join(d, 'progress.json'))
    if p is None:
        return None
    if p.get('state') == 'RUNNING' and not _alive(p.get('pid')):
        p['state'], p['stage'] = 'INTERRUPTED', 'worker is gone'
        write_json(os.path.join(d, 'progress.json'), p)
    return p
# ----------------------------------------------------------------------------------------------------------------------
_current_cache = {'t': 0.0, 'value': None}
def current(max_age=1.0):
    """The running benchmark's progress (or None). Cached for max_age s: the app's main loop asks every frame."""
    if time.time() - _current_cache['t'] < max_age:
        return _current_cache['value']
    value = None
    for rid in reversed(run_ids()[-5:]):
        p = progress(rid)
        if p and p.get('state') == 'RUNNING':
            value = p; break
    _current_cache.update(t=time.time(), value=value)
    return value
# ----------------------------------------------------------------------------------------------------------------------
def start(cfg, python=None):
    """Plan, create the run folder, launch bench_worker.py detached. Returns the run id. Raises ValueError."""
    if current(max_age=0):
        raise ValueError(f'benchmark {current(max_age=0)["id"]} is already running')
    p = plan(cfg)
    if not p['runs']:
        raise ValueError('nothing to run: no track fits the settings')
    d = new_run_dir()
    rid = os.path.basename(d)
    write_json(os.path.join(d, 'config.json'), {**p, 'id': rid, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), **provenance()})
    write_json(os.path.join(d, 'progress.json'), {'id': rid, 'state': 'RUNNING', 'stage': 'starting', 'pid': None, 'done_frac': 0.0,
                                                  'tasks_total': len(p['runs']), 'tasks_done': 0, 'frames_total': p['frames'], 'frames_done': 0,
                                                  'estimate_s': p['estimate_s'], 'elapsed_s': 0, 'left_s': p['estimate_s'], 'current': None})
    log = open(os.path.join(d, 'log.txt'), 'w')
    proc = subprocess.Popen([python or sys.executable, os.path.join(HERE, 'bench_worker.py'), d], cwd=HERE, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, **utils_platform.detached_kwargs())
    pr = read_json(os.path.join(d, 'progress.json'), {})
    if pr.get('pid') is None:
        pr['pid'] = proc.pid
        write_json(os.path.join(d, 'progress.json'), pr)
    _current_cache['t'] = 0.0
    return rid
# ----------------------------------------------------------------------------------------------------------------------
def cancel(run_id=None):
    p = progress(run_id) if run_id else current(max_age=0)
    if not p or p.get('state') != 'RUNNING':
        return False
    open(os.path.join(RUNS_DIR, p['id'], CANCEL_FLAG), 'w').close()      # the worker polls it: works on Windows too
    if utils_platform.IS_WINDOWS:
        return utils_platform.pid_alive(p.get('pid'))                   # SIGTERM there = TerminateProcess: no partial results
    try:
        os.kill(int(p['pid']), signal.SIGTERM)
    except (OSError, TypeError, ValueError):
        return False
    return True
# ----------------------------------------------------------------------------------------------------------------------
def provenance():
    git = lambda *a: (_cmd(['git', '-C', REPO, *a]) or '').strip()
    import platform
    return {'commit': git('rev-parse', '--short', 'HEAD'), 'branch': git('rev-parse', '--abbrev-ref', 'HEAD'),
            'tools_commit': (_cmd(['git', '-C', os.path.join(REPO, 'tools'), 'rev-parse', '--short', 'HEAD']) or '').strip(),
            'host': platform.node(), 'machine': platform.machine(), 'platform': utils_platform.name()}
# ----------------------------------------------------------------------------------------------------------------------
def runs_summary():
    """Newest first: [{id, created, state, models, sequences, tasks, frames, elapsed_s, headline}]"""
    out = []
    for rid in reversed(run_ids()):
        d = os.path.join(RUNS_DIR, rid)
        c = read_json(os.path.join(d, 'config.json'), {})
        p = progress(rid) or {}
        r = read_json(os.path.join(d, 'results.json'), {})
        cfg = c.get('config', {})
        out.append({'id': rid, 'created': c.get('created'), 'state': p.get('state'), 'commit': c.get('commit'),
                    'models': cfg.get('models'), 'sequences': cfg.get('sequences'), 'tasks': len(c.get('runs', [])),
                    'frames': c.get('frames'), 'elapsed_s': p.get('elapsed_s'),
                    'headline': {m: {k: v.get(k) for k in ('f1', 'fps')} for m, v in (r.get('models') or {}).items()}})
    return out
# ----------------------------------------------------------------------------------------------------------------------
def run_detail(run_id):
    d = os.path.join(RUNS_DIR, run_id)
    if not re.fullmatch(r'\d{4}', run_id or '') or not os.path.isdir(d):
        return None
    rep = os.path.join(d, 'report.md')
    return {'id': run_id, 'config': read_json(os.path.join(d, 'config.json')), 'progress': progress(run_id),
            'results': read_json(os.path.join(d, 'results.json')),
            'report': open(rep, encoding='utf-8').read() if os.path.isfile(rep) else None,
            'files': sorted(os.listdir(d))}
# ----------------------------------------------------------------------------------------------------------------------
def timelines(run_id, model):
    """Every object's frame states of one model, for the Gantt view: {task: {'recall': str, 'precision': str}}, one character
    per frame from the object's start: recall c = caught, m = missed, . = no target; precision g = box on the target,
    f = false box, . = no box. None for runs made before the hit / fp / fn timeline existed."""
    path = os.path.join(RUNS_DIR, run_id, 'frames.csv')
    if not re.fullmatch(r'\d{4}', run_id or '') or not os.path.isfile(path):
        return None
    out = {}
    with open(path) as f:
        if not next(f, '').startswith('task,model,frame,gt,box,hit,ms'):
            return None
        for line in f:
            t, m, fr, gt, box, hit = line.rstrip('\n').split(',')[:6]
            if m != model:
                continue
            o = out.setdefault(int(t), {'recall': [], 'precision': []})
            o['recall'].append('.' if gt == '0' else 'c' if hit == '1' else 'm')
            o['precision'].append('.' if box == '0' else 'g' if hit == '1' else 'f')
    return {t: {k: ''.join(v) for k, v in o.items()} for t, o in out.items()}
# ----------------------------------------------------------------------------------------------------------------------
def task_series(run_id, task):
    """Per-frame state of one task for the timeline: {model: {frame: [...], gt: [0/1], box: [0/1], hit: [0/1]}}
    (None for runs made before the hit / fp / fn timeline existed)."""
    path = os.path.join(RUNS_DIR, run_id, 'frames.csv')
    if not re.fullmatch(r'\d{4}', run_id or '') or not os.path.isfile(path) or not str(task).isdigit():
        return None
    out = {}
    with open(path) as f:
        if not next(f, '').startswith('task,model,frame,gt,box,hit,ms'):
            return None
        for line in f:
            t, m, fr, gt, box, hit = line.rstrip('\n').split(',')[:6]
            if int(t) != int(task):
                continue
            s = out.setdefault(m, {'frame': [], 'gt': [], 'box': [], 'hit': []})
            s['frame'].append(int(fr)); s['gt'].append(int(gt)); s['box'].append(int(box)); s['hit'].append(int(hit))
    return out
# ----------------------------------------------------------------------------------------------------------------------
def video_path(run_id, name):
    """Absolute path of a clip of a run (only files of <run>/video/ with a clip name), else None."""
    if not re.fullmatch(r'\d{4}', run_id or '') or not re.fullmatch(r'[A-Za-z0-9_.-]+__[A-Z]+\.mp4', name or ''):
        return None
    p = os.path.join(RUNS_DIR, run_id, 'video', name)
    return p if os.path.isfile(p) else None
# ----------------------------------------------------------------------------------------------------------------------
def options():
    T = utils_tracker_sot.Tracker_SOT(models_dir=os.path.join(REPO, 'models'))
    return {'presets': {k: {kk: vv for kk, vv in v.items()} for k, v in PRESETS.items()},
            'models': [{**m, 'pi_ms': utils_tracker_sot.SOT_MODELS[m['name']]['pi_ms']} for m in T.available()],
            'sequences': [{'id': s['id'], 'label': s['label'], 'kind': s['kind']} for s in sequences()],
            'objects': OBJECTS, 'defaults': DEFAULTS}
# ----------------------------------------------------------------------------------------------------------------------
