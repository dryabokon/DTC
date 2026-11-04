"""Click-to-track models side by side (utils_tracker_sot.SOT_MODELS) on sequences with ground truth.

Per model and sequence the raw tracker (no loss rules) starts on the true box and runs for up to --frames frames:
    ms           tracker time per frame (median), i.e. what it costs on this machine (run it on the Pi)
    success      share of frames with IoU >= 0.5 against the truth, iou_mean, center error (median, px)
    score_on     tracker score percentiles while on the target (IoU >= 0.3)
    score_off    the same while off it (IoU < 0.1) - a usable loss threshold separates the two
Then the full Tracker_SOT (loss rules on) runs the same start: frames until it stopped and why.
Sequences: drone_ir (images/v30, one small thermal drone) and PETS09 (images/v00, several people, two starts each).
Usage (on the Pi):  python tests/quality/sot_models.py out.json [--models NANO,VIT,SIAMBAN,SIAMFC] [--frames 300]
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'src'))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import utils_tracker_sot as U                                       # noqa: E402
MODELS_DIR = os.path.join(REPO, 'models')
# ----------------------------------------------------------------------------------------------------------------------
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0
# ----------------------------------------------------------------------------------------------------------------------
def read_frames(path, n):
    cap = cv2.VideoCapture(path); frames = []
    while len(frames) < n:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    return frames
# ----------------------------------------------------------------------------------------------------------------------
def runs(n_frames):
    """[(name, frames, {frame_index: xyxy}, start_index)]"""
    out = []
    gt = pd.read_csv(os.path.join(REPO, 'images', 'v30', 'gt.txt'), header=None).iloc[:, :6]
    frames = read_frames(os.path.join(REPO, 'images', 'v30', 'IR.mp4'), 10 ** 6)
    truth = {int(r[0]) - 1: [r[2], r[3], r[2] + r[4], r[3] + r[5]] for r in gt.values}
    out.append(('drone_ir', frames, truth, 0))

    gt = pd.read_csv(os.path.join(REPO, 'images', 'v00', 'gt.txt'), header=None).iloc[:, :6]
    gt.columns = ['f', 'id', 'x', 'y', 'w', 'h']
    frames = read_frames(os.path.join(REPO, 'images', 'v00', 'PETS09-S2L1.mp4'), 10 ** 6)
    H, W = frames[0].shape[:2]
    for tid, d in gt.groupby('id'):
        ok = d[(d.x > 2) & (d.y > 2) & (d.x + d.w < W - 2) & (d.y + d.h < H - 2) & (d.w > 15)]
        if len(ok) < 100:
            continue
        truth = {int(r.f) - 1: [r.x, r.y, r.x + r.w, r.y + r.h] for r in d.itertuples()}
        for s in sorted({int(ok.f.iloc[0]) - 1, int(ok.f.iloc[len(ok) // 2]) - 1}):
            out.append((f'pets_id{int(tid)}_f{s}', frames, truth, s))
    return out
# ----------------------------------------------------------------------------------------------------------------------
def run_one(model, frames, truth, s, n_frames):
    T = U.Tracker_SOT(model=model, models_dir=MODELS_DIR, app_rel=0)
    b = [int(v) for v in truth[s]]
    T.tracker = T._create()
    t0 = time.perf_counter(); T.tracker.init(frames[s], (b[0], b[1], b[2] - b[0], b[3] - b[1])); init_ms = (time.perf_counter() - t0) * 1000
    ious, errs, ms, on, off = [], [], [], [], []
    for f in range(s + 1, min(len(frames), s + 1 + n_frames)):
        t0 = time.perf_counter(); ok, (x, y, w, h), score = T.tracker.update(frames[f]); ms.append((time.perf_counter() - t0) * 1000)
        g = truth.get(f)
        if g is None:
            off.append(score); continue
        i = iou([x, y, x + w, y + h], g); ious.append(i)
        errs.append(np.hypot(x + w / 2 - (g[0] + g[2]) / 2, y + h / 2 - (g[1] + g[3]) / 2))
        (on if i >= 0.3 else off if i < 0.1 else []).append(score)

    F = U.Tracker_SOT(model=model, models_dir=MODELS_DIR)            # full: loss rules on
    F.start(frames[s], b); stopped = None
    for f in range(s + 1, min(len(frames), s + 1 + n_frames)):
        F.update(frames[f])
        if not F.is_active:
            stopped = f - s; break
    still_on = None if stopped is None or stopped - 1 >= len(ious) else bool(truth.get(s + stopped) is not None and ious[stopped - 1] >= 0.3)
    pct = lambda v: [round(float(np.percentile(v, q)), 3) for q in (5, 25, 50, 75, 95)] if v else None
    return {'frames': len(ms), 'init_ms': round(init_ms, 1), 'ms': round(float(np.median(ms)), 1) if ms else None,
            'success': round(float(np.mean(np.array(ious) >= 0.5)), 3) if ious else None,
            'iou_mean': round(float(np.mean(ious)), 3) if ious else None,
            'center_err_px': round(float(np.median(errs)), 1) if errs else None,
            'score_on': pct(on), 'score_off': pct(off), 'n_off': len(off),
            'full_stopped_at': stopped, 'full_reason': F.stop_reason if stopped else None, 'full_stop_while_on_target': still_on}
# ----------------------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--models', default='NANO,VIT,SIAMBAN,SIAMFC'); ap.add_argument('--frames', type=int, default=300)
    a = ap.parse_args()
    seqs = runs(a.frames)
    res = {'models': {}, 'detail': []}
    for m in a.models.split(','):
        rows = []
        for name, frames, truth, s in seqs:
            r = run_one(m, frames, truth, s, a.frames); r.update(model=m, seq=name); rows.append(r)
            print(json.dumps(r), flush=True)
        res['detail'] += rows
        ir = [r for r in rows if r['seq'] == 'drone_ir'][0]; pets = [r for r in rows if r['seq'] != 'drone_ir']
        res['models'][m] = {'ms': float(np.median([r['ms'] for r in rows])), 'init_ms': float(np.median([r['init_ms'] for r in rows])),
                            'drone_ir_success': ir['success'], 'drone_ir_center_err': ir['center_err_px'], 'drone_ir_stopped': ir['full_stopped_at'],
                            'pets_success': round(float(np.mean([r['success'] for r in pets])), 3),
                            'pets_iou': round(float(np.mean([r['iou_mean'] for r in pets])), 3),
                            'pets_false_stops': sum(1 for r in pets if r['full_stop_while_on_target']), 'pets_runs': len(pets)}
        print('summary', m, json.dumps(res['models'][m]), flush=True)
    json.dump(res, open(a.out, 'w'), indent=2)
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    main()
