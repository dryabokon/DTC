"""Human-assisted labeling of ONE object per frame (the Label tab of the web UI).

The operator draws a box on a frame (a manual key frame); the click-to-track tracker (Tracker_SOT, the app's loss rules)
follows the object forward and writes a box per frame. When it drops the target, or the operator sees it drift, the
operator draws the box again (a new key frame: the tracked frames after it up to the next key frame are cleared) and
lets it run again. Frames where the object is not visible are marked 'absent'.

Back-tracking: a run stops (or the operator notices) well after it left the object, and finding the frame where it went
wrong meant scrolling back frame by frame. So a box drawn on a frame is first tracked BACKWARDS over the tracked run before
it: where the backward boxes agree with the forward ones for AGREE_N frames, the forward run was right from there on down;
the frames above that get the backward boxes. The operator draws where the error is plain to see, the tool finds where it
began.

Why a session of its own and not the Live pipeline: Live is a stream that only goes forward; labeling needs any frame by
number, back and forth, with exact frame indices. So the video is decoded once, sequentially with cv2 (the same way the
benchmark reads it, so frame numbers match), into a JPEG cache runs/labels/<name>/frames/, at most CACHE_WIDTH wide.
Boxes are kept and saved in source-video pixels.

Output lies next to the video in the store videos/ (utils_sources), the benchmark reads it right away (kind 'clip'):
  videos/.../a.mp4         the video itself - never copied, never re-encoded
  videos/.../a.gt.jsonl    one line per labeled frame: {"frame": i (0-based), "box": [x1, y1, x2, y2] | null, "src": ...}
                           src: manual | track | interp | absent; frames never labeled are not written (= no object)
  videos/.../a.label.json  which tracker, counts, when
A video opened from outside the store is copied into videos/uploads/ first, so every label lives in one place.
Existing one-object ground truth of a video (a.gt.jsonl from a dataset, a sim recording) opens as labels and can be edited.
Every change is saved at once (while tracking: every SAVE_EVERY frames and at the stop).
"""
import json
import os
import re
import shutil
import threading
import time

import cv2
import numpy as np

import utils_platform
import utils_sources
import utils_tracker_sot

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CACHE_DIR = os.path.join(REPO, 'runs', 'labels')
UPLOAD_DIR = os.path.join(utils_sources.VIDEOS_DIR, 'uploads')     # browser uploads and videos opened from outside the store
TRASH_DIR = os.path.join(utils_sources.VIDEOS_DIR, '.trash')        # deleted videos with their labels (not scanned, not in git)
SIDECARS = (utils_sources.GT_ONE, utils_sources.GT_MOT, utils_sources.LABEL_META, '.gt.before_delete_all.jsonl')
THUMB_W = 160
CACHE_WIDTH = 1280                  # frames wider than this are cached (and tracked) downscaled, like the benchmark's max_width
JPEG_Q = 92
SAVE_EVERY = 100
MANUAL = ('manual', 'absent')
JUMP_REL = 0.3                      # tracking stops when the box centre lands this many box heights away from where its motion
JUMP2_REL = 0.35                    # was heading (from the last frame / from two frames back - a swap can slide over 2-3 frames):
                                    # on campus (PETS09) every such jump was a swap to another person, correct frames stayed
                                    # <= 0.23 (the app's loss rules do not see swaps: the other person looks similar)
MEET_IOU = 0.3                      # a run that ends on a key frame must arrive near its box, else it drifted on the way
AGREE_N = 3                         # back-tracking stops after this many frames in a row where it agrees with the forward run:
AGREE_REL = 0.2                     # IoU >= 0.5, or the centres within this many box heights (two trackers on a small target
                                    # differ in size more than in place)
ALONE_MAX = 60                      # a backward run that stops (loss, jump) without ever agreeing is applied only up to this
                                    # many frames: right ones ran 2-51 frames (campus, night drone), the one wrong (a box drawn
                                    # on a person hidden behind a sign; the backward run followed a passer-by) ran 174
UNDO_DEPTH = 50       # the operator's decisions: tracking stops there, clearing never removes them
# ----------------------------------------------------------------------------------------------------------------------
def safe_name(text):
    n = re.sub(r'[^\w.-]+', '_', os.path.splitext(os.path.basename(str(text).rstrip('/\\')))[0]).strip('_.')
    return n or 'video'
# ----------------------------------------------------------------------------------------------------------------------
def _imwrite(path, img):            # cv2.imwrite fails on non-ASCII Windows paths
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    if ok:
        with open(path, 'wb') as f:
            f.write(buf.tobytes())
    return ok
# ----------------------------------------------------------------------------------------------------------------------
def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0
# ----------------------------------------------------------------------------------------------------------------------
def _frames(a, b):
    return f'frame {a}' if a == b else f'frames {a}–{b}'
