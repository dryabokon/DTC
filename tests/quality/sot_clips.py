"""Click-to-track quality on one-object videos of the store (videos/<group>/... + <stem>.gt.jsonl: sim recordings of
harness/sim_fly2.py, Anti-UAV sequences; frames without a line have no object).

An "operator" clicks the target (box 32x24 around the true centre) whenever SOT is idle and the target has been visible
for 3 frames - as a user re-clicks after a loss. Per frame, with SOT active:
    on target      box centre within 20 px of the true centre
    drift (vis)    target visible but the box is elsewhere
    drift (gone)   target out of view but SOT still claims it  <- the silent failure users see
Usage (on the Pi):  python tests/quality/sot_clips.py <out.json> [group ...]   (default groups: GROUPS)
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', '..', 'src')
sys.path.insert(0, SRC)
import utils_sources                                                # noqa: E402
import utils_tracker_sot                                             # noqa: E402
GROUPS = ('sim', 'antiuav', 'antiuav_val')
# ----------------------------------------------------------------------------------------------------------------------
def evaluate(video, gt_path, models_dir):
    boxes = {}
    for line in open(gt_path, encoding='utf-8'):
        r = json.loads(line)
        boxes[int(r['frame'])] = r.get('box')
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) or (max(boxes) + 1 if boxes else 0)
    gt = [{'box': boxes.get(i)} for i in range(n)]
    T = utils_tracker_sot.Tracker_SOT(models_dir=models_dir)
    on = drift_vis = drift_gone = starts = vis_run = visible = 0
    stops, ms = {}, []
    for g in gt:
        ok, f = cap.read()
        if not ok:
            break
        b = g['box']
        c = None if b is None else ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        visible += c is not None
        vis_run = vis_run + 1 if c is not None else 0
        if not T.is_active:
            if vis_run >= 3:
                T.start(f, [c[0] - 16, c[1] - 12, c[0] + 16, c[1] + 12]); starts += 1
            continue
        T.update(f); ms.append(T.last_ms)
        if not T.is_active:
            stops[T.stop_reason] = stops.get(T.stop_reason, 0) + 1
            continue
        bx = T.box; tc = ((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)
        if c is None:
            drift_gone += 1
        elif np.hypot(tc[0] - c[0], tc[1] - c[1]) <= 20:
            on += 1
        else:
            drift_vis += 1
    return {'frames': len(gt), 'visible': visible, 'on_target': on, 'on_target_pct': round(100.0 * on / max(1, visible), 1),
            'drift_visible': drift_vis, 'drift_gone': drift_gone, 'starts': starts, 'stops': stops,
            'update_ms_median': round(float(np.median(ms)), 1) if ms else None}
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    out, groups = sys.argv[1], tuple(sys.argv[2:]) or GROUPS
    models = os.path.join(HERE, '..', '..', 'models', 'nanotrack') + '/'
    res = {}
    for v in utils_sources.list_videos():
        one = next((p for p, k in v['gt'] if k == 'one'), None)
        if one and v['name'].split('/')[0] in groups:
            res[v['name']] = evaluate(v['path'], one, models)
            print(v['name'], json.dumps(res[v['name']]), flush=True)
    json.dump(res, open(out, 'w'), indent=2)
