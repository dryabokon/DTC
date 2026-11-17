"""Public datasets -> the video store videos/ (utils_sources), and moving the older layouts into it. Local, not in git.

    datasets/_downloads/...                       the downloaded archives (as published)
    videos/<set>/<sequence>/<sequence>.mp4        + <sequence>.gt.txt (MOT, many objects) or <sequence>.gt.jsonl (one object)
                                                  -> listed everywhere as <set>/<sequence> (Live, Label, Benchmark, --source)

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
VIDEOS = os.path.join(ROOT, 'videos')
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
def write_one(video, boxes, src=None):
    """One-object ground truth next to the video: <stem>.gt.jsonl, a line per frame with the object (0-based frames)."""
    with open(os.path.splitext(video)[0] + '.gt.jsonl', 'w', encoding='utf-8') as f:
        for i, b in enumerate(boxes):
            if b is not None:
                f.write(json.dumps({'frame': i, 'box': [round(float(v), 1) for v in b], **({'src': src} if src else {})}) + '\n')
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
            open(os.path.join(out, seq + '.gt.txt'), 'wb').write(z.read(gt))     # the benchmark picks the small tracks itself
            rows = [l.split(',') for l in z.read(gt).decode().splitlines() if l.strip()]
            msg += f', gt {len(mot_tracks(rows))} tracks'
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
        open(os.path.join(out, scene + '.gt.txt'), 'w').write('\n'.join(rows) + '\n')
        msg = f'visdrone/{scene}: {n} frames {W}x{H}, {len(rows)} boxes, {len(tr)} pedestrian/people/car tracks'
        log('   ' + msg)
# ----------------------------------------------------------------------------------------------------------------------
def prepare_antiuav(n_clips=6, log=print):
    """Anti-UAV300 (RGB part): the n sequences with the smallest drones -> videos/antiuav/<seq>/ (one object each)."""
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
        write_one(mp4, boxes)
        log(f'   antiuav/{seq}: {len(boxes)} frames {W}x{H} @ {fps} fps, median drone box {area:.0f} px^2 ({np.sqrt(area):.0f} px side)')
# ----------------------------------------------------------------------------------------------------------------------
IMG_EXT = ('.jpg', '.jpeg', '.png', '.bmp')
def frames_folder_to_source(folder, name=None, fps=25, keep_frames=False, log=print):
    """Folder of numbered frames + Anti-UAV label (IR_label.json / *.json: {exist: [...], gt_rect: [[x,y,w,h], ...]})
    -> <folder>/<name>.mp4 + <name>.gt.jsonl (one object, frames from 0, absent target skipped). Copy the folder into
    videos/ (or convert it there) and it is a source with ground truth.
    The frames are deleted afterwards unless keep_frames (the json is kept as the original label)."""
    folder = os.path.normpath(folder)
    frames = sorted(f for f in os.listdir(folder) if f.lower().endswith(IMG_EXT))
    if not frames:
        raise ValueError(f'no image frames in {folder}')
    labels = sorted(f for f in os.listdir(folder) if f.lower().endswith('.json'))
    if not name:                                  # IR_label.json -> IR.mp4
        name = os.path.splitext(labels[0])[0].replace('_label', '') if labels else os.path.basename(folder)
    mp4 = os.path.join(folder, name + '.mp4')
    n = encode((open(os.path.join(folder, f), 'rb').read() for f in frames), mp4, fps)
    if n != len(frames):
        raise RuntimeError(f'encoded {n} of {len(frames)} frames')
    rows = 0
    if labels:
        a = json.load(open(os.path.join(folder, labels[0])))
        rects, exist = a.get('gt_rect') or [], a.get('exist') or []
        if len(rects) != len(frames):
            log(f'   warning: {len(rects)} labels for {len(frames)} frames')
        boxes = [[r[0], r[1], r[0] + r[2], r[1] + r[3]] if (i >= len(exist) or exist[i]) and r and len(r) == 4 and r[2] > 0 and r[3] > 0 else None
                 for i, r in enumerate(rects)]
        write_one(mp4, boxes)
        rows = sum(1 for b in boxes if b is not None)
    if not keep_frames:
        for f in frames:
            os.remove(os.path.join(folder, f))
    log(f'   {mp4}: {n} frames @ {fps} fps' + (f', {name}.gt.jsonl {rows} boxes' if labels else ', no label json - no ground truth'))
    return mp4
# ----------------------------------------------------------------------------------------------------------------------
# The older layouts -> videos/ (dtc data migrate). COPIES (the old folders stay until removed by hand / --cleanup), so it
# can run again: a file already there with the same size is skipped.
#   images/vNN/<x>.mp4 (+ gt.txt)                  -> videos/<set>/<x>/<x>.mp4 (+ <x>.gt.txt, or <x>.gt.jsonl for one object)
#   datasets/videos/<set>/<seq>/<seq>.mp4 + gt.txt -> videos/<set>/<seq>/<seq>.mp4 + <seq>.gt.txt | .gt.jsonl
#   datasets/videos/uploads/<n>/<n>.<ext>          -> videos/uploads/<n>.<ext>
#   clips/<n>/ (any video, gt.jsonl, label.json)   -> videos/<n>/ (sim_*: videos/sim/<n>/), files renamed to <stem>.gt.jsonl ...
#     a Label project (label.json with 'source') whose source moved too: its labels go next to the moved source
# ----------------------------------------------------------------------------------------------------------------------
IMAGES_SETS = {'v00': 'mot15', 'v01': 'oxford', 'v10': 'boats', 'v20': 'visdrone', 'v21': 'visdrone', 'v22': 'visdrone', 'v30': 'antiuav'}
FLAT_SETS = {'boats'}                                   # several videos without ground truth: one folder for all
VIDEO_EXT = ('.mp4', '.avi', '.mkv', '.mov')
# ----------------------------------------------------------------------------------------------------------------------
def _mot_ids(gt):
    ids = set()
    for line in open(gt, encoding='utf-8', errors='replace'):
        p = line.strip().split(',')
        if len(p) >= 6:
            ids.add(p[1].strip())
    return ids
# ----------------------------------------------------------------------------------------------------------------------
def mot_to_one(gt_txt, out_jsonl):
    """MOT rows of ONE object (frames 1-based, x, y, w, h) -> one-object lines {"frame": 0-based, "box": [x1, y1, x2, y2]}."""
    rows = {}
    for line in open(gt_txt, encoding='utf-8', errors='replace'):
        p = line.strip().split(',')
        if len(p) >= 6:
            f, x, y, w, h = int(float(p[0])), *[float(v) for v in p[2:6]]
            if w > 0 and h > 0:
                rows[f - 1] = [round(x, 1), round(y, 1), round(x + w, 1), round(y + h, 1)]
    with open(out_jsonl, 'w', encoding='utf-8') as f:
        for i in sorted(rows):
            f.write(json.dumps({'frame': i, 'box': rows[i]}) + '\n')
    return len(rows)
# ----------------------------------------------------------------------------------------------------------------------
def _gt_step(gt, video_dst):
    """A MOT gt.txt next to a video -> the step that puts it beside video_dst (one object: converted to .gt.jsonl)."""
    stem = os.path.splitext(video_dst)[0]
    if len(_mot_ids(gt)) == 1:
        return ('convert', gt, stem + '.gt.jsonl')
    return ('copy', gt, stem + '.gt.txt')
# ----------------------------------------------------------------------------------------------------------------------
def migration_plan(root=ROOT):
    """[(action, src, dst)] - action: copy | convert (one-object MOT -> .gt.jsonl) | labels (a Label project's gt.jsonl)."""
    steps, moved = [], {}                     # moved: old video path -> new one (for Label projects that point at it)
    store = os.path.join(root, 'videos')
    images = os.path.join(root, 'images')
    for vdir in sorted(os.listdir(images)) if os.path.isdir(images) else []:
        d = os.path.join(images, vdir)
        if not os.path.isdir(d):
            continue
        vids = sorted(f for f in os.listdir(d) if f.lower().endswith(VIDEO_EXT))
        for f in vids:
            stem = os.path.splitext(f)[0]
            group = IMAGES_SETS.get(vdir, vdir)
            dst = os.path.join(store, group, f) if group in FLAT_SETS else os.path.join(store, group, stem, f)
            steps.append(('copy', os.path.join(d, f), dst)); moved[os.path.join(d, f)] = dst
            if len(vids) == 1 and os.path.isfile(os.path.join(d, 'gt.txt')):
                steps.append(_gt_step(os.path.join(d, 'gt.txt'), dst))
    old = os.path.join(root, 'datasets', 'videos')
    for s in sorted(os.listdir(old)) if os.path.isdir(old) else []:
        for q in sorted(os.listdir(os.path.join(old, s))):
            d = os.path.join(old, s, q)
            if not os.path.isdir(d):
                continue
            vids = sorted(f for f in os.listdir(d) if f.lower().endswith(VIDEO_EXT))
            for f in vids:
                dst = os.path.join(store, 'uploads', f) if s == 'uploads' else os.path.join(store, s, q, f)
                steps.append(('copy', os.path.join(d, f), dst)); moved[os.path.join(d, f)] = dst
                if len(vids) == 1 and os.path.isfile(os.path.join(d, 'gt.txt')):
                    steps.append(_gt_step(os.path.join(d, 'gt.txt'), dst))
    clips = os.path.join(root, 'clips')
    for n in sorted(os.listdir(clips)) if os.path.isdir(clips) else []:
        d = os.path.join(clips, n)
        if not os.path.isdir(d):
            continue
        vids = sorted(f for f in os.listdir(d) if f.lower().endswith(VIDEO_EXT))
        meta = {}
        if os.path.isfile(os.path.join(d, 'label.json')):
            try:
                meta = json.load(open(os.path.join(d, 'label.json'), encoding='utf-8'))
            except ValueError:
                pass
        src = os.path.normcase(os.path.abspath(meta['source'])) if meta.get('source') else None
        target = next((v for k, v in moved.items() if os.path.normcase(os.path.abspath(k)) == src), None) if src else None
        if target and os.path.isfile(os.path.join(d, 'gt.jsonl')):
            stem = os.path.splitext(target)[0]
            steps.append(('labels', os.path.join(d, 'gt.jsonl'), stem + '.gt.jsonl'))
            steps.append(('copy', os.path.join(d, 'label.json'), stem + '.label.json'))
            continue
        base = os.path.join(store, 'sim' if n.startswith('sim_') else '', n)
        for f in vids:
            stem = n if f == 'video.mp4' else os.path.splitext(f)[0]
            dst = os.path.join(base, stem + os.path.splitext(f)[1])
            steps.append(('copy', os.path.join(d, f), dst))
            if len(vids) == 1:
                for old_name, suffix in (('gt.jsonl', '.gt.jsonl'), ('gt.txt', '.gt.txt'), ('label.json', '.label.json'), ('meta.json', '.meta.json')):
                    if os.path.isfile(os.path.join(d, old_name)):
                        steps.append(('copy', os.path.join(d, old_name), os.path.join(base, stem + suffix)))
    return steps
# ----------------------------------------------------------------------------------------------------------------------
def migrate(apply=False, root=ROOT, log=print):
    """Prints the plan; with apply copies / converts. Returns the number of steps that did (or would do) something."""
    import shutil
    todo = 0
    for action, src, dst in migration_plan(root):
        same = os.path.isfile(dst) and (action != 'copy' or os.path.getsize(dst) == os.path.getsize(src))
        rel = lambda p: os.path.relpath(p, root).replace('\\', '/')
        log(f"   {'done ' if same else action:8s} {rel(src)}  ->  {rel(dst)}")
        if same:
            continue
        todo += 1
        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if action == 'convert':
                mot_to_one(src, dst)
            else:
                shutil.copy2(src, dst + '.tmp')
                os.replace(dst + '.tmp', dst)
    return todo
# ----------------------------------------------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------------------------------------------
# The store on the Pi: videos/ is not in git, so the harness copies it (what is missing or has another size).
# ----------------------------------------------------------------------------------------------------------------------
FOR_TESTS = ('mot15/PETS09-S2L1/', 'oxford/', 'boats/', 'antiuav/', 'antiuav_val/', 'visdrone/', 'sim/')   # tests, quality, benchmark presets
# ----------------------------------------------------------------------------------------------------------------------
def store_files(prefixes=None, root=VIDEOS):
    """{relative posix path: size} of the store's files (not .trash, _downloads), those under the prefixes if given."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(('.', '_'))]
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), root).replace('\\', '/')
            if '.tmp' not in f and (not prefixes or rel.startswith(tuple(prefixes))):
                out[rel] = os.path.getsize(os.path.join(d, f))
    return out
# ----------------------------------------------------------------------------------------------------------------------
def push(pi, prefixes=None, log=print):
    """Copy the store (or the files under prefixes) to the Pi's videos/. Returns (copied, failed)."""
    remote = f'{pi.repo}/videos'
    rc, out, _ = pi.sh(f"mkdir -p {remote} && cd {remote} && find . -type f -not -path './.*' -printf '%s %P\\n'", timeout=60)
    have = {}
    for line in out.splitlines():
        size, _, rel = line.partition(' ')
        if size.isdigit():
            have[rel] = int(size)
    copied, failed = 0, []
    for rel, size in sorted(store_files(prefixes).items()):
        if have.get(rel) == size:
            continue
        folder = os.path.dirname(rel)
        if folder:
            pi.sh(f"mkdir -p '{remote}/{folder}'")
        log(f'   -> Pi videos/{rel} ({size / 1e6:.1f} MB)')
        if pi.push(os.path.join(VIDEOS, *rel.split('/')), f'{remote}/{rel}'):
            copied += 1
        else:
            failed.append(rel)
    return copied, failed
# ----------------------------------------------------------------------------------------------------------------------
