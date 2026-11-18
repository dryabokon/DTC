"""Click-to-track with re-acquisition (NCCM, NCCS): NANO tracks; when it stops, a whole-frame NCC search finds the target
again and restarts NANO there. Benchmark-only models (utils_bench lists them next to utils_tracker_sot.SOT_MODELS).

Why each piece is there (research on the 15 thermal videos of videos/itc, x86 run 0002: NANO F1 0.679, NCCM 0.833, NCCS 0.837):
  - NANO drifts off the target 4-17 frames BEFORE it stops, so a template taken at the stop is junk. Templates = the click
    template + the tracker's own box K frames before the stop (1 / 1.5 / 2 s), each at 4 scales.
  - gates against grabbing the wrong thing: score >= MIN_NCC; prediction gate (the target cannot have moved faster than
    GATE_PX_S px/s since it was last seen); stability (the same place wins STABLE frames in a row); lead (best minus the best
    elsewhere >= LEAD); overlay test (a patch that has not changed at all over 1 s is a HUD element, not a target).
  - NCCS only: after a re-acquisition the box is dropped again when it no longer looks like itself 1.5 s ago
    (NCC < VERIF_NCC for VERIF_S s). Checking from the first frame hurt (the click template is too weak), so only after a restart.
Limits: the search costs ~300 ms per frame at 1280x800 on x86 while the target is lost (not real time); among many similar
objects it grabs a neighbour (campus: NANO 0.640, NCCS 0.608). The thresholds were set on the same 15 videos (optimistic).
Interface as utils_tracker_sot.Tracker_SOT for bench_worker: start, update, is_active, stop_reason, model.
"""
import collections

import cv2
import numpy as np

import utils_tracker_sot as U
# ----------------------------------------------------------------------------------------------------------------------
K_BACK_S = (1.0, 1.5, 2.0)          # own-box templates this many seconds before the stop (24/36/48 frames at 24 fps)
SCALES = (0.85, 1.0, 1.15, 1.3)
MIN_NCC = 0.55
LEAD = 0.10
STABLE = 3
GATE_PX_S = 120.0
OVERLAY_DIFF = 1.5                  # mean |diff| of the patch vs 1 s ago below this = static overlay
VERIF_BACK_S, VERIF_NCC, VERIF_S = 1.5, 0.35, 0.5
# the benchmark lists these next to utils_tracker_sot.SOT_MODELS (Benchmark tab only: not in Live / Label, the default stays NANO)
REACQ_MODELS = {
    'NCCM': {'label': 'NCCM: NANO + re-acquisition', 'verify': False, 'pi_ms': 30},
    'NCCS': {'label': 'NCCS: NCCM + look check', 'verify': True, 'pi_ms': 30},
}
# ----------------------------------------------------------------------------------------------------------------------
def crop(g, box):
    H, W = g.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    return g[y1:y2, x1:x2].copy() if x2 - x1 >= 4 and y2 - y1 >= 4 else None
# ----------------------------------------------------------------------------------------------------------------------
def ncc(a, b):
    """NCC of two patches (b resized to a's size)."""
    if a is None or b is None:
        return 0.0
    b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])