# ----------------------------------------------------------------------------------------------------------------------
def _agree(a, b):
    """Two boxes on the same object: they overlap well, or their centres are close for the box size."""
    if _iou(a, b) >= 0.5:
        return True
    h = max(2.0, ((a[3] - a[1]) + (b[3] - b[1])) / 2)
    return float(np.hypot((a[0] + a[2] - b[0] - b[2]) / 2, (a[1] + a[3] - b[1] - b[3]) / 2)) <= AGREE_REL * h
# ----------------------------------------------------------------------------------------------------------------------
def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
# ----------------------------------------------------------------------------------------------------------------------
def _read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)
# ----------------------------------------------------------------------------------------------------------------------
def _write_json(path, value, **kw):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(value, f, **kw)
# ----------------------------------------------------------------------------------------------------------------------
def cache_key(name):
    """Folder of a video's frame cache in runs/labels/: readable, and unique although names may hold '/' and any letters."""
    import hashlib
    return f"{safe_name(name.replace('/', '__'))[:60]}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"
# ----------------------------------------------------------------------------------------------------------------------
def labels_file(video):
    """The one-object labels of a video: <stem>.gt.jsonl, else the older gt.jsonl of a folder with only this video, else None."""
    return next((p for p, k in utils_sources.find_gt(video) if k == 'one'), None)
