"""Live-tab trackers built on NANO. NCCF (below): automatic re-acquisition on a cheap search. Click-to-track with candidates (CAND): NANO tracks; when it stops, the places that look most like the target are shown, numbered,
and the OPERATOR picks one - NANO goes on from there. Nothing is re-acquired automatically: false candidates are the accepted price.

Why it can run in real time while the automatic search (utils_tracker_reacq) cannot: no decision needs several consecutive frames,
so the search may skip frames, and it runs beside the video (background=True) instead of holding it up. It also looks at a half-size
frame with 2 scales: 39 ms per search at 1280x800 on x86, against ~330 ms for the full search (runs/cand, 13 losses of videos/itc).
The templates are those of utils_tracker_reacq: the click + the tracker's own box 1 / 1.5 / 2 s before the stop.
"""
import os
import threading
import time

import cv2
import numpy as np

import utils_tracker_reacq as R
import utils_tracker_sot as U
# ----------------------------------------------------------------------------------------------------------------------
SEARCH_SCALE = 0.5                  # the search runs on the frame scaled by this
SCALES = (1.0, 1.15)                # template sizes tried
TOP_K = 5                           # candidates shown
PEAKS_PER_MAP = 8                   # best places taken from every template x scale (some are dropped as duplicates / overlay)
MIN_SCORE = 0.2
SAME_PLACE = 0.75                   # two peaks closer than this x the box size are one place
SLOT_MISSES = 3                     # a candidate not found again in this many searches gives up its number
LIVE_MODELS = {'NCCF': {'label': 'NCCF: NANO + fast re-acquisition'}, 'CAND': {'label': 'CAND: NANO + candidates after a loss'}}      # Live only
DROPDOWN = ('NANO', 'VIT', 'NCCF', 'CAND')      # what the Live tab offers; the other SOT_MODELS still work through --sot_tracking_model and the API
# ----------------------------------------------------------------------------------------------------------------------
def find(g, g_old, templates):
    """[(score, box)] best first, at most TOP_K; boxes in frame pixels. g_old: the frame ~1 s ago (what has not changed is a HUD overlay)."""
    f = SEARCH_SCALE
    small = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    H, W = small.shape
    peaks = []
    for t in templates:
        for s in SCALES:
            w, h = int(round(t.shape[1] * s * f)), int(round(t.shape[0] * s * f))
            if w < 4 or h < 4 or w >= W or h >= H:
                continue
            r = cv2.matchTemplate(small, cv2.resize(t, (w, h), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED)
            for _ in range(PEAKS_PER_MAP):
                _, v, _, (x, y) = cv2.minMaxLoc(r)
                if v < MIN_SCORE:
                    break
                peaks.append((float(v), [x / f, y / f, (x + w) / f, (y + h) / f]))
                r[max(0, y - h):y + h + 1, max(0, x - w):x + w + 1] = -1
    out, kept = [], []
    for v, b in sorted(peaks, key=lambda p: -p[0]):
        cx, cy, size = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(b[2] - b[0], b[3] - b[1])
        if any(np.hypot(cx - a, cy - c) < SAME_PLACE * max(size, e) for a, c, e in kept):
            continue
        p, q = R.crop(g, b), R.crop(g_old, b) if g_old is not None else None
        if p is not None and q is not None and float(cv2.absdiff(p, q).mean()) < R.OVERLAY_DIFF:
            continue
        kept.append((cx, cy, size)); out.append((v, [int(round(c)) for c in b]))
        if len(out) == TOP_K:
            break
    return out
# ----------------------------------------------------------------------------------------------------------------------
class Cand(R.Reacq):
    """Same interface as Reacq; while the target is lost update() returns no box and `candidates` holds [(number, box, score)].
    background=True: the search runs in its own thread on the newest frame. False: on every `every`-th frame, shown from the next one."""
    def __init__(self, models_dir, fps=25.0, tracker_kw=None, background=False, every=1):
        super().__init__('NCCM', models_dir, fps, tracker_kw)
        self.model, self.background, self.every = 'CAND', background, max(1, int(every))
        self.lock = threading.Lock()
        self.busy, self.gen, self.tick, self.pending, self.search_ms = False, 0, 0, None, 0.0
        self.slots = [None] * TOP_K
        self.frame, self.g = None, None

    @property
    def candidates(self):
        return [(n + 1, s['box'], s['score']) for n, s in enumerate(list(self.slots)) if s is not None]

    def _to_search(self, reason):
        super()._to_search(reason)
        self._clear()

    def _clear(self):
        with self.lock:
            self.gen += 1
            self.pending, self.tick = None, 0
        self.slots = [None] * TOP_K

    # ------------------------------------------------------------------------------------------------------------------
    def _job(self, g, g_old, templates, gen):
        t = cv2.getTickCount()
        found = find(g, g_old, templates)
        with self.lock:
            self.search_ms = (cv2.getTickCount() - t) / cv2.getTickFrequency() * 1000
            self.busy = False
            if gen == self.gen:
                self.pending = found

    def _assign(self, found):
        """Numbers stay with places: a candidate keeps its number while it is found near where it was."""
        slots, free = list(self.slots), set(range(TOP_K))
        seen = set()
        for v, b in found:
            cx, cy, size = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(b[2] - b[0], b[3] - b[1])
            near = [(np.hypot(cx - (s['box'][0] + s['box'][2]) / 2, cy - (s['box'][1] + s['box'][3]) / 2), n) for n, s in enumerate(slots) if s is not None and n in free]
            near = [p for p in near if p[0] <= 1.5 * size]
            n = min(near)[1] if near else next((k for k in sorted(free) if slots[k] is None), None)
            if n is None:
                continue
            slots[n] = {'box': b, 'score': v, 'miss': 0}
            free.discard(n); seen.add(n)
        for n, s in enumerate(slots):
            if s is not None and n not in seen:
                s['miss'] += 1
                if s['miss'] >= SLOT_MISSES:
                    slots[n] = None
        self.slots = slots

    def _search(self, frame, g):
        self.frame, self.g = frame, g
        with self.lock:
            found, self.pending = self.pending, None
        if found is not None:
            self._assign(found)
        g_old = self.greys[0] if len(self.greys) == self.greys.maxlen else None
        templates = [t for t in self.templates if t is not None]
        if self.background:
            with self.lock:
                start, self.busy = not self.busy, True
                gen = self.gen
            if start:
                threading.Thread(target=self._job, args=(g, g_old, templates, gen), daemon=True).start()
        else:
            if self.tick % self.every == 0:
                self.busy = True
                self._job(g, g_old, templates, self.gen)
            self.tick += 1
        return None, 0.0

    # ------------------------------------------------------------------------------------------------------------------
    def pick(self, number):
        """The operator chose candidate `number` (1..TOP_K): NANO goes on from its box."""
        s = self.slots[number - 1] if 1 <= number <= TOP_K else None
        if s is None or self.mode != 'search' or self.frame is None:
            return False
        box = list(s['box'])
        if not self._restart(self.frame, box):
            self.mode = 'search'
            return False
        self.last_seen = (self.n, self._centre(box))
        self.hist.append((self.n, box, R.crop(self.g, box)))
        self._clear()
        return True
# ----------------------------------------------------------------------------------------------------------------------
class NCCF(R.Reacq):
    """NCCS decisions (all its gates, 3 wins in a row, the look check) on the cheap search: half-size frame, 2 scales, ~36 ms at 1280x800
    instead of ~306 ms. x86 run 0007, the 15 videos of videos/itc: F1 0.827 (NCCS 0.837, NANO 0.679). background=True: the search runs
    beside the video on the newest frame, so a frame that arrives while it is busy is not searched."""
    candidates = []

    def __init__(self, models_dir, fps=25.0, tracker_kw=None, background=False):
        super().__init__('NCCS', models_dir, fps, tracker_kw)
        self.model, self.background = 'NCCF', background
        self.lock = threading.Lock()
        self.busy, self.gen, self.pending, self.search_ms, self.searches, self.search_frames = False, 0, None, 0.0, 0, 0

    def pick(self, number):
        return False

    def _to_search(self, reason):
        super()._to_search(reason)
        with self.lock:
            self.gen += 1
            self.pending = None

    @staticmethod
    def _match(g, templates):
        """(score, lead, x, y, w, h) of the best place in the half-size frame, or None."""
        f = SEARCH_SCALE
        small = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        H, W = small.shape
        best = None
        for t in templates:
            for s in SCALES:
                w, h = int(round(t.shape[1] * s * f)), int(round(t.shape[0] * s * f))
                if w < 4 or h < 4 or w >= W or h >= H:
                    continue
                r = cv2.matchTemplate(small, cv2.resize(t, (w, h), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED)
                _, v, _, (x, y) = cv2.minMaxLoc(r)
                if best is not None and v <= best[0]:
                    continue
                r2 = r.copy()
                r2[max(0, y - h):y + h + 1, max(0, x - w):x + w + 1] = -1
                best = (v, v - float(r2.max()), x, y, w, h)
        return best

    def _job(self, frame, g, n, templates, gen):
        t = time.perf_counter()
        best = self._match(g, templates)
        with self.lock:
            self.search_ms = (time.perf_counter() - t) * 1000
            self.busy = False
            self.searches += 1
            if gen == self.gen:
                self.pending = (frame, g, n, best)

    def _decide(self, frame, g, n, best):
        if best is None:
            return None, 0.0
        f = SEARCH_SCALE
        v, lead, x, y, w, h = best
        box = [int(round(x / f)), int(round(y / f)), int(round((x + w) / f)), int(round((y + h) / f))]
        c = self._centre(box)
        size = max(box[2] - box[0], box[3] - box[1])
        n0, c0 = self.last_seen
        reach = R.GATE_PX_S * (n - n0) / self.fps + 1.5 * size
        ok = v >= R.MIN_NCC and lead >= R.LEAD and np.hypot(c[0] - c0[0], c[1] - c0[1]) <= reach and not self._overlay(g, box)
        if not ok:
            self.stable = []
            return None, 0.0
        self.stable = [p for p in self.stable if np.hypot(p[0] - c[0], p[1] - c[1]) <= 0.5 * size] + [c]
        if len(self.stable) < R.STABLE:
            return None, 0.0
        if not self._restart(frame, box):
            self.mode = 'search'
            return None, 0.0
        self.after_reacq = True
        self.last_seen = (n, c)
        self.hist.append((n, box, R.crop(g, box)))
        return box, float(v)

    def _take(self):
        with self.lock:
            p, self.pending = self.pending, None
        return self._decide(*p) if p is not None else (None, 0.0)

    def _search(self, frame, g):
        self.search_frames += 1
        templates = [t for t in self.templates if t is not None]
        if not self.background:
            self._job(frame, g, self.n, templates, self.gen)
            return self._take()
        out = self._take()
        if self.mode != 'search':
            return out
        with self.lock:
            start, self.busy = not self.busy, True
            gen = self.gen
        if start:
            threading.Thread(target=self._job, args=(frame, g, self.n, templates, gen), daemon=True).start()
        return out
# ----------------------------------------------------------------------------------------------------------------------
class Tracker_Live(U.Tracker_SOT):
    """The Live tab's click-to-track tracker: Tracker_SOT + NCCF and CAND, switchable like the other models."""
    def __init__(self, model='NANO', models_dir='../models/', score_th=None, lost_th=15, edge_frames=8, app_rel=0.6, app_window=30, empty_rel=0.35):
        self.cand, self.fps = None, 25.0
        self.tracker_kw = dict(score_th=score_th, lost_th=lost_th, edge_frames=edge_frames, app_rel=app_rel, app_window=app_window, empty_rel=empty_rel)
        super().__init__(model=model, models_dir=models_dir, **self.tracker_kw)

    @property
    def is_active(self):
        return self.cand is not None or super().is_active

    @property
    def searching(self):
        c = self.cand
        return c is not None and c.mode == 'search'

    @property
    def candidates(self):
        c = self.cand
        return c.candidates if c is not None and c.mode == 'search' else []

    @property
    def search_stats(self):
        """NCCF: {frames seen while searching, searches done}: the difference is what the search skipped."""
        c = self.cand
        return {'frames': c.search_frames, 'searches': c.searches} if isinstance(c, NCCF) else None

    def candidate_at(self, point, margin=6):
        """The number of the candidate under a click, or None."""
        x, y = point
        for n, b, _ in self.candidates:
            if b[0] - margin <= x <= b[2] + margin and b[1] - margin <= y <= b[3] + margin:
                return n
        return None

    def set_model(self, model):
        model = str(model).strip().upper()
        if model not in LIVE_MODELS:
            if self.cand is not None:
                self.stop('model changed')
            return super().set_model(model)
        if self.is_active:
            self.stop('model changed')
        self.model, self.score_th = model, None
        return model

    def weights(self, model=None):
        model = model or self.model
        return super().weights('NANO' if model in LIVE_MODELS else model)

    def available(self):
        every = super().available() + [{'name': m, 'label': v['label'], 'ok': all(os.path.isfile(f) for f in self.weights(m))} for m, v in LIVE_MODELS.items()]
        return [m for m in every if m['name'] in DROPDOWN or m['name'] == self.model]      # a model set from the command line stays visible

    # ------------------------------------------------------------------------------------------------------------------
    def start(self, frame, box_xyxy):
        if self.model not in LIVE_MODELS:
            return super().start(frame, box_xyxy)
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box_xyxy]
        box = [max(0, x1), max(0, y1), min(W - 1, x2), min(H - 1, y2)]
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return False
        t = time.perf_counter()
        c = (NCCF if self.model == 'NCCF' else Cand)(self.models_dir, self.fps, self.tracker_kw, background=True)
        if not c.start(frame, box):
            return False
        self.cand, self.box, self.score, self.stop_reason = c, box, 1.0, None
        self.last_ms = (time.perf_counter() - t) * 1000
        print(f'[SOT trk] start {self.model} box={box} fps={self.fps:.1f} ({self.last_ms:.0f} ms)')
        return True

    def stop(self, reason='cancelled'):
        if self.cand is None:
            return super().stop(reason)
        print(f'[SOT trk] stop: {reason}')
        self.cand, self.box, self.score, self.stop_reason, self.stop_time = None, None, 0.0, reason, time.time()

    def update(self, frame):
        c = self.cand
        if c is None:
            return super().update(frame)
        t = time.perf_counter()
        box, score = c.update(frame)
        self.last_ms = c.search_ms if c.mode == 'search' else (time.perf_counter() - t) * 1000
        self.box = [int(v) for v in box] if box is not None else None
        self.score = score
        return self.box, score

    def pick(self, number):
        c = self.cand
        if c is None or not c.pick(number):
            return False
        self.box, self.score = [int(v) for v in c.hist[-1][1]], 1.0
        print(f'[SOT trk] candidate {number} picked, box={self.box}')
        return True
# ----------------------------------------------------------------------------------------------------------------------
