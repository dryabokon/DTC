import os
import time
import cv2
import numpy as np
# ----------------------------------------------------------------------------------------------------------------------
class Tracker_SOT:
    """Lightweight single-object tracker for click-to-track (no detector involved).

    model 'NANO' -> cv2.TrackerNano (NanoTrack v2, ONNX via cv2.dnn), ~11-14 ms/frame on RPi5 CPU
    model 'MIL'  -> cv2.TrackerMIL (no extra weights, slower ~70 ms/frame, fallback only)
    Boxes are xyxy in frame pixels.

    Loss detection. NanoTrack's own score does NOT drop when the target is lost (it latches onto background or a
    neighbour with score ~0.87, same as when tracking; PETS09 study with ground truth: AUC 0.48). Two checks that do work:
      edge       - the box touches the frame border for edge_frames consecutive frames: the target has left the frame
      appearance - NanoTrack backbone embedding of the current box vs the one taken at start (1.4 ms on RPi5):
                   median over app_window frames below app_rel x its value in the first frames -> occluded / drifted away
      empty box  - contrast (grey std) inside the box drops below empty_rel x its start value for empty_frames frames:
                   on sky / water NanoTrack keeps a box with nothing in it and drifts steadily to the frame edge
                   (Fly2 sim with camera yaw: silent drift after the target left the view 860 -> 28 frames)
    Known limit: a swap to another similar-looking person at a crossing mostly goes unnoticed (needs a detector / ReID).
    """
    def __init__(self, model='NANO', models_dir='../models/nanotrack/', score_th=0.3, lost_th=15,
                 edge_frames=8, edge_px=3, app_rel=0.6, app_window=30, app_base_frames=10, empty_rel=0.35, empty_frames=5):
        self.model = model.upper()
        self.models_dir = models_dir
        self.score_th = score_th
        self.lost_th = lost_th
        self.edge_frames = edge_frames          # 0/None disables the edge check
        self.edge_px = edge_px
        self.app_rel = app_rel                  # 0/None disables the appearance check
        self.app_window = app_window
        self.app_base_frames = app_base_frames
        self.empty_rel = empty_rel              # 0/None disables the empty-box check
        self.empty_frames = empty_frames
        self.tracker = None
        self.embedder = None
        self.box = None
        self.score = 0.0
        self.similarity = 1.0
        self.lost_cnt = 0
        self.last_ms = 0.0
        self.stop_reason = None                 # why the last run ended: 'cancelled', 'left the frame', ...
        self.stop_time = 0.0
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def _create(self):
        if self.model == 'NANO':
            backbone = os.path.join(self.models_dir, 'nanotrack_backbone_sim.onnx')
            head = os.path.join(self.models_dir, 'nanotrack_head_sim.onnx')
            if os.path.isfile(backbone) and os.path.isfile(head):
                params = cv2.TrackerNano_Params()
                params.backbone = backbone
                params.neckhead = head
                if self.embedder is None and self.app_rel:
                    self.embedder = cv2.dnn.readNet(backbone)
                return cv2.TrackerNano_create(params)
            print('[SOT trk] NanoTrack weights not found in', self.models_dir, '- falling back to MIL')
            self.model = 'MIL'
        return cv2.TrackerMIL_create()
    # ----------------------------------------------------------------------------------------------------------------------
    def _embed(self, frame, box):
        """NanoTrack backbone on a 127x127 template-style crop (box + context), pooled to a unit 48-d vector."""
        x1, y1, x2, y2 = box
        w, h = max(2, x2 - x1), max(2, y2 - y1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        p = 0.5 * (w + h)
        k = 127.0 / np.sqrt((w + p) * (h + p))
        M = np.float32([[k, 0, 63.5 - cx * k], [0, k, 63.5 - cy * k]])
        z = cv2.warpAffine(frame, M, (127, 127), borderMode=cv2.BORDER_REPLICATE)
        self.embedder.setInput(cv2.dnn.blobFromImage(z))
        v = self.embedder.forward()[0].mean(axis=(1, 2))
        return v / (np.linalg.norm(v) + 1e-9)
    # ----------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _contrast(frame, box):
        x1, y1, x2, y2 = [int(v) for v in box]
        p = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        return float(cv2.cvtColor(p, cv2.COLOR_BGR2GRAY).std()) if p.size else 0.0
    # ----------------------------------------------------------------------------------------------------------------------
    @property
    def is_active(self):
        return self.tracker is not None
    # ----------------------------------------------------------------------------------------------------------------------
    def start(self, frame, box_xyxy):
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box_xyxy]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(W - 1, x2), min(H - 1, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return False
        t = time.perf_counter()
        self.tracker = self._create()
        self.tracker.init(frame, (x1, y1, x2 - x1, y2 - y1))
        self.box = [x1, y1, x2, y2]
        self.emb0 = self._embed(frame, self.box) if self.embedder is not None else None
        self.sims = []
        self.app_base = None
        self.edge_cnt = 0
        self.contrast0 = self._contrast(frame, self.box)
        self.empty_cnt = 0
        self.last_ms = (time.perf_counter() - t) * 1000
        self.score = 1.0
        self.similarity = 1.0
        self.lost_cnt = 0
        self.stop_reason = None
        print(f'[SOT trk] start {self.model} box={self.box}')
        return True
    # ----------------------------------------------------------------------------------------------------------------------
    def stop(self, reason='cancelled'):
        if self.tracker is not None:
            print(f'[SOT trk] stop: {reason}')
            self.stop_reason = reason
            self.stop_time = time.time()
        self.tracker = None
        self.box = None
        self.score = 0.0
        self.lost_cnt = 0
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def _check_lost(self, frame, box):
        """Returns a reason string if the target looks lost, else None."""
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = box
        if self.edge_frames:
            e = self.edge_px
            at_edge = x1 <= e or y1 <= e or x2 >= W - 1 - e or y2 >= H - 1 - e
            self.edge_cnt = self.edge_cnt + 1 if at_edge else 0
            if self.edge_cnt >= self.edge_frames:
                return 'left the frame'

        if self.empty_rel and self.contrast0 > 1.0:
            self.empty_cnt = self.empty_cnt + 1 if self._contrast(frame, box) < self.empty_rel * self.contrast0 else 0
            if self.empty_cnt >= self.empty_frames:
                return 'target lost (empty box)'

        if self.emb0 is not None:
            self.similarity = float(self._embed(frame, box) @ self.emb0)
            self.sims.append(self.similarity)
            if self.app_base is None and len(self.sims) >= self.app_base_frames:
                self.app_base = float(np.median(self.sims))
            if self.app_base is not None and len(self.sims) >= self.app_window:
                recent = float(np.median(self.sims[-self.app_window:]))
                if recent < self.app_rel * self.app_base:
                    return 'target lost (occluded or drifted)'
            if len(self.sims) > 4 * self.app_window:
                del self.sims[:-self.app_window]
        return None
    # ----------------------------------------------------------------------------------------------------------------------
    def update(self, frame):
        """Returns (box_xyxy or None, score). Stops itself when the target is judged lost (see class docstring)."""
        if self.tracker is None:
            return None, 0.0
        t = time.perf_counter()
        ok, (x, y, w, h) = self.tracker.update(frame)
        score = float(self.tracker.getTrackingScore()) if self.model == 'NANO' else (1.0 if ok else 0.0)

        if ok and score >= self.score_th and w >= 2 and h >= 2:
            self.box = [int(x), int(y), int(x + w), int(y + h)]
            self.lost_cnt = 0
        else:
            self.lost_cnt += 1

        self.score = score if ok else 0.0
        reason = None
        if self.lost_th is not None and self.lost_cnt >= self.lost_th:
            reason = 'low tracker score'
        elif self.lost_cnt == 0:
            reason = self._check_lost(frame, self.box)
        self.last_ms = (time.perf_counter() - t) * 1000

        if reason is not None:
            self.stop(reason)
            return None, 0.0
        return (self.box if self.lost_cnt == 0 else None), self.score
# ----------------------------------------------------------------------------------------------------------------------