# ----------------------------------------------------------------------------------------------------------------------
class LabelSession:
    """One video being labeled. Thread-safe: the web UI calls it from Flask threads; decoding and tracking run in threads of
    their own. Labels: list (one entry per frame) of None or {'src', 'box' (source px, or None for absent), 'score'?}."""
    def __init__(self, source, name=None, model='NANO', cache_dir=CACHE_DIR, models_dir=None):
        if not os.path.isfile(source):
            raise ValueError(f'not a video file: {source}')
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise ValueError(f'cannot open the video: {source}')
        self.src_w, self.src_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
        self.frames_hint = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)        # container's guess; the decode gives the truth
        cap.release()
        if self.src_w <= 0 or self.src_h <= 0:
            raise ValueError(f'no video frames in {source}')
        self.source = os.path.abspath(source)
        self.models_dir = models_dir or os.path.join(REPO, 'models')
        self.name = name or (utils_sources.video_name(self.source) if utils_sources.in_store(self.source) else safe_name(source))
        self.gt_path = utils_sources.sidecar(self.source, utils_sources.GT_ONE)
        self.meta_path = utils_sources.sidecar(self.source, utils_sources.LABEL_META)
        self.cache = os.path.join(cache_dir, cache_key(self.name), 'frames')
        self.k = min(1.0, CACHE_WIDTH / self.src_w)                           # cache px = source px * k
        self.model = model
        self.lock = threading.RLock()
        self.labels = []
        self.decoded = 0                                                      # frames in the cache so far
        self.total = None                                                     # known when decoding has finished
        self.decode_error = None
        self.version = 0                                                      # bumps on every label change (the UI refetches)
        self.tracking = False
        self.track_pos = None
        self.stop_reason = None
        self.back_note = None                                                 # what the last back-tracking pass did
        self.phase = None                                                     # 'back' / 'forward' while tracking
        self.undo_stack = []                                                  # label lists before each operator action
        self._stop = threading.Event()
        self._thread = None
        self._closed = False
        self.created = time.strftime('%Y-%m-%d %H:%M:%S')
        self._load_existing()
        self._decoder = threading.Thread(target=self._decode, daemon=True)
        self._decoder.start()
    # ----------------------------------------------------------------------------------------------------------------------
    def _load_existing(self):
        """Resume: the video's one-object labels (ours, or ground truth from elsewhere - rows without 'src' are key frames)."""
        p = labels_file(self.source)
        self.legacy_gt = p if p and os.path.normcase(p) != os.path.normcase(self.gt_path) else None   # moves to <stem>.gt.jsonl on save
        if not p:
            return
        try:
            self.created = _read_json(self.meta_path).get('created', self.created)
        except (OSError, ValueError):
            pass
        with open(p, encoding='utf-8') as f:
            lines = f.read().splitlines()
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            i = int(r['frame'])
            while len(self.labels) <= i:
                self.labels.append(None)
            box = r.get('box')
            src = r.get('src') or ('manual' if box else 'absent')
            self.labels[i] = {'src': src, 'box': [float(v) for v in box] if box else None, **{k: r[k] for k in ('score', 'sim') if k in r}}
        print(f'[ Label ] resumed {self.name}: {sum(1 for x in self.labels if x)} labeled frames')
    # ----------------------------------------------------------------------------------------------------------------------
    def _decode(self):
        """Fills the JPEG cache. A complete cache of the same source (done.json) is reused."""
        os.makedirs(self.cache, exist_ok=True)
        done = os.path.join(os.path.dirname(self.cache), 'done.json')
        st = os.stat(self.source)
        sig = {'source': self.source, 'size': st.st_size, 'mtime': int(st.st_mtime), 'k': self.k}
        try:
            d = _read_json(done)
            if all(d.get(key) == v for key, v in sig.items()) and d.get('frames'):
                self.decoded = self.total = int(d['frames'])
                self._fit_labels()
                return
        except (OSError, ValueError):
            pass
        if os.path.exists(done):
            os.remove(done)
        cap = cv2.VideoCapture(self.source)
        i = 0
        while not self._closed:
            ok, frame = cap.read()
            if not ok:
                break
            if self.k < 1.0:
                frame = cv2.resize(frame, (int(round(self.src_w * self.k)), int(round(self.src_h * self.k))), interpolation=cv2.INTER_AREA)
            if not _imwrite(self._frame_path(i), frame):
                self.decode_error = f'cannot write the frame cache ({self.cache})'
                break
            i += 1
            self.decoded = i
        cap.release()
        if self._closed:
            return
        self.total = i
        if i == 0 and not self.decode_error:
            self.decode_error = 'no frames decoded'
        self._fit_labels()
        if not self.decode_error:
            _write_json(done, {**sig, 'frames': i})
        print(f'[ Label ] {self.name}: {i} frames cached')
        if any(self.labels):                       # labeled while decoding: now label.json knows the frame count
            self.save()
    # ----------------------------------------------------------------------------------------------------------------------
    def _fit_labels(self):
        with self.lock:
            n = self.total
            if len(self.labels) < n:
                self.labels += [None] * (n - len(self.labels))
            del self.labels[n:]
            self.version += 1
    # ----------------------------------------------------------------------------------------------------------------------
    def _frame_path(self, i):
        return os.path.join(self.cache, f'{i:06d}.jpg')
    # ----------------------------------------------------------------------------------------------------------------------
    def frame_file(self, i):
        """Path of the cached JPEG of frame i, or None if not decoded (yet)."""
        return self._frame_path(i) if 0 <= i < self.decoded else None
    # ----------------------------------------------------------------------------------------------------------------------
    def frame(self, i):
        p = self.frame_file(i)
        return _imread(p) if p else None
    # ----------------------------------------------------------------------------------------------------------------------
    @property
    def n(self):
        return self.total if self.total is not None else self.decoded
    # ----------------------------------------------------------------------------------------------------------------------
    def _check(self, i):
        i = int(i)
        if not 0 <= i < self.n:
            raise ValueError(f'frame {i} is not there (0..{self.n - 1})')
        while len(self.labels) <= i:
            self.labels.append(None)
        return i
    # ----------------------------------------------------------------------------------------------------------------------
    def _box(self, box):
        x1, x2 = sorted(float(v) for v in (box[0], box[2]))
        y1, y2 = sorted(float(v) for v in (box[1], box[3]))
        x1, y1 = max(0.0, x1), max(0.0, y1)
        x2, y2 = min(float(self.src_w), x2), min(float(self.src_h), y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            raise ValueError('the box is too small')
        return [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]
    # ----------------------------------------------------------------------------------------------------------------------
    def next_key(self, i):
        """Index of the next operator decision (manual / absent) after frame i, or len(labels)."""
        for j in range(i + 1, len(self.labels)):
            if self.labels[j] and self.labels[j]['src'] in MANUAL:
                return j
        return len(self.labels)
    # ----------------------------------------------------------------------------------------------------------------------
    def prev_key(self, i):
        for j in range(min(i, len(self.labels)) - 1, -1, -1):
            if self.labels[j] and self.labels[j]['src'] in MANUAL:
                return j
        return -1
    # ----------------------------------------------------------------------------------------------------------------------
    def _clear_after(self, i):
        """Tracked / interpolated frames after i up to the next key frame came from the old box: drop them."""
        for j in range(i + 1, self.next_key(i)):
            if self.labels[j] and self.labels[j]['src'] not in MANUAL:
                self.labels[j] = None
    # ----------------------------------------------------------------------------------------------------------------------
    def _snapshot(self):
        """Before an operator action: what Ctrl+Z goes back to (dicts are replaced, never changed in place)."""
        self.undo_stack.append(list(self.labels))
        del self.undo_stack[:-UNDO_DEPTH]
        self.stop_reason = self.back_note = None    # the operator acts on it: the messages are done
    # ----------------------------------------------------------------------------------------------------------------------
    def undo(self):
        """Back to the labels before the last action (a whole tracking run is one action). Returns how many are left."""
        self.stop_tracking('undo')
        with self.lock:
            if not self.undo_stack:
                raise ValueError('nothing to undo')
            self.labels = self.undo_stack.pop()
            self.stop_reason = self.back_note = None                      # they described the run just undone
            if self.total is not None:
                self._fit_labels()
            self._changed()
            return len(self.undo_stack)
    # ----------------------------------------------------------------------------------------------------------------------
    def _changed(self, save=True):
        self.version += 1
        if save:
            self.save()
    # ----------------------------------------------------------------------------------------------------------------------
    def set_box(self, i, box):
        """The operator's box on frame i: a key frame; what was tracked after it from the old box goes. The same box again
        (K: "this tracked box is right") keeps them - the tracker got them from exactly this box."""
        self.stop_tracking('box drawn')
        with self.lock:
            i = self._check(i)
            box = self._box(box)
            old = self.labels[i]
            same = bool(old and old['box']) and max(abs(u - v) for u, v in zip(old['box'], box)) <= 0.5
            self._snapshot()
            self.labels[i] = {'src': 'manual', 'box': box}
            if not same:
                self._clear_after(i)
            self._changed()
        return i
    # ----------------------------------------------------------------------------------------------------------------------
    def set_absent(self, i, to_next_key=False):
        """No object on frame i only (the tracked frames after it stay), or from i up to the next key frame."""
        self.stop_tracking('marked absent')
        with self.lock:
            i = self._check(i)
            self._snapshot()
            end = self.next_key(i) if to_next_key else i + 1
            for j in range(i, end):
                self.labels[j] = {'src': 'absent', 'box': None}
            self._changed()
        return end - i
    # ----------------------------------------------------------------------------------------------------------------------
    def delete_all(self, name):
        """Every label of this project (Delete all). Guarded: the caller must name the project (the UI sends it only after
        the operator confirmed); Ctrl+Z brings the labels back, and the gt.jsonl as it was stays in the clip folder as
        gt.before_delete_all.jsonl - so even after a restart nothing is lost. Returns how many labels went."""
        if name != self.name:
            raise ValueError(f'not deleted: confirm with the project name ({self.name})')
        self.stop_tracking('deleted all')
        with self.lock:
            n = sum(1 for lab in self.labels if lab)
            if not n:
                return 0
            gt = labels_file(self.source)
            if gt and os.path.isfile(gt):
                shutil.copyfile(gt, utils_sources.sidecar(self.source, '.gt.before_delete_all.jsonl'))
            self._snapshot()
            self.labels = [None] * len(self.labels)
            self.back_note = f'deleted all {n} labels - Ctrl+Z brings them back'
            self._changed()
        return n
    # ----------------------------------------------------------------------------------------------------------------------
    def delete(self, i):
        """Delete from here: the label of frame i and what the tracker / interpolation put after it, up to the next key frame.
        Always the same, on a key frame or a tracked one: removing a single tracked frame left a hole, and a frame without
        a label reads as "no object" in the benchmark."""
        self.stop_tracking('label deleted')
        with self.lock:
            i = self._check(i)
            self._snapshot()
            self.labels[i] = None
            self._clear_after(i)
            self._changed()
    # ----------------------------------------------------------------------------------------------------------------------
    def clear_after(self, i):
        """Drop tracked / interpolated frames after i up to the next key frame (X)."""
        self.stop_tracking('cleared')
        with self.lock:
            i = self._check(i)
            self._snapshot()
            self._clear_after(i)
            self._changed()
    # ----------------------------------------------------------------------------------------------------------------------
    def interpolate(self, i):
        """Linear boxes between the key frames around frame i (both must have a box), replacing what the tracker put there
        (occlusions, a short drift between two good boxes); Ctrl+Z brings them back. Returns the number of frames filled."""
        self.stop_tracking('interpolated')
        with self.lock:
            i = self._check(i)
            a = i if self.labels[i] and self.labels[i]['src'] == 'manual' else self.prev_key(i)
            b = self.next_key(a if a >= 0 else i)
            if a < 0 or b >= len(self.labels):
                raise ValueError('interpolation needs a key frame before and after this frame')
            la, lb = self.labels[a], self.labels[b]
            if not (la['box'] and lb['box']):
                raise ValueError('both key frames must have a box (not "absent")')
            self._snapshot()
            A, B = np.array(la['box']), np.array(lb['box'])
            filled = 0
            for j in range(a + 1, b):
                t = (j - a) / (b - a)
                filled += 1
                self.labels[j] = {'src': 'interp', 'box': [round(float(v), 1) for v in A + (B - A) * t]}
            self._changed()
            return filled
    # ----------------------------------------------------------------------------------------------------------------------
    def set_model(self, model):
        model = str(model).strip().upper()
        if model not in utils_tracker_sot.SOT_MODELS:
            raise ValueError(f"unknown SOT model '{model}', one of: {', '.join(utils_tracker_sot.SOT_MODELS)}")
        self.stop_tracking('model changed')
        self.model = model
        return model
    # ----------------------------------------------------------------------------------------------------------------------
    def start_tracking(self, i, forward=True):
        """From an operator's box on frame i: first back-track over the tracked run before it (see the module docstring),
        then track forward until: stop_tracking, the tracker loses the target (the app's loss rules, and any frame without a
        confident box), the next key frame, or the end of the decoded video. As fast as the tracker goes: a wrong run is
        checked with Review, watching it slowly does not pay. From a tracked box (Track on a stop frame) there is nothing new
        to look back with: forward only. forward=False: the back pass only (a box drawn with Shift). One undo step for all."""
        self.stop_tracking('restarted')
        with self.lock:
            i = self._check(i)
            lab = self.labels[i]
            if not (lab and lab['box']):
                raise ValueError('draw a box on this frame first')
            back = lab['src'] == 'manual' and i > 0 and bool(self.labels[i - 1]) and self.labels[i - 1]['src'] == 'track'
            if not (back or forward):
                return False
            self._snapshot()
            if forward:
                self._clear_after(i)
            self._changed()
            self._stop.clear()
            self.tracking, self.track_pos, self.stop_reason = True, i, None
            self._thread = threading.Thread(target=self._track, args=(i, list(lab['box']), back, forward), daemon=True)
            self._thread.start()
            return True
    # ----------------------------------------------------------------------------------------------------------------------
    def stop_tracking(self, reason='stopped'):
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            self._stop.set()
            self._stop_why = reason
            t.join(timeout=5)
        self._thread = None
    # ----------------------------------------------------------------------------------------------------------------------
    def _back_track(self, i, box):
        """Track backwards from the operator's box on frame i over the tracked frames before it, down to where the backward
        boxes agree with the forward ones AGREE_N frames in a row; above that the forward run had left the object: its boxes
        are replaced. Returns a note for the operator. Nothing changes if the operator stops it."""
        with self.lock:
            fwd, j = {}, i - 1
            while j >= 0 and self.labels[j] and self.labels[j]['src'] == 'track' and self.labels[j]['box']:
                fwd[j] = list(self.labels[j]['box']); j -= 1
            first = j + 1                                                 # the run before i: first .. i-1
            key = self.labels[j] if j >= 0 else None                      # what it started from (normally a key frame)
        if not fwd:
            return None
        k = self.k
        T = utils_tracker_sot.Tracker_SOT(model=self.model, models_dir=self.models_dir)
        frame = self.frame(i)
        if frame is None or not T.start(frame, [v * k for v in box]):
            return 'back-tracking cannot start on this box'
        back, recent, run, why, j = {}, [box], 0, None, i - 1
        while j >= first:
            if self._stop.is_set():
                return None
            frame = self.frame(j)
            b, score = T.update(frame) if frame is not None else (None, 0)
            if b is None:
                why = f'lost the object at frame {j}'
                break
            bs = self._box([v / k for v in b])
            if self._jump(recent, bs) and not _agree(bs, fwd[j]):          # both runs jumped together: the camera did
                why = f'jumped at frame {j}'
                break
            back[j] = {'src': 'track', 'box': bs, 'score': round(float(score), 3), 'sim': round(float(T.similarity), 3)}
            recent = recent[-4:] + [bs]
            run = run + 1 if _agree(bs, fwd[j]) else 0
            if run >= AGREE_N:
                break
            j -= 1
        if run >= AGREE_N:                                                # agreed on j .. j+AGREE_N-1: the forward run is right there
            fix = [f for f in back if f >= j + AGREE_N]
        elif why:                                                         # stopped without ever agreeing: which run is on the object?
            fix = list(back)
            if len(back) > ALONE_MAX:                                     # both runs cannot be right that long: it follows another
                return (f'back-tracking from {i} went {len(back)} frames without ever meeting the run - is this box on '
                        f'the object (not on something in front of it)? {_frames(first, i - 1)} unchanged, check them')
        else:                                                             # down to the start of the run (a short run: < AGREE_N)
            meets = run > 0 or (bool(key and key.get('box')) and (_iou(recent[-1], key['box']) >= MEET_IOU or _agree(recent[-1], key['box'])))
            if not meets:
                return (f'back-tracking from {i} disagrees with the run all the way down to frame {first} and misses '
                        f'its start: {_frames(first, i - 1)} unchanged, check them')
            fix = [f for f in back if f >= first + run]                   # the frames agreeing at its start keep theirs
        if not fix:
            return f'checked back from {i}: the frames before it are right'
        a, z = min(fix), max(fix)
        with self.lock:
            for f in fix:
                self.labels[f] = back[f]
            self._changed(save=False)
        note = f'back-tracked from {i}: {_frames(a, z)} fixed (the run had left the object there)'
        if why and run < AGREE_N and a > first:
            note += f'; back-tracking {why}: {_frames(first, a - 1)} unchecked'
        return note
    # ----------------------------------------------------------------------------------------------------------------------
    # ----------------------------------------------------------------------------------------------------------------------
    def _track(self, i, box, back=False, forward=True):
        k = self.k
        reason, last_save = 'end of the video', i
        try:
            if back:
                self.phase = 'back'
                self.back_note = self._back_track(i, box)
                if self._stop.is_set():
                    reason = getattr(self, '_stop_why', 'stopped')
                    return
            if not forward:
                reason = 'box only, no tracking forward'
                return
            self.phase = 'forward'
            T = utils_tracker_sot.Tracker_SOT(model=self.model, models_dir=self.models_dir)
            frame = self.frame(i)
            if frame is None or not T.start(frame, [v * k for v in box]):
                reason = 'the tracker cannot start on this box'
                return
            self.model = T.model                                          # MIL if the weights are missing
            recent = [box]
            j = i + 1
            while True:
                if self._stop.is_set():
                    reason = getattr(self, '_stop_why', 'stopped')
                    break
                if j >= self.n:
                    if self.total is None:                                # decoding has not got this far yet
                        time.sleep(0.05); continue
                    reason = 'end of the video'
                    break
                with self.lock:
                    lab = self.labels[j] if j < len(self.labels) else None
                if lab and lab['src'] in MANUAL:
                    reason = f'reached the key frame {j}'
                    meet = _iou(recent[-1], lab['box']) if lab['box'] and j > i + 1 else 1.0
                    if meet < MEET_IOU:                                   # it followed something else on the way (an occluder)
                        reason = f'the track does not meet the key frame {j} (IoU {meet:.2f}): it drifted on the way'
                    break
                frame = self.frame(j)
                if frame is None:
                    reason = f'frame {j} not readable'
                    break
                b, score = T.update(frame)
                if b is None:
                    reason = 'tracker: ' + (T.stop_reason or 'low score')
                    break
                box_src = self._box([v / k for v in b])
                jump = self._jump(recent, box_src)
                with self.lock:
                    self._check(j)
                    self.labels[j] = {'src': 'track', 'box': box_src, 'score': round(float(score), 3), 'sim': round(float(T.similarity), 3)}
                    self.track_pos = j
                    self._changed(save=False)
                if jump:                                                  # either side can be the wrong one: on campus person
                    reason = (f'the box jumped {jump:.1f} box heights since frame {j - 1}: here or just before, '   # 1 drifted to a
                              f'it is on another object (it can come back after a passer-by)')    # passer-by at 485-488, back at 489
                    break
                recent = recent[-4:] + [box_src]
                if j - last_save >= SAVE_EVERY:
                    self.save(); last_save = j
                j += 1
        except Exception as e:                                            # noqa: BLE001 - the UI must see it, not a dead thread
            reason = f'tracker error: {e}'
        finally:
            self.tracking, self.stop_reason, self.phase = False, reason, None
            self.version += 1
            self.save()
            print(f'[ Label ] {self.name}: tracking stopped at {self.track_pos}: {reason}')
    @staticmethod
    def _jump(boxes, box):
        """How far (in box heights) the new box centre is from where the last boxes were heading, if beyond JUMP_REL (one step)
        or JUMP2_REL (two steps); else 0."""
        if len(boxes) < 3:
            return 0.0
        c = lambda b: np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
        h = max(2.0, boxes[-1][3] - boxes[-1][1])
        d1 = float(np.linalg.norm(c(box) - (c(boxes[-1]) + (c(boxes[-1]) - c(boxes[-3])) / 2)) / h)
        if d1 > JUMP_REL:
            return d1
        if len(boxes) >= 5:
            d2 = float(np.linalg.norm(c(box) - (c(boxes[-2]) + 2 * (c(boxes[-2]) - c(boxes[-5])) / 3)) / h)
            if d2 > JUMP2_REL:
                return d2
        return 0.0
    # ----------------------------------------------------------------------------------------------------------------------
    def counts(self):
        c = {'manual': 0, 'track': 0, 'interp': 0, 'absent': 0}
        for lab in self.labels:
            if lab:
                c[lab['src']] = c.get(lab['src'], 0) + 1
        c['labeled'] = sum(c.values())
        return c
    # ----------------------------------------------------------------------------------------------------------------------
    def state(self):
        return {'name': self.name, 'source': self.source, 'out': self.gt_path, 'frames': self.n, 'decoded': self.decoded,
                'decoding': self.total is None, 'decode_error': self.decode_error, 'width': self.src_w, 'height': self.src_h,
                'fps': round(self.fps, 3), 'model': self.model, 'tracking': self.tracking,
                'track_pos': self.track_pos, 'stop_reason': self.stop_reason, 'back_note': self.back_note, 'phase': self.phase, 'version': self.version, 'counts': self.counts(),
                'undo': len(self.undo_stack)}
    # ----------------------------------------------------------------------------------------------------------------------
    def labels_compact(self):
        """For the UI: per frame None or [src letter, x1, y1, x2, y2] (no box for absent)."""
        with self.lock:
            out = []
            for lab in self.labels:
                if not lab:
                    out.append(None)
                elif lab['box'] is None:
                    out.append([lab['src'][0]])
                else:
                    out.append([lab['src'][0]] + lab['box'] + [lab.get('sim')])
            return {'version': self.version, 'labels': out}
    # ----------------------------------------------------------------------------------------------------------------------
    def save(self):
        """<stem>.gt.jsonl + <stem>.label.json next to the video (an older gt.jsonl it was read from goes: one file per video)."""
        with self.lock:
            rows = [json.dumps({'frame': i, 'box': lab['box'], 'src': lab['src'], **{k: lab[k] for k in ('score', 'sim') if k in lab}})
                    for i, lab in enumerate(self.labels) if lab]
            meta = {'tool': 'DTC Label tab', 'frames': self.n, 'width': self.src_w, 'height': self.src_h,
                    'fps': round(self.fps, 3), 'tracker': self.model, 'created': self.created,
                    'updated': time.strftime('%Y-%m-%d %H:%M:%S'), 'counts': self.counts()}
        tmp = self.gt_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(rows) + ('\n' if rows else ''))
        utils_platform.replace_file(tmp, self.gt_path)     # the benchmark may be reading it
        _write_json(self.meta_path, meta, indent=1)
        if self.legacy_gt and os.path.isfile(self.legacy_gt):
            os.remove(self.legacy_gt)
            self.legacy_gt = None
    # ----------------------------------------------------------------------------------------------------------------------
    def close(self, save=True):
        self.stop_tracking('closed')
        self._closed = True
        if self._decoder.is_alive() and self._decoder is not threading.current_thread():
            self._decoder.join(timeout=5)
        if save and self.labels and any(self.labels):
            self.save()