# ----------------------------------------------------------------------------------------------------------------------
class Reacq:
    """Same interface as utils_tracker_sot.Tracker_SOT for bench_worker: start, update, is_active, stop_reason, model."""
    def __init__(self, model, models_dir, fps=25.0, tracker_kw=None):
        self.model, self.models_dir, self.verify = model, models_dir, REACQ_MODELS[model]['verify']
        self.tracker_kw = tracker_kw or {}      # loss rules of the inner NANO (utils_tracker_cand in Live: the app's settings)
        self.fps = float(fps) if fps and fps > 0 else 25.0
        self.inner, self.stop_reason, self.active = None, None, False

    @property
    def is_active(self):
        return self.active                  # it never gives up: when NANO stops it searches

    def start(self, frame, box):
        self.hist_len = int(max(K_BACK_S + (VERIF_BACK_S, 1.0)) * self.fps) + 2
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.greys = collections.deque([g], maxlen=int(self.fps) + 1)        # for the overlay test
        self.click = crop(g, box)
        self.templates = [self.click]
        self.n = 0
        self.last_seen = (self.n, self._centre(box))
        self.after_reacq, self.bad = False, 0
        self.stable = []
        self.active = True
        return self._restart(frame, box)

    @staticmethod
    def _centre(b):
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    def _restart(self, frame, box):
        self.inner = U.Tracker_SOT(model='NANO', models_dir=self.models_dir, **self.tracker_kw)
        ok = self.inner.start(frame, box)
        self.hist = collections.deque(maxlen=self.hist_len)                 # (n, box, grey crop) of this track segment
        self.mode = 'track' if ok else 'search'
        return ok

    # ------------------------------------------------------------------------------------------------------------------
    def update(self, frame):
        self.n += 1
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.greys.append(g)
        if self.mode == 'track':
            box, score = self.inner.update(frame)
            if self.inner.is_active:
                if box is None:
                    return None, score
                c = crop(g, box)
                self.hist.append((self.n, list(box), c))
                self.last_seen = (self.n, self._centre(box))
                if self.verify and self.after_reacq and self._looks_changed(c):
                    self._to_search('appearance changed after re-acquisition')
                    return None, 0.0
                return box, score
            self._to_search(self.inner.stop_reason)
        return self._search(frame, g)

    def _looks_changed(self, c):
        back = int(VERIF_BACK_S * self.fps)
        if len(self.hist) <= back:
            return False
        old = self.hist[-1 - back][2]
        self.bad = self.bad + 1 if ncc(c, old) < VERIF_NCC else 0
        return self.bad >= int(VERIF_S * self.fps)

    def _to_search(self, reason):
        self.stop_reason = reason
        hist = list(self.hist)
        fresh = []
        for s in K_BACK_S:
            k = int(s * self.fps)
            if len(hist) > k and hist[-1 - k][2] is not None:
                fresh.append(hist[-1 - k][2])
        if fresh:                               # a long enough segment: its own look replaces the older K-back templates
            self.templates = [self.click] + fresh
        self.inner, self.mode, self.stable, self.bad = None, 'search', [], 0
        self.after_reacq = False

    # ------------------------------------------------------------------------------------------------------------------
    def _search(self, frame, g):
        H, W = g.shape
        best = None                             # (score, lead, x1, y1, w, h)
        for t in self.templates:
            if t is None:
                continue
            for s in SCALES:
                w, h = int(round(t.shape[1] * s)), int(round(t.shape[0] * s))
                if w < 4 or h < 4 or w >= W or h >= H:
                    continue
                r = cv2.matchTemplate(g, cv2.resize(t, (w, h)), cv2.TM_CCOEFF_NORMED)
                _, v, _, (x, y) = cv2.minMaxLoc(r)
                if best is not None and v <= best[0]:
                    continue
                r2 = r.copy()
                r2[max(0, y - h):y + h + 1, max(0, x - w):x + w + 1] = -1     # the best elsewhere, for the lead
                best = (v, v - float(r2.max()), x, y, w, h)
        if best is None:
            return None, 0.0
        v, lead, x, y, w, h = best
        box = [x, y, x + w, y + h]
        c = self._centre(box)
        n0, c0 = self.last_seen
        reach = GATE_PX_S * (self.n - n0) / self.fps + 1.5 * max(w, h)
        ok = v >= MIN_NCC and lead >= LEAD and np.hypot(c[0] - c0[0], c[1] - c0[1]) <= reach and not self._overlay(g, box)
        if not ok:
            self.stable = []
            return None, 0.0
        self.stable = [p for p in self.stable if np.hypot(p[0] - c[0], p[1] - c[1]) <= 0.5 * max(w, h)] + [c]
        if len(self.stable) < STABLE:
            return None, 0.0
        if self._restart(frame, box):
            self.after_reacq = True
            self.last_seen = (self.n, c)
            self.hist.append((self.n, box, crop(g, box)))
            return box, float(v)
        return None, 0.0

    def _overlay(self, g, box):
        if len(self.greys) < self.greys.maxlen:
            return False
        a, b = crop(g, box), crop(self.greys[0], box)
        return a is not None and b is not None and float(cv2.absdiff(a, b).mean()) < OVERLAY_DIFF
# ----------------------------------------------------------------------------------------------------------------------
