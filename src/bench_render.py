"""Benchmark videos: per video of a run and per model one H.264 clip <video>__<model>.mp4 (played in the Runs tab), two halves:
    left   recall     the true boxes of the benchmarked objects: hit (caught) / miss
    right  precision  the tracker's boxes: hit (on the target) / false positive
Frames come from frames.csv (boxes in the benchmark's frame size); the whole video is rendered so it lines up with the Gantt.
Usage: python bench_render.py <run folder>   (re-makes the clips of a run; bench_worker.py calls render() at the end of a run)
"""
import os
import re
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import utils_bench as B                                             # noqa: E402
# ----------------------------------------------------------------------------------------------------------------------
HALF_W = 720                                        # width of each half of a clip (smaller = faster to encode and to load)
BGR = {'hit_r': (216, 200, 48), 'miss': (58, 69, 255), 'hit_p': (88, 209, 48), 'fp': (10, 159, 255)}   # #30c8d8 #ff453a #30d158 #ff9f0a
# ----------------------------------------------------------------------------------------------------------------------
def clip_name(seq, model):
    return f'{re.sub(r"[^A-Za-z0-9_.-]+", "_", seq)}__{model}.mp4'
# ----------------------------------------------------------------------------------------------------------------------
class Encoder:
    def __init__(self, path, w, h, fps):
        self.p = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{w}x{h}', '-r', f'{fps:.3f}',
                                   '-i', '-', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '27', '-pix_fmt', 'yuv420p',
                                   '-movflags', '+faststart', path], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)

    def write(self, img):
        self.p.stdin.write(img.tobytes())

    def close(self):
        self.p.stdin.close(); self.p.wait()
# ----------------------------------------------------------------------------------------------------------------------
def load_boxes(run_dir, tasks):
    """{seq: {model: {abs_frame: [(track, gt or None, box or None, hit)]}}} from frames.csv (benchmark frame pixels)."""
    out = {}
    with open(os.path.join(run_dir, 'frames.csv')) as f:
        head = next(f).strip().split(',')
        if 'bx1' not in head:
            return None                                 # made before boxes were recorded
        for line in f:
            p = line.rstrip('\n').split(',')
            t, m, rel = int(p[0]), p[1], int(p[2])
            task = tasks[t]
            num = lambda vs: [float(v) for v in vs] if vs[0] != '' else None
            gt, box = num(p[11:15]), num(p[7:11])
            out.setdefault(task['seq'], {}).setdefault(m, {}).setdefault(task['start'] + rel, []).append((task['track'], gt, box, p[5] == '1'))
    return out
# ----------------------------------------------------------------------------------------------------------------------
def draw(img, boxes, kind, s):
    for track, gt, box, hit in boxes:
        b = gt if kind == 'recall' else box
        if b is None:
            continue
        col = (BGR['hit_r'] if hit else BGR['miss']) if kind == 'recall' else (BGR['hit_p'] if hit else BGR['fp'])
        x1, y1, x2, y2 = [int(round(v * s)) for v in b]
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
        label(img, str(track), x1 - 1, y1, col)
# ----------------------------------------------------------------------------------------------------------------------
def label(img, text, x, y, bg, scale=0.42):
    """White text on a plate of the box colour, sitting on the box's top-left corner (inside the frame)."""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x = max(0, min(x, img.shape[1] - tw - 6))
    y0 = y - th - base - 3 if y - th - base - 3 >= 0 else y
    cv2.rectangle(img, (x, y0), (x + tw + 6, y0 + th + base + 3), bg, -1)
    cv2.putText(img, text, (x + 3, y0 + th + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
# ----------------------------------------------------------------------------------------------------------------------
def render(run_dir, progress=None):
    """Writes the clips into <run>/video/; returns {seq: {model: file}}. progress(frames_done)."""
    cfg_all = B.read_json(os.path.join(run_dir, 'config.json'))
    c, tasks = cfg_all['config'], cfg_all['runs']
    data = load_boxes(run_dir, tasks)
    if not data:
        return {}
    seqs = {s['id']: s for s in B.sequences()}
    out_dir = os.path.join(run_dir, 'video')
    os.makedirs(out_dir, exist_ok=True)
    made = {}
    for seq, per_model in data.items():
        cap = cv2.VideoCapture(seqs[seq]['video'])
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        k = min(1.0, c['max_width'] / W)                 # the benchmark's frame size (boxes are in it)
        s_out = min(1.0, HALF_W / (W * k))
        w, h = int(W * k * s_out) // 2 * 2, int(H * k * s_out) // 2 * 2
        gap = 8
        enc = {m: Encoder(os.path.join(out_dir, clip_name(seq, m)), 2 * w + gap, h, fps) for m in per_model}
        f = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            base = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            for m, frames in per_model.items():
                halves = []
                for kind in ('recall', 'precision'):
                    img = base.copy()
                    draw(img, frames.get(f, []), kind, w / (W * k))
                    cv2.putText(img, f'{kind}   {m}   frame {f}', (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                    halves.append(img)
                enc[m].write(cv2.hconcat([halves[0], np.full((h, gap, 3), 20, np.uint8), halves[1]]))
            f += 1
            if progress:
                progress(1)
        cap.release()
        for e in enc.values():
            e.close()
        made[seq] = {m: clip_name(seq, m) for m in per_model}
    return made
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    run = sys.argv[1]
    vdir = os.path.join(run, 'video')
    old = set(os.listdir(vdir)) if os.path.isdir(vdir) else set()
    made = render(run)
    res = B.read_json(os.path.join(run, 'results.json'))
    if res is not None:
        res['videos'] = made
        B.write_json(os.path.join(run, 'results.json'), res)
    for f in old - {n for m in made.values() for n in m.values()}:       # clips of the older layout
        os.remove(os.path.join(vdir, f))
    print(made)
