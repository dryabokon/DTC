"""Tracker_SOT on synthetic frames: a small dark target on a plain background (like a drone on the sky)."""
import os, unittest
import numpy as np
from tests.unit import _paths
import utils_tracker_sot as U

MODELS = os.path.join(_paths.REPO, 'models', 'nanotrack') + '/'
W, H = 640, 480


def frame(target=None):
    rng = np.random.default_rng(0)
    f = np.full((H, W, 3), 200, np.uint8) + rng.integers(0, 3, (H, W, 3), dtype=np.uint8)     # near-uniform "sky"
    if target is not None:
        x, y = int(target[0]), int(target[1])
        f[y - 6:y + 6, x - 12:x + 12] = 40                     # body
        f[y - 2:y + 2, x - 20:x + 20] = 70                     # arms
    return f


def started(x, y):
    T = U.Tracker_SOT(models_dir=MODELS)
    assert T.start(frame((x, y)), [x - 24, y - 16, x + 24, y + 16])
    return T


@unittest.skipUnless(os.path.isfile(MODELS + 'nanotrack_backbone_sim.onnx'), 'NanoTrack weights not found')
class TestTrackerSOT(unittest.TestCase):
    def test_follows_a_moving_target(self):
        T, x = started(200, 240), 200
        for _ in range(60):
            x += 2; T.update(frame((x, 240)))
        self.assertTrue(T.is_active, T.stop_reason)
        cx = (T.box[0] + T.box[2]) / 2
        self.assertLess(abs(cx - x), 6)

    def test_stops_when_the_target_vanishes_from_a_plain_background(self):
        T = started(320, 240)
        for _ in range(10): T.update(frame((320, 240)))
        n = 0
        while T.is_active and n < 40:
            T.update(frame(None)); n += 1
        self.assertFalse(T.is_active, 'SOT kept an empty box for 40 frames')
        self.assertIn('empty box', T.stop_reason)
        self.assertLessEqual(n, 15)

    def test_stops_when_the_target_leaves_the_frame(self):
        T, x = started(560, 240), 560
        while x < W + 40:
            x += 4; T.update(frame((x, 240)) if x < W + 20 else frame(None))
            if not T.is_active: break
        self.assertFalse(T.is_active)
        self.assertTrue(any(k in T.stop_reason for k in ('left the frame', 'empty box')), T.stop_reason)

    def test_manual_stop_records_the_reason(self):
        T = started(320, 240); T.stop('stopped by user')
        self.assertFalse(T.is_active); self.assertEqual(T.stop_reason, 'stopped by user')


if __name__ == '__main__':
    unittest.main()