# ----------------------------------------------------------------------------------------------------------------------
_SESSION = None
_SESSION_LOCK = threading.Lock()
# ----------------------------------------------------------------------------------------------------------------------
def current():
    return _SESSION
# ----------------------------------------------------------------------------------------------------------------------
def open_session(source, model='NANO', **kw):
    """Opens a video for labeling (the previous session is saved and closed). source: a name in the store, an alias, or a
    file path (copied into videos/uploads/ when outside the store)."""
    global _SESSION
    path = resolve(source)
    with _SESSION_LOCK:
        if _SESSION is not None:
            _SESSION.close()
            _SESSION = None
        _SESSION = LabelSession(path, model=model, **kw)
        return _SESSION
# ----------------------------------------------------------------------------------------------------------------------
def close_session(save=True):
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            _SESSION.close(save=save)
        _SESSION = None
# ----------------------------------------------------------------------------------------------------------------------
def _free_path(folder, base, ext):
    name, i = base, 1
    while os.path.exists(os.path.join(folder, name + ext)):
        i += 1
        name = f'{base}_{i}'
    return os.path.join(folder, name + ext)
# ----------------------------------------------------------------------------------------------------------------------
def copy_in(path, name=None, upload_dir=None, replace=False):
    """A video from outside the store -> videos/uploads/<name>.<ext> (never over another file unless replace: tests)."""
    upload_dir = upload_dir or UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(path)[1].lower() or '.mp4'
    base = safe_name(name or path)
    dst = os.path.join(upload_dir, base + ext) if replace else _free_path(upload_dir, base, ext)
    if replace:
        for suf in SIDECARS:
            if os.path.isfile(utils_sources.sidecar(dst, suf)):
                os.remove(utils_sources.sidecar(dst, suf))
    print(f'[ Label ] copying {path} -> {dst}')
    shutil.copyfile(path, dst + '.tmp')
    os.replace(dst + '.tmp', dst)
    return dst
