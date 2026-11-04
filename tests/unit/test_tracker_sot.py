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


def _torch():
    try:
        import torch, torchvision  # noqa: F401
        return True
    except ImportError:
        return False


class TestSOTModels(unittest.TestCase):
    """Every selectable model (sot_tracking_model / UI dropdown) starts, follows a moving target and reports its score."""
    def follow(self, model):
        T = U.Tracker_SOT(model=model, models_dir=os.path.join(_paths.REPO, 'models'))
        if not all(os.path.isfile(f) for f in T.weights()):
            self.skipTest(f'{model}: weights not found')
        x = 200
        self.assertTrue(T.start(frame((x, 240)), [x - 24, 240 - 16, x + 24, 240 + 16]))
        self.assertEqual(T.model, model)
        for _ in range(40):
            x += 2; T.update(frame((x, 240)))
        self.assertTrue(T.is_active, f'{model}: {T.stop_reason}')
        self.assertLess(abs((T.box[0] + T.box[2]) / 2 - x), 8, f'{model}: box {T.box}, target x {x}')

    def test_nano(self): self.follow('NANO')
    def test_vit(self): self.follow('VIT')
    def test_mil(self): self.follow('MIL')

    @unittest.skipUnless(_torch(), 'torch not installed')
    def test_siamban(self): self.follow('SIAMBAN')

    @unittest.skipUnless(_torch(), 'torch not installed')
    def test_siamfc(self): self.follow('SIAMFC')

    def test_unknown_model_is_an_error(self):
        with self.assertRaises(ValueError):
            U.Tracker_SOT(model='NOPE')

    def test_set_model_stops_a_running_track(self):
        T = started(320, 240)
        T.set_model('VIT')
        self.assertFalse(T.is_active); self.assertEqual(T.stop_reason, 'model changed'); self.assertEqual(T.model, 'VIT')

    def test_old_models_dir_setting_still_works(self):
        T = U.Tracker_SOT(models_dir=MODELS)          # .../models/nanotrack/ -> .../models
        self.assertTrue(all(os.path.isfile(f) for f in T.weights('NANO')))

    def test_available_lists_every_model(self):
        names = [m['name'] for m in U.Tracker_SOT(models_dir=os.path.join(_paths.REPO, 'models')).available()]
        self.assertEqual(names, list(U.SOT_MODELS))


if __name__ == '__main__':
    unittest.main()
