"""Does click-to-track notice when it lost the target? PETS09-S2L1 (campus) with ground truth.

For every person (2 starts: first fully visible frame and the middle of the track) SOT starts on the true box.
Truth comes from the raw NanoTrack trajectory: the target counts as lost from the first of 5 frames in a row where
the box overlaps the true box by IoU < 0.2 or the person is no longer annotated (left the scene).
    caught        SOT stopped itself no earlier than 10 frames before the truth loss
    false_cancel  SOT stopped earlier (it was still on the person)
    missed        the target was lost and SOT kept going to the end
Usage (on the Pi):  python tests/quality/sot_pets.py <out.json>
"""
import json
import os
import sys

import cv2
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(REPO, 'src'))
import utils_tracker_sot                                             # noqa: E402
MODELS = os.path.join(REPO, 'models', 'nanotrack') + '/'
# ----------------------------------------------------------------------------------------------------------------------
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0
# ----------------------------------------------------------------------------------------------------------------------
def raw_nano():
    p = cv2.TrackerNano_Params(); p.backbone = MODELS + 'nanotrack_backbone_sim.onnx'; p.neckhead = MODELS + 'nanotrack_head_sim.onnx'
    return cv2.TrackerNano_create(p)
# ----------------------------------------------------------------------------------------------------------------------
def main(out):
    gt = pd.read_csv(os.path.join(REPO, 'images', 'v00', 'gt.txt'), header=None).iloc[:, :6]
    gt.columns = ['f', 'id', 'x', 'y', 'w', 'h']
    cap = cv2.VideoCapture(os.path.join(REPO, 'images', 'v00', 'PETS09-S2L1.mp4')); frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    H, W = frames[0].shape[:2]
    runs = []
    for tid, d in gt.groupby('id'):
        d = d.set_index('f')
        okf = [f for f, r in d.iterrows() if r.x > 2 and r.y > 2 and r.x + r.w < W - 2 and r.y + r.h < H - 2 and r.w > 15]
        if not okf:
            continue
        for start in sorted({okf[0], okf[len(okf) // 2]}):
            r = d.loc[start]; box = [r.x, r.y, r.x + r.w, r.y + r.h]
            T = utils_tracker_sot.Tracker_SOT(models_dir=MODELS); T.start(frames[start - 1], box)
            R = raw_nano(); R.init(frames[start - 1], (int(r.x), int(r.y), int(r.w), int(r.h)))
            bad, t_lost, t_stop = 0, None, None
            for f in range(start + 1, min(len(frames), d.index.max() + 40) + 1):
                ok, (x, y, w, h) = R.update(frames[f - 1])
                g = d.loc[f] if f in d.index else None
                hit = g is not None and iou([x, y, x + w, y + h], [g.x, g.y, g.x + g.w, g.y + g.h]) >= 0.2
                bad = 0 if hit else bad + 1
                if t_lost is None and bad >= 5:
                    t_lost = f - start - 4
                if t_stop is None:
                    T.update(frames[f - 1])
                    if not T.is_active:
                        t_stop = f - start
                if t_lost is not None and t_stop is not None:
                    break
            kind = ('false_cancel' if t_stop is not None and (t_lost is None or t_stop < t_lost - 10) else
                    'missed' if t_stop is None and t_lost is not None else
                    'caught' if t_stop is not None else 'no_loss')
            runs.append({'id': int(tid), 'start': int(start), 'kind': kind, 'reason': T.stop_reason, 't_lost': t_lost, 't_stop': t_stop})
            print(json.dumps(runs[-1]), flush=True)
    k = pd.Series([r['kind'] for r in runs]).value_counts().to_dict()
    lat = [r['t_stop'] - r['t_lost'] for r in runs if r['kind'] == 'caught']
    lost = k.get('caught', 0) + k.get('missed', 0)
    res = {'runs': len(runs), 'losses': lost, 'caught': k.get('caught', 0), 'missed': k.get('missed', 0),
           'false_cancel': k.get('false_cancel', 0), 'caught_pct': round(100.0 * k.get('caught', 0) / max(1, lost), 1),
           'latency_frames_median': float(np.median(lat)) if lat else None,
           'reasons': pd.Series([r['reason'] for r in runs if r['kind'] == 'caught']).value_counts().to_dict(), 'detail': runs}
    json.dump(res, open(out, 'w'), indent=2)
    print('summary', json.dumps({k_: v for k_, v in res.items() if k_ != 'detail'}))
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    main(sys.argv[1])