# ----------------------------------------------------------------------------------------------------------------------
def _find(name):
    alias = utils_sources.ALIASES.get(name)
    return next((v for v in utils_sources.list_videos() if v['name'] in (name, alias)), None)
# ----------------------------------------------------------------------------------------------------------------------
def resolve(spec):
    """spec (a store name, an alias, a file path) -> the video file in the store."""
    spec = str(spec or '').strip().strip('"')
    if not spec:
        raise ValueError('no video given')
    v = _find(spec)
    if v:
        return v['path']
    if not os.path.isfile(spec):
        name, src, gt = utils_sources.resolve_source(spec)          # raises ValueError listing the names
        if not os.path.isfile(src):
            raise ValueError(f'labeling needs a video file, not {src} (record a camera / stream to a file first)')
        spec = src
    return spec if utils_sources.in_store(spec) else copy_in(spec)
# ----------------------------------------------------------------------------------------------------------------------
def _labels_meta(video):
    try:
        return _read_json(utils_sources.sidecar(video, utils_sources.LABEL_META))
    except (OSError, ValueError):
        return None
# ----------------------------------------------------------------------------------------------------------------------
def video_sources():
    """Every video of the store for the Label tab: [{name, label, sub}], the ones with our labels first (latest first).
    sub: its folder · labels n / N frames | ground truth (MOT) | not labeled."""
    alias_of = {v: k for k, v in utils_sources.ALIASES.items()}
    mine, rest = [], []
    for v in utils_sources.list_videos():
        meta = _labels_meta(v['path'])
        kinds = {k for _, k in v['gt']}
        if meta:
            sub = f"labels {(meta.get('counts') or {}).get('labeled', 0)} / {meta.get('frames') or '?'} frames"
        elif kinds:
            sub = 'ground truth' + (' (MOT)' if kinds == {'mot'} else '')
        else:
            sub = 'not labeled'
        folder = os.path.dirname(v['name'])
        row = {'name': v['name'], 'label': v['name'] + (f" ({alias_of[v['name']]})" if v['name'] in alias_of else ''),
               'sub': (folder + ' · ' if folder else '') + sub}
        (mine if meta else rest).append((meta.get('updated', '') if meta else '', row))
    mine.sort(key=lambda r: r[0], reverse=True)
    return [r for _, r in mine + rest]
