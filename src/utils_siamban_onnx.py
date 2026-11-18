"""SiamBAN on ONNX Runtime, without torch: one tracker logic, many backbones (click-to-track models SBAN_<NAME>).

Ported from https://github.com/Yemen-Romanian/DTC_Training at commit 6e3123f:
  src/models/trackers/siamban.py       TrackerSiamBAN (initialize, track, cosine window, size penalty, APCE confidence)
  src/models/trackers/siamban_onnx.py  TrackerSiamBANOnnx (backbone / head sessions, input layout)
The crop helpers (get_subwindow, mean_channels) come from tools/DL/siamban_pytorch/tracking_utils.py, vendored from the
same repo. Their module there pulls in torch, timm and the datasets, so the tracking code is copied here instead of
imported; keep the two in step when DTC_Training changes the tracker.

Weights: the backbone and head exported separately by DTC_Training's utils/onnx/export_to_onnx.py. The backbone maps
'image' -> 'features' (dynamic spatial size: one graph for the 127 exemplar and the 255 search crop, ImageNet
normalisation baked in); the head maps ('z_feat', 'x_feat') -> ('cls', 'reg').
"""
import cv2  # noqa: F401  (tracking_utils needs it; imported here so a missing cv2 fails at the top)
import numpy as np

from DL.siamban_pytorch.tracking_utils import get_subwindow, mean_channels
# ----------------------------------------------------------------------------------------------------------------------
_SESSIONS = {}                      # (path, threads, spinning) -> InferenceSession, shared by all tracker instances:
                                    # the sessions hold no tracking state (run() is thread-safe), loading takes a while on the Pi
# ----------------------------------------------------------------------------------------------------------------------
def _session(path, threads, allow_spinning):
    key = (path, int(threads), bool(allow_spinning))
    if key not in _SESSIONS:
        import onnxruntime as ort
        o = ort.SessionOptions()
        o.intra_op_num_threads = int(threads)                     # 0 = ONNX Runtime picks (one per physical core)
        o.add_session_config_entry('session.intra_op.allow_spinning', '1' if allow_spinning else '0')
        # CPU only, as in DTC_Training: with onnxruntime-gpu on the laptop the numbers stay comparable with the Pi's
        _SESSIONS[key] = ort.InferenceSession(path, o, providers=['CPUExecutionProvider'])
    return _SESSIONS[key]
