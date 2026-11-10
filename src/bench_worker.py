"""Click-to-track benchmark executor: python bench_worker.py <run folder with config.json>
(started by utils_bench.start() from the web UI or `dtc bench run`; SIGTERM = cancel, partial results are kept).

Per task (one object of a video, from its first full view to the end of its track) every model runs side by side on the
same decoded frames, as the app runs it (Tracker_SOT: the model + the app's loss rules, so it can drop the box).
Every frame is one of:
  hit   the box is on the target       (IoU >= 0.5, or its centre within half the target size + 2 px of the true centre)
  fp    a box, but not on the target   (wrong place, or the target is not annotated on this frame)
  fn    the target is there, no box on it (no box at all, or the box is elsewhere - then the frame is fp and fn)
precision = hit / (hit + fp), recall = hit / (hit + fn), F1; fps from the median time of one update.
Writes progress.json (about twice a second), frames.csv, results.json, report.md, and the clips of bench_render.py in video/.
"""
import os
import signal
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import utils_bench as B                                             # noqa: E402
import utils_tracker_sot as U                                       # noqa: E402
# ----------------------------------------------------------------------------------------------------------------------
HIT_IOU, HIT_PX = 0.5, 2.0
# ----------------------------------------------------------------------------------------------------------------------
def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0
# ----------------------------------------------------------------------------------------------------------------------
def is_hit(box, gt):
    if iou(box, gt) >= HIT_IOU:
        return True
    side = np.sqrt(max(1.0, (gt[2] - gt[0]) * (gt[3] - gt[1])))
    d = np.hypot((box[0] + box[2] - gt[0] - gt[2]) / 2, (box[1] + box[3] - gt[1] - gt[3]) / 2)
    return bool(d <= side / 2 + HIT_PX)
# ----------------------------------------------------------------------------------------------------------------------
def frame_state(box, gt):
    """(hit, fp, fn) of one frame; box / gt = xyxy or None."""
    if box is None:
        return 0, 0, int(gt is not None)
    if gt is None:
        return 0, 1, 0
    return (1, 0, 0) if is_hit(box, gt) else (0, 1, 1)
# ----------------------------------------------------------------------------------------------------------------------
def prf(hit, fp, fn):
    p = hit / (hit + fp) if hit + fp else None
    r = hit / (hit + fn) if hit + fn else None
    f = (2 * p * r / (p + r) if p + r else 0.0) if p is not None and r is not None else None
    rd = lambda v: None if v is None else round(v, 3)
    return {'precision': rd(p), 'recall': rd(r), 'f1': rd(f)}
