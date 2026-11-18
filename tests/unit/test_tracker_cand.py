"""CAND (utils_tracker_cand): after a loss the target is among the numbered candidates, and picking it resumes tracking."""
import os, unittest
import numpy as np
from tests.unit import _paths
from tests.unit.test_tracker_reacq import frame
import utils_tracker_cand as C

MODELS = os.path.join(_paths.REPO, 'models')


def near(box, x, tol=10):
    return abs((box[0] + box[2]) / 2 - (x + 7)) < tol and abs((box[1] + box[3]) / 2 - 107) < tol


class TestCandidates(unittest.TestCase):
    def test_pick_the_target_after_it_came_back(self):
        rng = np.random.default_rng(0)
        T = C.Cand(MODELS, fps=25)
        x = lambda k: 60 + k
        self.assertTrue(T.start(frame(x(0), True, rng), [x(0), 100, x(0) + 14, 114]))
        for k in range(1, 101):
            box, _ = T.update(frame(x(k), not 40 <= k < 70, rng))
            if 45 <= k:
                self.assertIsNone(box, f'frame {k}: CAND must not pick by itself')
        self.assertEqual(T.mode, 'search')
        self.assertLessEqual(len(T.candidates), C.TOP_K)
        on = [n for n, b, _ in T.candidates if near(b, x(100))]
        self.assertTrue(on, f'the target is not among {T.candidates}')
        self.assertTrue(T.pick(on[0]))
        self.assertEqual((T.mode, T.candidates), ('track', []))
        box, _ = T.update(frame(x(101), True, rng))
        self.assertTrue(box is not None and near(box, x(101)))

    def test_a_number_stays_with_its_place(self):
        T = C.Cand(MODELS, fps=25)
        T._assign([(0.9, [10, 10, 30, 30]), (0.8, [100, 100, 120, 120])])
        T._assign([(0.9, [102, 100, 122, 120]), (0.8, [11, 10, 31, 30])])
        self.assertEqual([(n, b[0]) for n, b, _ in T.candidates], [(1, 11), (2, 102)])

    def test_an_empty_number_cannot_be_picked(self):
        self.assertFalse(C.Cand(MODELS, fps=25).pick(1))


class TestNCCF(unittest.TestCase):
    def test_target_found_again_after_it_vanished(self):
        rng = np.random.default_rng(0)
        T = C.NCCF(MODELS, fps=25)
        x = lambda k: 60 + k
        self.assertTrue(T.start(frame(x(0), True, rng), [x(0), 100, x(0) + 14, 114]))
        hits = 0
        for k in range(1, 121):
            box, _ = T.update(frame(x(k), not 40 <= k < 70, rng))
            hits += k >= 90 and box is not None and near(box, x(k))
        self.assertGreater(T.searches, 0)
        self.assertGreaterEqual(hits, 25)


class TestTrackerLive(unittest.TestCase):
    def setUp(self):
        self.T = C.Tracker_Live(models_dir=MODELS)
        self.rng = np.random.default_rng(0)

    def test_the_list_is_short_and_keeps_the_current_model(self):
        self.assertEqual([m['name'] for m in self.T.available()], ['NANO', 'VIT', 'NCCF', 'CAND'])
        self.T.set_model('MIL')
        self.assertEqual([m['name'] for m in self.T.available()], ['NANO', 'VIT', 'MIL', 'NCCF', 'CAND'])

    def test_the_slow_ncc_models_are_refused(self):
        with self.assertRaises(ValueError):
            self.T.set_model('NCCS')

    def test_switching_models_stops_the_track(self):
        self.T.set_model('cand')
        self.assertTrue(self.T.start(frame(60, True, self.rng), [60, 100, 74, 114]))
        box, _ = self.T.update(frame(61, True, self.rng))
        self.assertTrue(self.T.is_active); self.assertFalse(self.T.searching); self.assertEqual(self.T.box, box)
        self.T.set_model('NANO')
        self.assertFalse(self.T.is_active); self.assertEqual(self.T.stop_reason, 'model changed'); self.assertIsNone(self.T.box)


if __name__ == '__main__':
    unittest.main()
