"""Click-to-track benchmark executor: python bench_worker.py <run folder with config.json>
(started by utils_bench.start() from the web UI or `dtc bench run`; SIGTERM = cancel, partial results are kept).

Per task (one target, one start) every model runs side by side on the same decoded frames:
  raw tracker      - the model alone: IoU against the truth, score, time per update
  with loss rules  - (config loss_rules) the app's Tracker_SOT: when it stopped by itself and why
Writes progress.json (about twice a second), frames.csv, results.json, report.md.
"""
import json
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
LOST_IOU, LOST_FRAMES, EARLY = 0.1, 5, 10       # lost = IoU < 0.1 on 5 scored frames in a row; a stop > 10 frames before that is early
AUC_TH = np.linspace(0, 1, 21)
# ----------------------------------------------------------------------------------------------------------------------
def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0
# ----------------------------------------------------------------------------------------------------------------------
def task_metrics(ious, errs, ms, on, off, rel_frames):
    """ious/errs on scored frames (rel_frames = their index after the start)."""
    a = np.array(ious, dtype=float)
    lost_at, bad = None, 0
    for f, v in zip(rel_frames, ious):
        bad = bad + 1 if v < LOST_IOU else 0
        if bad >= LOST_FRAMES:
            lost_at = f - LOST_FRAMES + 1; break
    pct = lambda v: [round(float(np.percentile(v, q)), 3) for q in (5, 50, 95)] if v else None
    return {'scored': len(a), 'iou': round(float(a.mean()), 3) if len(a) else None,
            'success': round(float((a >= 0.5).mean()), 3) if len(a) else None,
            'auc': round(float(np.mean([(a >= t).mean() for t in AUC_TH])), 3) if len(a) else None,
            'precision20': round(float((np.array(errs) <= 20).mean()), 3) if errs else None,
            'center_err': round(float(np.median(errs)), 1) if errs else None,
            'lost_at': lost_at, 'ms': round(float(np.median(ms)), 1) if ms else None,
            'ms_p95': round(float(np.percentile(ms, 95)), 1) if ms else None, 'score_on': pct(on), 'score_off': pct(off)}