# ----------------------------------------------------------------------------------------------------------------------
class Worker:
    def __init__(self, d):
        self.d = d
        self.cfg_all = B.read_json(os.path.join(d, 'config.json'))
        self.c = self.cfg_all['config']
        self.tasks = self.cfg_all['runs']
        self.cancelled = False
        self.t0 = time.time()
        self.per_frame = B.DECODE_MS + sum(B.model_ms(self.c, m) for m in self.c['models'])
        self.render_frames = sum(s['frames_in_video'] for s in self.cfg_all.get('per_sequence', []) if s['runs'])
        self.render_per_frame = B.render_ms(self.c)
        self.total_work = sum(t['frames'] for t in self.tasks) * self.per_frame + self.render_frames * self.render_per_frame
        self.done_work = 0.0
        self.p = {'id': self.cfg_all['id'], 'state': 'RUNNING', 'pid': os.getpid(), 'stage': 'loading models', 'tasks_total': len(self.tasks),
                  'tasks_done': 0, 'frames_total': self.cfg_all['frames'], 'frames_done': 0, 'done_frac': 0.0,
                  'estimate_s': self.cfg_all['estimate_s'], 'elapsed_s': 0, 'left_s': self.cfg_all['estimate_s'], 'current': None}
        self._last_write = 0.0
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)

    def _on_term(self, *_):
        self.cancelled = True

    @property
    def cancelled(self):
        """SIGTERM (Linux) or the cancel flag file in the run folder (any OS: on Windows SIGTERM kills without cleanup)."""
        if not self._cancelled and os.path.exists(os.path.join(self.d, B.CANCEL_FLAG)):
            self._cancelled = True
        return self._cancelled

    @cancelled.setter
    def cancelled(self, value):
        self._cancelled = value

    def write_progress(self, force=False):
        if not force and time.time() - self._last_write < 0.5:
            return
        el = time.time() - self.t0
        frac = min(1.0, self.done_work / self.total_work) if self.total_work else 1.0
        left = (el / frac * (1 - frac)) if frac > 0.03 else self.cfg_all['estimate_s'] * (1 - frac)
        self.p.update(elapsed_s=round(el, 1), done_frac=round(frac, 4), left_s=round(left))
        B.write_json(os.path.join(self.d, 'progress.json'), self.p)
        self._last_write = time.time()

    # ------------------------------------------------------------------------------------------------------------------
    def run(self):
        seqs = {s['id']: s for s in B.sequences()}
        models_dir = os.path.join(B.REPO, 'models')
        results, csv = [], open(os.path.join(self.d, 'frames.csv'), 'w')
        # per frame: target annotated, box shown, box on the target, ms; the boxes (benchmark frame pixels) for the clips
        csv.write('task,model,frame,gt,box,hit,ms,bx1,by1,bx2,by2,gx1,gy1,gx2,gy2\n')
        self.write_progress(force=True)
        for ti, task in enumerate(self.tasks):
            if self.cancelled:
                break
            seq = seqs[task['seq']]
            truth = B.load_tracks(seq)[task['track']]
            label = f'{task["seq"]} · object {task["track"]} · from frame {task["start"]}'
            self.p['current'] = {'task': ti + 1, 'label': label, 'frame': 0, 'frames': task['frames']}
            self.p['stage'] = 'running'
            cap = cv2.VideoCapture(seq['video'])
            for _ in range(task['start']):
                cap.grab()
            ok, frame = cap.read()
            if not ok:
                print(f'[ bench ] {label}: cannot read the start frame', flush=True); continue
            k = min(1.0, self.c['max_width'] / frame.shape[1])
            scale = lambda b: [v * k for v in b]
            prep = (lambda f: cv2.resize(f, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)) if k < 1 else (lambda f: f)
            frame = prep(frame)
            box0 = [int(round(v)) for v in scale(truth[task['start']])]
            T, acc = {}, {}
            for m in self.c['models']:
                T[m] = U.Tracker_SOT(model=m, models_dir=models_dir)
                t = time.perf_counter(); T[m].start(frame, box0); init_ms = (time.perf_counter() - t) * 1000
                acc[m] = {'hit': 0, 'fp': 0, 'fn': 0, 'ms': [], 'init_ms': round(init_ms, 1), 'stopped_at': None, 'reason': None}
            for rel in range(1, task['frames'] + 1):
                if self.cancelled:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                frame = prep(frame)
                f = task['start'] + rel
                g = scale(truth[f]) if f in truth else None
                for m in self.c['models']:
                    a, tr = acc[m], T[m]
                    box, ms = None, ''
                    if tr.is_active:
                        t = time.perf_counter(); box, _ = tr.update(frame); ms = (time.perf_counter() - t) * 1000
                        a['ms'].append(ms)
                        if not tr.is_active:
                            a['stopped_at'], a['reason'] = rel, tr.stop_reason
                    h, fp, fn = frame_state(box, g)
                    a['hit'] += h; a['fp'] += fp; a['fn'] += fn
                    bs = ',,,' if box is None else ','.join(str(int(v)) for v in box)
                    gs = ',,,' if g is None else ','.join(f'{v:.1f}' for v in g)
                    csv.write(f'{ti},{m},{rel},{int(g is not None)},{int(box is not None)},{h},{"" if ms == "" else round(ms, 1)},{bs},{gs}\n')
                self.done_work += self.per_frame
                self.p['frames_done'] += 1
                self.p['current']['frame'] = rel
                self.write_progress()
            cap.release()
            for m in self.c['models']:
                a = acc[m]
                results.append({'task': ti, 'seq': task['seq'], 'track': task['track'], 'start': task['start'], 'frames': task['frames'],
                                'median_side': task['median_side'], 'model': m, 'hit': a['hit'], 'fp': a['fp'], 'fn': a['fn'],
                                **prf(a['hit'], a['fp'], a['fn']), 'ms': round(float(np.median(a['ms'])), 1) if a['ms'] else None,
                                'init_ms': a['init_ms'], 'stopped_at': a['stopped_at'], 'stop_reason': a['reason']})
            csv.flush()
            if not self.cancelled:
                self.p['tasks_done'] = ti + 1
            print(f'[ bench ] {ti + 1}/{len(self.tasks)} {label}: ' +
                  ', '.join(f'{r["model"]} F1 {r["f1"]} {r["ms"]} ms' for r in results if r['task'] == ti), flush=True)
        csv.close()
        videos = {}
        if not self.cancelled:
            self.p['stage'] = 'videos'
            self.p['current'] = {'task': len(self.tasks), 'label': 'making the hit / miss videos', 'frame': 0, 'frames': self.render_frames}
            import bench_render
            def step(nf):
                self.done_work += nf * self.render_per_frame
                self.p['current']['frame'] += nf
                self.write_progress()
            try:
                videos = bench_render.render(self.d, step)
            except Exception as e:                      # no clips is not a failed benchmark
                print(f'[ bench ] videos failed: {type(e).__name__}: {e}', flush=True)
        self.p['stage'] = 'report'
        self.write_progress(force=True)
        summary = aggregate(self.c, results)
        B.write_json(os.path.join(self.d, 'results.json'), {'version': 2, 'models': summary['models'], 'by_sequence': summary['by_sequence'],
                                                            'tasks': results, 'videos': videos, 'complete': not self.cancelled})
        open(os.path.join(self.d, 'report.md'), 'w', encoding='utf-8').write(report(self.cfg_all, summary, self.cancelled, time.time() - self.t0))
        self.p.update(state='CANCELLED' if self.cancelled else 'DONE', stage='cancelled - partial results kept' if self.cancelled else 'done',
                      current=None, finished=time.strftime('%Y-%m-%d %H:%M:%S'))
        if not self.cancelled:
            self.done_work = self.total_work
        self.write_progress(force=True)