# ----------------------------------------------------------------------------------------------------------------------
def remove_source(name):
    """Delete from the store: the video with its labels / ground truth is MOVED to videos/.trash/<time>_<name>/ (move it
    back to undo); the only video of a folder goes with its whole folder. The frame cache runs/labels/... is dropped."""
    name = str(name or '')
    v = _find(name)
    if v is None:
        raise ValueError(f'not in the list: {name}')
    S = current()
    if S is not None and os.path.normcase(S.source) == os.path.normcase(os.path.abspath(v['path'])):
        close_session()
    os.makedirs(TRASH_DIR, exist_ok=True)
    dst = os.path.join(TRASH_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{cache_key(v['name'])}")
    folder = os.path.dirname(os.path.abspath(v['path']))
    if utils_sources._alone_in_folder(v['path']) and utils_sources.in_store(folder):
        shutil.move(folder, dst)
    else:
        os.makedirs(dst)
        for p in [v['path']] + [utils_sources.sidecar(v['path'], suf) for suf in SIDECARS]:
            if os.path.isfile(p):
                shutil.move(p, os.path.join(dst, os.path.basename(p)))
    shutil.rmtree(os.path.join(CACHE_DIR, cache_key(v['name'])), ignore_errors=True)
    return {'action': 'trash', 'to': dst}
# ----------------------------------------------------------------------------------------------------------------------
def thumb(name):
    """A small JPEG for the list (cached in runs/labels/.thumbs/): a labeled video shows its first key frame with the box,
    another one a frame a third of the way in."""
    import hashlib
    v = _find(name)
    if v is None:
        raise ValueError(f'not in the list: {name}')
    path = v['path']
    gt = labels_file(path)
    stamp = os.path.getmtime(gt) if gt and os.path.isfile(gt) else os.path.getmtime(path)
    cache = os.path.join(CACHE_DIR, '.thumbs')
    out = os.path.join(cache, f"{hashlib.md5(str(name).encode()).hexdigest()[:12]}_{int(stamp)}.jpg")
    if os.path.isfile(out):
        return out
    i, box = None, None
    if gt and os.path.isfile(gt):
        rows = []
        for line in open(gt, encoding='utf-8'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        r = next((r for r in rows if r.get('src') == 'manual' and r.get('box')), None) or next((r for r in rows if r.get('box')), None)
        if r:
            i, box = int(r['frame']), r['box']
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, i if i is not None else n // 3)
    ok, img = cap.read()
    cap.release()
    if not ok or img is None:
        raise ValueError(f'cannot read a frame of {path}')
    s = THUMB_W / img.shape[1]
    img = cv2.resize(img, (THUMB_W, max(1, round(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
    if box:
        x1, y1, x2, y2 = [round(v * s) for v in box]
        cv2.rectangle(img, (x1, y1), (max(x1 + 2, x2), max(y1 + 2, y2)), (0, 149, 255), 1)
    os.makedirs(cache, exist_ok=True)
    _imwrite(out, img)
    return out
# ----------------------------------------------------------------------------------------------------------------------
def save_upload(file_storage, upload_dir=None):
    """A video sent from the browser -> videos/uploads/<name>.<ext> (listed as 'uploads/<name>'). A name already there is
    never overwritten: <name>_2, <name>_3, ... Returns the path."""
    upload_dir = upload_dir or UPLOAD_DIR
    base, ext = os.path.splitext(os.path.basename(file_storage.filename or 'video.mp4'))
    ext = ext.lower() if re.fullmatch(r'\.[A-Za-z0-9]{1,5}', ext or '') else '.mp4'
    os.makedirs(upload_dir, exist_ok=True)
    path = _free_path(upload_dir, safe_name(base), ext)
    file_storage.save(path)
    return path
# ----------------------------------------------------------------------------------------------------------------------
