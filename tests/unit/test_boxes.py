"""Click-to-track box geometry (Pipeliner.click_to_box / clamp_box)."""
import unittest
from tests.unit import _paths  # noqa: F401
import utils_DTC_pipeline as P


class Cfg: sot_box_ratio = 0.10
class Dummy: config = Cfg()


class TestBoxes(unittest.TestCase):
    def test_click_box_is_ratio_of_frame_and_centred(self):
        x1, y1, x2, y2 = P.Pipeliner.click_to_box(Dummy(), (400, 300), (600, 800, 3))
        self.assertEqual((x2 - x1, y2 - y1), (80, 60))
        self.assertEqual(((x1 + x2) // 2, (y1 + y2) // 2), (400, 300))

    def test_click_box_stays_inside_the_frame(self):
        for pt in [(0, 0), (799, 599), (0, 599), (799, 0)]:
            x1, y1, x2, y2 = P.Pipeliner.click_to_box(Dummy(), pt, (600, 800, 3))
            self.assertTrue(0 <= x1 < x2 <= 800 and 0 <= y1 < y2 <= 600, (pt, (x1, y1, x2, y2)))

    def test_clamp_box_orders_and_clips(self):
        self.assertEqual(P.Pipeliner.clamp_box(Dummy(), [900, 700, -50, -20], (600, 800, 3)), [0, 0, 800, 600])

    def test_clamp_box_keeps_a_minimum_size(self):
        x1, y1, x2, y2 = P.Pipeliner.clamp_box(Dummy(), [100, 100, 101, 101], (600, 800, 3))
        self.assertGreaterEqual(min(x2 - x1, y2 - y1), 8)


if __name__ == '__main__':
    unittest.main()
