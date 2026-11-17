"""Re-acquisition (utils_tracker_reacq): the target vanishes, NANO stops, the target comes back - NCCS finds it again."""
import os, unittest
import numpy as np
from tests.unit import _paths
import utils_tracker_reacq as R


def frame(x, visible, rng):
    """A flat noisy sky with a small textured bright target at (x, 100)."""
    f = np.full((240, 320), 60, np.uint8) + rng.integers(0, 6, (240, 320), dtype=np.uint8)
    if visible:
        yy, xx = np.mgrid[0:14, 0:14]
        f[100:114, x:x + 14] = (140 + 80 * np.exp(-((yy - 6) ** 2 + (xx - 7) ** 2) / 18.0)).astype(np.uint8)
    return np.dstack([f, f, f])


class TestReacquisition(unittest.TestCase):
    def test_target_found_again_after_it_vanished(self):
        rng = np.random.default_rng(0)
        T = R.Reacq('NCCS', os.path.join(_paths.REPO, 'models'), fps=25)
        x = lambda k: 60 + k                                    # 1 px per frame to the right
        self.assertTrue(T.start(frame(x(0), True, rng), [x(0), 100, x(0) + 14, 114]))
        hits_after = 0
        for k in range(1, 121):
            visible = not 40 <= k < 70                          # gone for 30 frames
            box, _ = T.update(frame(x(k), visible, rng))
            if k >= 90 and box is not None and abs((box[0] + box[2]) / 2 - (x(k) + 7)) < 8 and abs((box[1] + box[3]) / 2 - 107) < 8:
                hits_after += 1
            if 45 <= k < 70:
                self.assertIsNone(box, f'frame {k}: a box while the target is gone')
        self.assertTrue(T.is_active)
        self.assertGreaterEqual(hits_after, 25, 'the target was not found again after it came back')


if __name__ == '__main__':
    unittest.main()