# ----------------------------------------------------------------------------------------------------------------------
class Worker:
    def __init__(self, d):
        self.d = d
        self.cfg_all = B.read_json(os.path.join(d, 'config.json'))
        self.c = self.cfg_all['config']
        self.tasks = self.cfg_all['runs']
        self.cancelled = False
        self.t0 = time.time()
        self.total_work = sum(t['frames'] for t in self.tasks) * (B.DECODE_MS + sum(B.model_ms(self.c, m) for m in self.c['models']))
        self.done_work = 0.0
        self.p = {'id': self.cfg_all['id'], 'state': 'RUNNING', 'pid': os.getpid(), 'stage': 'loading models', 'tasks_total': len(self.tasks),
                  'tasks_done': 0, 'frames_total': self.cfg_all['frames'], 'frames_done': 0, 'done_frac': 0.0,
                  'estimate_s': self.cfg_all['estimate_s'], 'elapsed_s': 0, 'left_s': self.cfg_all['estimate_s'], 'current': None}
        self._last_write = 0.0
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)

    def _on_term(self, *_):
        self.cancelled = True

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
        csv.write('task,model,frame,iou,score,ms\n')
        self.write_progress(force=True)
        for ti, task in enumerate(self.tasks):
            if self.cancelled:
                break
            seq = seqs[task['seq']]
            tracks = B.load_tracks(seq)
            truth = tracks[task['track']]
            label = f'{task["seq"]} · target {task["track"]} · from frame {task["start"]}'
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
            b0 = scale(truth[task['start']])
            box0 = [int(round(v)) for v in b0]
            raw, full, acc = {}, {}, {}
            for m in self.c['models']:
                T = U.Tracker_SOT(model=m, models_dir=models_dir)
                be = T._create()
                t = time.perf_counter(); be.init(frame, (box0[0], box0[1], box0[2] - box0[0], box0[3] - box0[1])); init_ms = (time.perf_counter() - t) * 1000
                raw[m] = be
                acc[m] = {'ious': [], 'errs': [], 'ms': [], 'on': [], 'off': [], 'rel': [], 'init_ms': round(init_ms, 1), 'stopped_at': None, 'reason': None}
                if self.c['loss_rules']:
                    F = U.Tracker_SOT(model=m, models_dir=models_dir)
                    F.start(frame, box0)
                    full[m] = F
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
                    a = acc[m]
                    t = time.perf_counter(); okm, (x, y, w, h), score = raw[m].update(frame); ms = (time.perf_counter() - t) * 1000
                    a['ms'].append(ms)
                    v = ''
                    if g is not None:
                        b = [x, y, x + w, y + h]
                        v = iou(b, g)
                        a['ious'].append(v); a['rel'].append(rel)
                        a['errs'].append(float(np.hypot(x + w / 2 - (g[0] + g[2]) / 2, y + h / 2 - (g[1] + g[3]) / 2)))
                        (a['on'] if v >= 0.3 else a['off'] if v < 0.1 else []).append(score)
                    csv.write(f'{ti},{m},{rel},{"" if v == "" else round(v, 3)},{round(float(score), 3)},{round(ms, 1)}\n')
                    if m in full and full[m].is_active:
                        full[m].update(frame)
                        if not full[m].is_active:
                            a['stopped_at'], a['reason'] = rel, full[m].stop_reason
                self.done_work += B.DECODE_MS + sum(B.model_ms(self.c, m) for m in self.c['models'])
                self.p['frames_done'] += 1
                self.p['current']['frame'] = rel
                self.write_progress()
            cap.release()
            for m in self.c['models']:
                a = acc[m]
                r = task_metrics(a['ious'], a['errs'], a['ms'], a['on'], a['off'], a['rel'])
                r.update(task=ti, seq=task['seq'], track=task['track'], start=task['start'], frames=task['frames'],
                         median_side=task['median_side'], model=m, init_ms=a['init_ms'])
                if self.c['loss_rules']:
                    r['stopped_at'], r['stop_reason'] = a['stopped_at'], a['reason']
                    lost, stop = r['lost_at'], a['stopped_at']
                    r['loss_verdict'] = ('early_stop' if stop is not None and (lost is None or stop < lost - EARLY) else
                                         'caught' if stop is not None else 'missed' if lost is not None else 'kept')
                results.append(r)
            csv.flush()
            if not self.cancelled:
                self.p['tasks_done'] = ti + 1
            print(f'[ bench ] {ti + 1}/{len(self.tasks)} {label}: ' +
                  ', '.join(f'{r["model"]} IoU {r["iou"]} {r["ms"]} ms' for r in results if r['task'] == ti), flush=True)
        csv.close()
        self.p['stage'] = 'report'
        self.write_progress(force=True)
        summary = aggregate(self.c, results)
        B.write_json(os.path.join(self.d, 'results.json'), {'models': summary['models'], 'by_sequence': summary['by_sequence'], 'tasks': results,
                                                            'complete': not self.cancelled})
        open(os.path.join(self.d, 'report.md'), 'w', encoding='utf-8').write(report(self.cfg_all, summary, results, self.cancelled, time.time() - self.t0))
        self.p.update(state='CANCELLED' if self.cancelled else 'DONE', stage='cancelled - partial results kept' if self.cancelled else 'done',
                      current=None, finished=time.strftime('%Y-%m-%d %H:%M:%S'))
        if not self.cancelled:
            self.done_work = self.total_work
        self.write_progress(force=True)
# ----------------------------------------------------------------------------------------------------------------------
def _agg(rows, loss_rules):
    if not rows:
        return {}
    mean = lambda k: round(float(np.mean([r[k] for r in rows if r[k] is not None])), 3) if any(r[k] is not None for r in rows) else None
    out = {'tasks': len(rows), 'iou': mean('iou'), 'success': mean('success'), 'auc': mean('auc'), 'precision20': mean('precision20'),
           'center_err': mean('center_err'), 'ms': round(float(np.median([r['ms'] for r in rows if r['ms'] is not None])), 1),
           'ms_p95': round(float(np.median([r['ms_p95'] for r in rows if r['ms_p95'] is not None])), 1),
           'lost': sum(1 for r in rows if r['lost_at'] is not None)}
    if loss_rules:
        for v in ('caught', 'missed', 'early_stop', 'kept'):
            out[v] = sum(1 for r in rows if r.get('loss_verdict') == v)
    return out
