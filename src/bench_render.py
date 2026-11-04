"""Benchmark videos: per video of a run and per model, two H.264 clips (played by the browser in the Runs tab):
    <video>__<model>__recall.mp4      the true boxes of the benchmarked objects: hit (caught) / miss
    <video>__<model>__precision.mp4   the tracker's boxes: hit (on the target) / false positive
Frames come from frames.csv (boxes in the benchmark's frame size); the whole video is rendered so it lines up with the Gantt.
Usage: python bench_render.py <run folder>   (bench_worker.py calls render() at the end of a run)
"""
import os
import re
import subprocess
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import utils_bench as B                                             # noqa: E402
# ----------------------------------------------------------------------------------------------------------------------
MAX_W = 960                                         # clip width (smaller = faster to encode and to load)
BGR = {'hit_r': (216, 200, 48), 'miss': (58, 69, 255), 'hit_p': (88, 209, 48), 'fp': (10, 159, 255)}   # #30c8d8 #ff453a #30d158 #ff9f0a
# ----------------------------------------------------------------------------------------------------------------------
def clip_name(seq, model, kind):
    return f'{re.sub(r"[^A-Za-z0-9_.-]+", "_", seq)}__{model}__{kind}.mp4'
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
        cv2.putText(img, str(track), (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
# ----------------------------------------------------------------------------------------------------------------------
def render(run_dir, progress=None):
    """Writes the clips into <run>/video/; returns {seq: {model: {'recall': file, 'precision': file}}}. progress(frames_done)."""
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
        s_out = min(1.0, MAX_W / (W * k))
        w, h = int(W * k * s_out) // 2 * 2, int(H * k * s_out) // 2 * 2
        enc = {(m, kind): Encoder(os.path.join(out_dir, clip_name(seq, m, kind)), w, h, fps) for m in per_model for kind in ('recall', 'precision')}
        f = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            base = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            for m, frames in per_model.items():
                for kind in ('recall', 'precision'):
                    img = base.copy()
                    draw(img, frames.get(f, []), kind, w / (W * k))
                    cv2.putText(img, f'{kind} · {m} · {f}'.replace('·', '-'), (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
                    enc[(m, kind)].write(img)
            f += 1
            if progress:
                progress(1)
        cap.release()
        for e in enc.values():
            e.close()
        made[seq] = {m: {kind: clip_name(seq, m, kind) for kind in ('recall', 'precision')} for m in per_model}
    return made
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    print(render(sys.argv[1]))
