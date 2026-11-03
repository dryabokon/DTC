"""Public datasets -> DTC sources and reference clips. Everything stays local (datasets/ and clips/ are git-ignored).

    datasets/_downloads/...                       the downloaded archives (as published)
    datasets/videos/<set>/<sequence>/<sequence>.mp4 (+ gt.txt in MOT format when the set has ground truth)
                                                  -> listed as DTC sources (UI dropdown, --source <set>/<sequence>)
    clips/<set>_<sequence>[_t<id>]/video.mp4 + gt.jsonl + meta.json
                                                  -> single-target reference clips for `dtc test quality`

Video is encoded straight from the archives (no full extraction: the disk is nearly full).
Licenses: MOT15 CC BY-NC-SA 3.0 (non-commercial); VisDrone (Voxel51 mirror of the val split) CC BY-SA 3.0 per its card,
academic use per the authors; Anti-UAV project MIT. Not for redistribution as our own material.
"""
import configparser
import io
import json
import os
import subprocess
import zipfile

import numpy as np
# ----------------------------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DL = os.path.join(ROOT, 'datasets', '_downloads')
VIDEOS = os.path.join(ROOT, 'datasets', 'videos')
CLIPS = os.path.join(ROOT, 'clips')
# ----------------------------------------------------------------------------------------------------------------------
def encode(frames_jpg, out_mp4, fps, max_width=1920):
    """JPEG bytes (iterable, in order) -> H.264 mp4 (every frame kept, keyframe each second)."""
    os.makedirs(os.path.dirname(out_mp4), exist_ok=True)
    vf = f"scale='min({max_width},iw)':-2"
    p = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', str(fps), '-c:v', 'mjpeg', '-i', '-',
                          '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-g', str(int(round(fps))), '-pix_fmt', 'yuv420p', out_mp4],
                         stdin=subprocess.PIPE)
    n = 0
    for b in frames_jpg:
        p.stdin.write(b); n += 1
    p.stdin.close(); p.wait()
    return n
# ----------------------------------------------------------------------------------------------------------------------
def write_clip(name, video_src, boxes, fps, size, source, note=''):
    """Reference clip: copy of a sequence video + the box of ONE target per frame (None when absent)."""
    out = os.path.join(CLIPS, name)
    os.makedirs(out, exist_ok=True)
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', video_src, '-c', 'copy', os.path.join(out, 'video.mp4')], check=True)
    with open(os.path.join(out, 'gt.jsonl'), 'w') as f:
        for i, b in enumerate(boxes):
            f.write(json.dumps({'frame': i, 't': round(i / fps, 3), 'box': None if b is None else [round(float(v), 1) for v in b]}) + '\n')
    json.dump({'frames': len(boxes), 'fps': fps, 'size': size, 'maneuver': 'real', 'source': source, 'note': note},
              open(os.path.join(out, 'meta.json'), 'w'), indent=2)
    return out
# ----------------------------------------------------------------------------------------------------------------------
def pick_small_track(tracks, n_frames, min_len_frac=0.5):
    """Track id with the smallest median box among those visible in >= min_len_frac of the frames."""
    best = None
    for tid, fb in tracks.items():
        if len(fb) < min_len_frac * n_frames:
            continue
        area = float(np.median([(b[2] - b[0]) * (b[3] - b[1]) for b in fb.values()]))
        if best is None or area < best[1]:
            best = (tid, area)
    return best
# ----------------------------------------------------------------------------------------------------------------------
def mot_tracks(gt_rows):
    """MOT gt rows (frame,id,x,y,w,h,conf,...) -> {id: {frame: [x1,y1,x2,y2]}} (conf==0 rows are ignored)."""
    tr = {}
    for r in gt_rows:
        f, tid, x, y, w, h = int(r[0]), int(r[1]), *map(float, r[2:6])
        if len(r) > 6 and float(r[6]) == 0:
            continue
        tr.setdefault(tid, {})[f] = [x, y, x + w, y + h]
    return tr
# ----------------------------------------------------------------------------------------------------------------------
def prepare_mot15(seqs=('PETS09-S2L2', 'Venice-2', 'ETH-Sunnyday', 'ADL-Rundle-6'), log=print):
    z = zipfile.ZipFile(os.path.join(DL, 'mot15', 'MOT15.zip'))
    names = z.namelist()
    for seq in seqs:
        base = next(b for b in (f'MOT15/train/{seq}/', f'MOT15/test/{seq}/') if b + 'seqinfo.ini' in names)
        ini = configparser.ConfigParser(); ini.read_string(z.read(base + 'seqinfo.ini').decode())
        fps = float(ini['Sequence'].get('frameRate', 25)); W, H = int(ini['Sequence']['imWidth']), int(ini['Sequence']['imHeight'])
        imgs = sorted(n for n in names if n.startswith(base + 'img1/') and n.endswith('.jpg'))
        out = os.path.join(VIDEOS, 'mot15', seq)
        mp4 = os.path.join(out, seq + '.mp4')
        n = encode((z.read(n) for n in imgs), mp4, fps)
        gt = base + 'gt/gt.txt'
        msg = f'mot15/{seq}: {n} frames {W}x{H} @ {fps:g} fps'
        if gt in names:
            open(os.path.join(out, 'gt.txt'), 'wb').write(z.read(gt))
            rows = [l.split(',') for l in z.read(gt).decode().splitlines() if l.strip()]
            tr = mot_tracks(rows)
            pick = pick_small_track(tr, n)
            if pick:
                tid = pick[0]
                boxes = [tr[tid].get(f) for f in range(1, n + 1)]
                if W > 1920:
                    k = 1920 / W; boxes = [None if b is None else [v * k for v in b] for b in boxes]
                write_clip(f'mot15_{seq}_t{tid}', mp4, boxes, fps, [min(W, 1920), int(H * min(1, 1920 / W))], f'MOT15 {seq} track {tid}',
                           f'smallest long track, median area {pick[1]:.0f} px^2')
                msg += f', gt {len(tr)} tracks, clip track {tid} (median {pick[1]:.0f} px^2)'
        else:
            msg += ', no public ground truth (test sequence)'
        log('   ' + msg)
