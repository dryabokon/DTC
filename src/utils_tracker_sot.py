import os
import time
import cv2
import numpy as np
# ----------------------------------------------------------------------------------------------------------------------
# Click-to-track models. All take a BGR frame and a box; weights live under models_dir (default ../models/).
#   NANO     NanoTrack v2 (cv2.TrackerNano, ONNX)                       models/nanotrack/        default
#   VIT      VitTrack 2023sep (cv2.TrackerVit, ONNX)                    models/vittrack/         from DTC_Training (Yemen-Romanian)
#   SIAMBAN  SiamBAN, MobileNetV3 backbone, trained in DTC_Training     models/siamban/          torch, tools/DL/siamban_pytorch
#   SIAMFC   SiamFC, AlexNet backbone (siamfc_alexnet_e50)              tools/DL/siamfc_pytorch/ torch
#   MIL      cv2.TrackerMIL, no weights                                                          fallback only
# score_th: below it a frame counts as "lost" (lost_th such frames in a row stop SOT). Every model has its own score
# scale, so the default is per model (None = no score check; the loss rules of Tracker_SOT still apply).
# RPi5 CPU, tests/quality/sot_models.py 2026-11-04 (drone_ir + 10 PETS09 runs x 300 frames, raw tracker, IoU vs truth):
#   NANO 10 ms, PETS IoU 0.53 | VIT 14 ms, 0.58 | SIAMBAN 42 ms, 0.41 | SIAMFC 384 ms (2.6 fps), 0.69 on 3 short runs.
#   All four lose the 32x24 thermal drone of drone_ir after ~55 frames. Scores: NANO does not separate on/off target,
#   VIT does (PETS), SIAMBAN (APCE) overlaps on PETS, SIAMFC's is always ~0 -> no score threshold for those two.
# ----------------------------------------------------------------------------------------------------------------------
SOT_MODELS = {
    'NANO':    {'label': 'NanoTrack (default)',                       'score_th': 0.30},
    'VIT':     {'label': 'VitTrack (DTC_Training)',                   'score_th': 0.30},
    'SIAMBAN': {'label': 'SiamBAN MobileNetV3 (DTC_Training, trained)', 'score_th': None},
    'SIAMFC':  {'label': 'SiamFC AlexNet',                            'score_th': None},
    'MIL':     {'label': 'MIL (OpenCV, no weights)',                  'score_th': None},
}
_TORCH_NETS = {}                    # loaded torch trackers, kept between runs (loading takes seconds on the Pi)
# ----------------------------------------------------------------------------------------------------------------------
class _CV:
    """OpenCV tracker. Backend interface: init(frame, xywh); update(frame) -> (ok, xywh, score)."""
    def __init__(self, tracker, has_score):
        self.t, self.has_score = tracker, has_score
    def init(self, frame, xywh):
        self.t.init(frame, tuple(int(v) for v in xywh))
    def update(self, frame):
        ok, xywh = self.t.update(frame)
        return ok, xywh, (float(self.t.getTrackingScore()) if self.has_score else (1.0 if ok else 0.0))
# ----------------------------------------------------------------------------------------------------------------------
class _SiamBAN:
    def __init__(self, weights):
        if weights not in _TORCH_NETS:
            import torch
            from DL.siamban_pytorch.siamban import SiamBANNet, TrackerSiamBAN
            net = SiamBANNet()
            net.load_state_dict(torch.load(weights, map_location='cpu'))
            _TORCH_NETS[weights] = TrackerSiamBAN(net, 'cpu')
        self.t = _TORCH_NETS[weights]
    def init(self, frame, xywh):
        self.t.initialize(frame, list(xywh))
    def update(self, frame):
        r = self.t.track(frame)
        return True, (r.bbox.x, r.bbox.y, r.bbox.width, r.bbox.height), float(r.confidence)
# ----------------------------------------------------------------------------------------------------------------------
class _SiamFC:
    def __init__(self, weights):
        if weights not in _TORCH_NETS:
            from DL.siamfc_pytorch.siamfc.siamfc import TrackerSiamFC
            _TORCH_NETS[weights] = TrackerSiamFC(net_path=weights)
        self.t = _TORCH_NETS[weights]
    def init(self, frame, xywh):
        self.t.init(frame, list(xywh))
    def update(self, frame):
        box, score = self.t.update(frame)
        return True, tuple(float(v) for v in box), float(score)