# ----------------------------------------------------------------------------------------------------------------------
class TrackerSiamBANOnnx:
    EXEMPLAR_SIZE = 127
    SEARCH_SIZE = 255
    RESPONSE_SIZE = 17
    STRIDE = 8
    WINDOW_INFLUENCE = 0.30         # weight of the cosine window in the blended score
    SIZE_LR = 0.1                   # how fast the target size follows the predicted one
    PENALTY_K = 0.0                 # scale / aspect-ratio penalty; 0 = off

    def __init__(self, backbone_path, head_path, threads=0, allow_spinning=True):
        """threads: intra-op threads per session (0 = ONNX Runtime default). allow_spinning: idle worker threads busy-wait
        for the next op - lower latency on free cores, but it takes CPU from decoding and the rest of the loop."""
        self.backbone = _session(str(backbone_path), threads, allow_spinning)
        self.head = _session(str(head_path), threads, allow_spinning)
        self.window = self._cosine_window()
        self.exemplar_features = None
    # ----------------------------------------------------------------------------------------------------------------------
    @classmethod
    def _cosine_window(cls):
        # normalised by max, not sum: the score it is blended with is a softmax probability peaking near 1
        hann = np.hanning(cls.RESPONSE_SIZE)
        window = np.outer(hann, hann)
        return window / window.max()
    # ----------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _to_input(crop):
        return crop.transpose(2, 0, 1).astype(np.float32)[np.newaxis] / 255.0
    # ----------------------------------------------------------------------------------------------------------------------
    def _features(self, crop):
        return self.backbone.run(['features'], {'image': self._to_input(crop)})[0]
    # ----------------------------------------------------------------------------------------------------------------------
    def initialize(self, image, bbox):
        """bbox: [x, y, w, h] in frame pixels."""
        self.target_sz = np.array([bbox[3], bbox[2]], dtype=float)                  # [h, w]
        self.pos = np.array([bbox[1] + bbox[3] / 2, bbox[0] + bbox[2] / 2])         # [cy, cx]
        self._update_scales()
        z_crop = get_subwindow(image, self.pos, self.EXEMPLAR_SIZE, round(self.s_z), mean_channels(image))
        self.exemplar_features = self._features(z_crop)
    # ----------------------------------------------------------------------------------------------------------------------
    def track(self, image):
        """-> ((x, y, w, h), confidence); confidence = APCE of the cls map (its own scale, not a probability)."""
        x_crop = get_subwindow(image, self.pos, self.SEARCH_SIZE, round(self.s_x), mean_channels(image))
        cls, reg = self.head.run(['cls', 'reg'], {'z_feat': self.exemplar_features, 'x_feat': self._features(x_crop)})

        cls_exp = np.exp(cls[0] - cls[0].max(axis=0))
        score = cls_exp[1] / cls_exp.sum(axis=0)                                    # foreground probability [17, 17]
        confidence = self._calculate_confidence(cls)
        reg_map = reg[0]                                                            # [4, 17, 17]: dl, dt, dr, db per cell

        # the size penalty first, then the positional window (as in the reference implementation)
        score = score * self._size_penalty(reg_map)
        score = (1 - self.WINDOW_INFLUENCE) * score + self.WINDOW_INFLUENCE * self.window
        r_max, c_max = np.unravel_index(score.argmax(), score.shape)
        dl, dt, dr, db = reg_map[:, r_max, c_max]

        image_center = self.SEARCH_SIZE // 2
        px = image_center + (c_max - self.RESPONSE_SIZE // 2) * self.STRIDE
        py = image_center + (r_max - self.RESPONSE_SIZE // 2) * self.STRIDE
        cx_crop = np.clip(px + (dr - dl) / 2, 0.0, self.SEARCH_SIZE)
        cy_crop = np.clip(py + (db - dt) / 2, 0.0, self.SEARCH_SIZE)
        w_crop = np.clip(dl + dr, 1.0, self.SEARCH_SIZE)
        h_crop = np.clip(dt + db, 1.0, self.SEARCH_SIZE)

        scale = self.s_x / self.SEARCH_SIZE
        cx_img = self.pos[1] + (cx_crop - image_center) * scale
        cy_img = self.pos[0] + (cy_crop - image_center) * scale
        max_change = 1.3                                                            # at most +-30 % size change per frame
        w_img = np.clip(w_crop * scale, self.target_sz[1] / max_change, self.target_sz[1] * max_change)
        h_img = np.clip(h_crop * scale, self.target_sz[0] / max_change, self.target_sz[0] * max_change)

        # the centre stays inside the frame: outside it the search crop would be pure padding and could never recover
        H, W = image.shape[0], image.shape[1]
        self.pos = np.array([np.clip(cy_img, 0.0, H - 1), np.clip(cx_img, 0.0, W - 1)])
        self.target_sz = (1 - self.SIZE_LR) * self.target_sz + self.SIZE_LR * np.array([h_img, w_img])
        self.target_sz = np.maximum(self.target_sz, 2.0)
        self._update_scales()

        box = (int(self.pos[1] - self.target_sz[1] / 2), int(self.pos[0] - self.target_sz[0] / 2),
               int(self.target_sz[1]), int(self.target_sz[0]))
        return box, float(confidence)
    # ----------------------------------------------------------------------------------------------------------------------
    def _size_penalty(self, reg_map):
        """[17, 17] multiplier in (0, 1]: down-weights cells whose box disagrees in size / aspect with the target."""
        if self.PENALTY_K <= 0.0:
            return np.ones_like(reg_map[0])
        eps = 1e-6
        scale = self.s_x / self.SEARCH_SIZE
        w = (reg_map[0] + reg_map[2]) * scale + eps
        h = (reg_map[1] + reg_map[3]) * scale + eps
        target_h, target_w = self.target_sz[0] + eps, self.target_sz[1] + eps

        def change(r):
            return np.maximum(r, 1.0 / r)

        def padded(width, height):
            pad = 0.5 * (width + height)
            return np.sqrt((width + pad) * (height + pad))

        s = change(padded(w, h) / padded(target_w, target_h))
        r = change((target_w / target_h) / (w / h))
        return np.exp(-(s * r - 1.0) * self.PENALTY_K)
    # ----------------------------------------------------------------------------------------------------------------------
    def _update_scales(self):
        context = 0.5 * self.target_sz.sum()
        self.s_z = np.sqrt((self.target_sz[0] + context) * (self.target_sz[1] + context))
        self.s_x = self.s_z * (self.SEARCH_SIZE / self.EXEMPLAR_SIZE)
    # ----------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _calculate_confidence(cls):
        d = cls[0, 1] - cls[0, 0]                                                   # foreground - background
        lo = np.min(d)
        return (np.max(d) - lo) ** 2 / np.mean((d - lo) ** 2)