# ----------------------------------------------------------------------------------------------------------------------
VISDRONE_CLASSES = {1: 'pedestrian', 2: 'people', 3: 'bicycle', 4: 'car', 5: 'van', 6: 'truck', 7: 'tricycle', 8: 'awning-tricycle', 9: 'bus', 10: 'motor'}
def prepare_visdrone(fps=25, log=print):
    root = os.path.join(DL, 'visdrone_hf')
    S = json.load(open(os.path.join(root, 'samples.json')))['samples']
    cls_id = {v: k for k, v in VISDRONE_CLASSES.items()}
    scenes = {}
    for s in S:
        scenes.setdefault(s['scene_id'], []).append(s)
    for scene, ss in sorted(scenes.items()):
        ss.sort(key=lambda s: s['frame_number'])
        W0, H0 = ss[0]['metadata']['width'], ss[0]['metadata']['height']
        k = min(1.0, 1920.0 / W0)                     # encode() caps the width at 1920: boxes must follow
        W, H = int(round(W0 * k)), int(round(H0 * k / 2) * 2)
        out = os.path.join(VIDEOS, 'visdrone', scene)
        mp4 = os.path.join(out, scene + '.mp4')
        n = encode((open(os.path.join(root, s['filepath']), 'rb').read() for s in ss), mp4, fps)
        rows, tr = [], {}
        for i, s in enumerate(ss, start=1):
            for d in s.get('detections') or []:
                x, y, w, h = d['bounding_box']; x, y, w, h = x * W, y * H, w * W, h * H     # relative -> pixels of the encoded video
                tid = int(d.get('index') or 0)
                rows.append(f'{i},{tid},{x:.1f},{y:.1f},{w:.1f},{h:.1f},1,{cls_id.get(d["label"], -1)},{d.get("visibility", 1)}')
                if d['label'] in ('pedestrian', 'people', 'car'):
                    tr.setdefault(tid, {})[i] = [x, y, x + w, y + h]
        open(os.path.join(out, 'gt.txt'), 'w').write('\n'.join(rows) + '\n')
        pick = pick_small_track(tr, n)
        msg = f'visdrone/{scene}: {n} frames {W}x{H}, {len(rows)} boxes'
        if pick:
            tid = pick[0]
            write_clip(f'visdrone_{scene}_t{tid}', mp4, [tr[tid].get(f) for f in range(1, n + 1)], fps, [W, H],
                       f'VisDrone2019-MOT val {scene} track {tid}', f'smallest long pedestrian/people/car track, median area {pick[1]:.0f} px^2')
            msg += f', clip track {tid} (median {pick[1]:.0f} px^2)'
        log('   ' + msg)
# ----------------------------------------------------------------------------------------------------------------------
def prepare_antiuav(n_clips=6, log=print):
    """Anti-UAV300 (RGB part): the n sequences with the smallest drones -> clips + sources."""
    z = zipfile.ZipFile(os.path.join(DL, 'antiuav', 'Anti-UAV-RGBT.zip'))
    names = z.namelist()
    jsons = sorted(n for n in names if n.lower().endswith('visible.json'))
    cand = []
    for j in jsons:
        a = json.loads(z.read(j))
        rects = a.get('gt_rect') or []
        exist = a.get('exist') or [1 if r else 0 for r in rects]
        areas = [r[2] * r[3] for r, e in zip(rects, exist) if e and r and len(r) == 4]
        if areas:
            cand.append((float(np.median(areas)), j, a))
    cand.sort()
    for area, j, a in cand[:n_clips]:
        seq_dir = os.path.dirname(j)
        seq = '_'.join(seq_dir.split('/')[-2:])                  # split_sequence
        video = next(n for n in names if n.startswith(seq_dir + '/') and n.lower().endswith('visible.mp4'))
        out = os.path.join(VIDEOS, 'antiuav', seq)
        os.makedirs(out, exist_ok=True)
        mp4 = os.path.join(out, seq + '.mp4')
        open(mp4, 'wb').write(z.read(video))
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate',
                                '-of', 'csv=p=0', mp4], capture_output=True, text=True).stdout.strip().split(',')
        W, H = int(probe[0]), int(probe[1]); num, den = probe[2].split('/'); fps = round(float(num) / float(den), 2)
        rects, exist = a.get('gt_rect') or [], a.get('exist') or []
        boxes = [[r[0], r[1], r[0] + r[2], r[1] + r[3]] if (i >= len(exist) or exist[i]) and r and len(r) == 4 else None for i, r in enumerate(rects)]
        with open(os.path.join(out, 'gt.txt'), 'w') as f:
            for i, b in enumerate(boxes, start=1):
                if b is not None:
                    f.write(f'{i},1,{b[0]:.1f},{b[1]:.1f},{b[2] - b[0]:.1f},{b[3] - b[1]:.1f},1,-1,-1,-1\n')
        write_clip(f'antiuav_{seq}', mp4, boxes, fps, [W, H], f'Anti-UAV300 {seq} (visible)', f'median drone box {area:.0f} px^2')
        log(f'   antiuav/{seq}: {len(boxes)} frames {W}x{H} @ {fps} fps, median drone box {area:.0f} px^2 ({np.sqrt(area):.0f} px side)')