# ----------------------------------------------------------------------------------------------------------------------
class Tracker_SOT:
    """Lightweight single-object tracker for click-to-track (no detector involved). Models: SOT_MODELS above.
    Boxes are xyxy in frame pixels. The model can be changed at any time (set_model); a running track is stopped.

    Loss detection. NanoTrack's own score does NOT drop when the target is lost (it latches onto background or a
    neighbour with score ~0.87, same as when tracking; PETS09 study with ground truth: AUC 0.48). Checks that do work,
    for every model (the appearance check always uses the NanoTrack backbone as the embedder):
      edge       - the box touches the frame border for edge_frames consecutive frames: the target has left the frame
      appearance - NanoTrack backbone embedding of the current box vs the one taken at start (1.4 ms on RPi5):
                   median over app_window frames below app_rel x its value in the first frames -> occluded / drifted away
      empty box  - contrast (grey std) inside the box drops below empty_rel x its start value for empty_frames frames:
                   on sky / water NanoTrack keeps a box with nothing in it and drifts steadily to the frame edge
                   (Fly2 sim with camera yaw: silent drift after the target left the view 860 -> 28 frames)
    Known limit: a swap to another similar-looking person at a crossing mostly goes unnoticed (needs a detector / ReID).
    """
    def __init__(self, model='NANO', models_dir='../models/', score_th=None, lost_th=15,
                 edge_frames=8, edge_px=3, app_rel=0.6, app_window=30, app_base_frames=10, empty_rel=0.35, empty_frames=5):
        models_dir = os.path.normpath(models_dir)
        if os.path.basename(models_dir) == 'nanotrack':     # older setting: the NanoTrack folder itself
            models_dir = os.path.dirname(models_dir)
        self.models_dir = models_dir
        self.score_th_override = score_th       # None: per-model default from SOT_MODELS
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
        self.set_model(model)
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def set_model(self, model):
        model = str(model).strip().upper()
        if model not in SOT_MODELS:
            raise ValueError(f"unknown SOT model '{model}', one of: {', '.join(SOT_MODELS)}")
        if self.tracker is not None:
            self.stop('model changed')
        self.model = model
        self.score_th = self.score_th_override if self.score_th_override is not None else SOT_MODELS[model]['score_th']
        return model
    # ----------------------------------------------------------------------------------------------------------------------
    def weights(self, model=None):
        """Files the model needs (none for MIL)."""
        d = self.models_dir
        return {'NANO': [os.path.join(d, 'nanotrack', 'nanotrack_backbone_sim.onnx'), os.path.join(d, 'nanotrack', 'nanotrack_head_sim.onnx')],
                'VIT': [os.path.join(d, 'vittrack', 'object_tracking_vittrack_2023sep.onnx')],
                'SIAMBAN': [os.path.join(d, 'siamban', 'siamban_mobilenetv3.pth')],
                'SIAMFC': [os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'DL', 'siamfc_pytorch', 'siamfc_alexnet_e50.pth')],
                'MIL': []}[model or self.model]
    # ----------------------------------------------------------------------------------------------------------------------
    def available(self):
        """[{name, label, ok}] for the UI; ok = the weights are present."""
        return [{'name': m, 'label': v['label'], 'ok': all(os.path.isfile(f) for f in self.weights(m))} for m, v in SOT_MODELS.items()]
    # ----------------------------------------------------------------------------------------------------------------------
    def _create(self):
        files = self.weights()
        missing = [f for f in files if not os.path.isfile(f)]
        if missing:
            print(f'[SOT trk] {self.model}: weights not found ({missing[0]}) - falling back to MIL')
            self.model, self.score_th, files = 'MIL', (self.score_th_override if self.score_th_override is not None else None), []
        if self.embedder is None and self.app_rel:
            backbone = self.weights('NANO')[0]
            if os.path.isfile(backbone):
                self.embedder = cv2.dnn.readNet(backbone)
        if self.model == 'NANO':
            params = cv2.TrackerNano_Params()
            params.backbone, params.neckhead = files
            return _CV(cv2.TrackerNano_create(params), True)
        if self.model == 'VIT':
            params = cv2.TrackerVit_Params()
            params.net = files[0]
            return _CV(cv2.TrackerVit_create(params), True)
        if self.model == 'SIAMBAN':
            return _SiamBAN(files[0])
        if self.model == 'SIAMFC':
            return _SiamFC(files[0])
        return _CV(cv2.TrackerMIL_create(), False)
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
        print(f'[SOT trk] start {self.model} box={self.box} ({self.last_ms:.0f} ms)')
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
        ok, (x, y, w, h), score = self.tracker.update(frame)

        if ok and (self.score_th is None or score >= self.score_th) and w >= 2 and h >= 2:
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