# ----------------------------------------------------------------------------------------------------------------------
def aggregate(c, results):
    models = {m: _agg([r for r in results if r['model'] == m], c['loss_rules']) for m in c['models']}
    by_seq = {s: {m: _agg([r for r in results if r['model'] == m and r['seq'] == s], c['loss_rules']) for m in c['models']}
              for s in c['sequences']}
    return {'models': models, 'by_sequence': {s: v for s, v in by_seq.items() if any(v.values())}}
# ----------------------------------------------------------------------------------------------------------------------
def report(cfg_all, summary, results, cancelled, elapsed):
    c = cfg_all['config']
    L = [f'# SOT benchmark {cfg_all["id"]}' + (' (CANCELLED - partial)' if cancelled else ''), '',
         f'{cfg_all.get("created")} · {cfg_all.get("host")} · DTC {cfg_all.get("commit")} ({cfg_all.get("branch")}) · tools {cfg_all.get("tools_commit")} · '
         f'{B.fmt_s(elapsed)} (estimate {B.fmt_s(cfg_all["estimate_s"])})', '',
         '## Method', '',
         '- One-pass evaluation: each model is initialised once with the ground-truth box of the target on the start frame and then tracks '
         'on its own, without re-initialisation, until the target\'s track ends or max_frames.',
         '- Multi-object ground truth is used as single-object tasks: one task = one track ID (+ start frame); the tracker is compared only '
         'with that ID\'s box, so jumping to a neighbour counts as lost. Frames where the target has no box are not scored.',
         f'- Targets per sequence: {c["order"]} {c["tracks"] or "all"} track(s) that are fully inside the frame, at least {c["min_size"]} px and '
         f'{c["min_frames"]} frames; start(s): {c["starts"]}; at most {c["max_frames"]} frames per task; frames wider than {c["max_width"]} px downscaled.',
         '- IoU = intersection over union with the truth box. success = share of scored frames with IoU >= 0.5; AUC = mean success over '
         'IoU thresholds 0..1 (OTB style); precision20 = share of frames with the centre within 20 px; lost = IoU < 0.1 on 5 frames in a row; '
         'ms = median time of one tracker update on this machine.']
    if c['loss_rules']:
        L.append('- With loss rules (the app\'s Tracker_SOT): caught = stopped itself at most 10 frames before the loss or later; '
                 'early_stop = stopped while still on the target; missed = lost and kept going; kept = never lost, never stopped.')
    L += ['', f'Models: {", ".join(c["models"])} · sequences: {", ".join(c["sequences"])} · {len(cfg_all["runs"])} tasks, {cfg_all["frames"]} frames', '',
          '## Models (mean over tasks)', '']
    cols = ['tasks', 'auc', 'success', 'iou', 'precision20', 'center_err', 'lost', 'ms', 'ms_p95'] + (['caught', 'missed', 'early_stop', 'kept'] if c['loss_rules'] else [])
    L += ['| model | ' + ' | '.join(cols) + ' |', '|---' * (len(cols) + 1) + '|']
    for m, v in summary['models'].items():
        L.append(f'| {m} | ' + ' | '.join(str(v.get(k, '')) for k in cols) + ' |')
    L += ['', '## By sequence (AUC / ms)', '', '| sequence | ' + ' | '.join(c['models']) + ' |', '|---' * (len(c['models']) + 1) + '|']
    for s, mv in summary['by_sequence'].items():
        L.append(f'| {s} | ' + ' | '.join(f'{mv[m].get("auc")} / {mv[m].get("ms")}' if mv.get(m) else '' for m in c['models']) + ' |')
    L += ['', '## Tasks', '', '| # | sequence | target | start | frames | side px | model | AUC | IoU | lost at | ms' + (' | stopped at | verdict' if c['loss_rules'] else '') + ' |',
          '|---' * (11 + (2 if c['loss_rules'] else 0)) + '|']
    for r in results:
        L.append(f'| {r["task"] + 1} | {r["seq"]} | {r["track"]} | {r["start"]} | {r["frames"]} | {r["median_side"]} | {r["model"]} | {r["auc"]} | {r["iou"]} | '
                 f'{r["lost_at"] if r["lost_at"] is not None else "-"} | {r["ms"]}' +
                 (f' | {r["stopped_at"] if r["stopped_at"] is not None else "-"} ({r["stop_reason"] or ""}) | {r["loss_verdict"]}' if c['loss_rules'] else '') + ' |')
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