# ----------------------------------------------------------------------------------------------------------------------
def _agg(rows):
    if not rows:
        return {}
    hit, fp, fn = (sum(r[k] for r in rows) for k in ('hit', 'fp', 'fn'))
    ms = [r['ms'] for r in rows if r['ms'] is not None]
    med = float(np.median(ms)) if ms else None
    return {'tasks': len(rows), 'hit': hit, 'fp': fp, 'fn': fn, **prf(hit, fp, fn),
            'ms': round(med, 1) if med else None, 'fps': round(1000.0 / med) if med else None}
# ----------------------------------------------------------------------------------------------------------------------
def aggregate(c, results):
    models = {m: _agg([r for r in results if r['model'] == m]) for m in c['models']}
    by_seq = {s: {m: _agg([r for r in results if r['model'] == m and r['seq'] == s]) for m in c['models']} for s in c['sequences']}
    return {'models': models, 'by_sequence': {s: v for s, v in by_seq.items() if any(v.values())}}
# ----------------------------------------------------------------------------------------------------------------------
def report(cfg_all, summary, cancelled, elapsed):
    c = cfg_all['config']
    L = [f'# SOT benchmark {cfg_all["id"]}' + (' (CANCELLED - partial)' if cancelled else ''), '',
         f'{cfg_all.get("created")} · {cfg_all.get("host")} · DTC {cfg_all.get("commit")} ({cfg_all.get("branch")}) · {B.fmt_s(elapsed)}', '',
         f'Each object (the {c["tracks"] or "all"} smallest per video, >= {c["min_size"]} px) is tracked as the app does it - the model with the '
         'app\'s loss rules - from its first full view to the end of its track. hit = box on the target (IoU >= 0.5 or centre within half the '
         'target size + 2 px); fp = box not on the target; fn = target without a box on it.', '',
         '| model | precision | recall | F1 | fps | ms | hit | fp | fn |', '|---|---|---|---|---|---|---|---|---|']
    for m, v in summary['models'].items():
        if v:
            L.append(f'| {m} | {v["precision"]} | {v["recall"]} | {v["f1"]} | {v["fps"]} | {v["ms"]} | {v["hit"]} | {v["fp"]} | {v["fn"]} |')
    L += ['', '| sequence | ' + ' | '.join(f'{m} F1' for m in c['models']) + ' |', '|---' * (len(c['models']) + 1) + '|']
    for s, mv in summary['by_sequence'].items():
        L.append(f'| {s} | ' + ' | '.join(str(mv[m].get('f1')) if mv.get(m) else '' for m in c['models']) + ' |')
    return '\n'.join(L) + '\n'
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    W = Worker(sys.argv[1])
    try:
        W.run()
    except Exception as e:
        import traceback
        traceback.print_exc()
        W.p.update(state='FAILED', stage=f'{type(e).__name__}: {e}')
        W.write_progress(force=True)
        sys.exit(1)
