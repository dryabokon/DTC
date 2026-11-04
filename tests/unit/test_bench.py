"""SOT benchmark: settings, target selection, metrics, and one tiny run end to end (worker process, progress, results)."""
import json, os, shutil, tempfile, time, unittest
from tests.unit import _paths
import utils_bench as B
import bench_worker as BW


class TestSettings(unittest.TestCase):
    def test_presets_normalize(self):
        for k in B.PRESETS:
            c = B.normalize({'preset': k})
            self.assertTrue(c['models'] and c['sequences'], k)

    def test_bad_settings_are_readable_errors(self):
        for cfg, word in (({'models': ['NOPE']}, 'models'), ({'sequences': ['nope']}, 'sequences'), ({'tracks': -1}, 'tracks'),
                          ({'min_size': 0}, 'min_size')):
            with self.assertRaises(ValueError) as e:
                B.normalize(cfg)
            self.assertIn(word, str(e.exception))

    def test_estimate_grows_with_slow_models(self):
        c = B.normalize({'models': ['NANO']}); c2 = B.normalize({'models': ['NANO', 'SIAMFC']})
        self.assertGreater(B.estimate_s(c2, 100), 10 * B.estimate_s(c, 100))


class TestTargets(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        rows = []
        for f in range(1, 201):
            rows.append(f'{f},1,100,100,40,40,1,-1,-1,-1')                  # big, whole sequence
            rows.append(f'{f},2,300,200,10,12,1,-1,-1,-1')                  # small, whole sequence
            if f <= 30:
                rows.append(f'{f},3,50,50,20,20,1,-1,-1,-1')                # too short
            rows.append(f'{f},4,0,300,30,30,1,-1,-1,-1')                    # touches the edge: never eligible
        open(os.path.join(self.d, 'gt.txt'), 'w').write('\n'.join(rows))
        self.seq = {'id': 'syn', 'kind': 'mot', 'video': 'x', 'gt': os.path.join(self.d, 'gt.txt')}

    def tearDown(self):
        shutil.rmtree(self.d)

    def cfg(self, **kw):
        return {**B.DEFAULTS, **kw}

    def test_smallest_first_and_ineligible_tracks_skipped(self):
        runs = B.select_runs(self.seq, self.cfg(tracks=5), (640, 480, 200))
        self.assertEqual([r['track'] for r in runs], [2, 1])

    def test_one_object_tracked_to_the_end_of_its_track(self):
        runs = B.select_runs(self.seq, self.cfg(tracks=1), (640, 480, 200))
        self.assertEqual([(r['track'], r['start'], r['frames']) for r in runs], [(2, 0, 199)])


class TestMetrics(unittest.TestCase):
    def test_frame_states(self):
        g = [100, 100, 140, 140]
        self.assertEqual(BW.frame_state([100, 100, 140, 140], g), (1, 0, 0))     # on the target
        self.assertEqual(BW.frame_state([300, 300, 340, 340], g), (0, 1, 1))     # elsewhere: a false box and a miss
        self.assertEqual(BW.frame_state(None, g), (0, 0, 1))                     # no box: a miss
        self.assertEqual(BW.frame_state([1, 1, 5, 5], None), (0, 1, 0))         # a box, no target
        self.assertEqual(BW.frame_state(None, None), (0, 0, 0))

    def test_a_tiny_target_a_few_px_off_is_a_hit(self):
        self.assertEqual(BW.frame_state([104, 102, 111, 109], [100, 100, 107, 107]), (1, 0, 0))

    def test_precision_recall_f1(self):
        r = BW.prf(8, 2, 8)
        self.assertEqual((r['precision'], r['recall']), (0.8, 0.5))
        self.assertAlmostEqual(r['f1'], 2 * 0.8 * 0.5 / 1.3, places=3)

    def test_iou(self):
        self.assertAlmostEqual(BW.iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
        self.assertAlmostEqual(BW.iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)
        self.assertEqual(BW.iou([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)


@unittest.skipUnless(os.path.isfile(os.path.join(_paths.REPO, 'images', 'v00', 'gt.txt')), 'campus video not found')
class TestRun(unittest.TestCase):
    def setUp(self):
        self.saved = B.RUNS_DIR
        B.RUNS_DIR = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(B.RUNS_DIR, ignore_errors=True)
        B.RUNS_DIR = self.saved

    def test_tiny_run_end_to_end(self):
        rid = B.start({'models': ['NANO', 'MIL'], 'sequences': ['campus'], 'tracks': 1, 'max_frames': 25})
        t0 = time.time()
        while B.progress(rid)['state'] == 'RUNNING' and time.time() - t0 < 120:
            time.sleep(0.5)
        p = B.progress(rid)
        self.assertEqual(p['state'], 'DONE', open(os.path.join(B.RUNS_DIR, rid, 'log.txt')).read()[-500:])
        self.assertEqual(p['done_frac'], 1.0)
        r = json.load(open(os.path.join(B.RUNS_DIR, rid, 'results.json')))
        self.assertEqual(set(r['models']), {'NANO', 'MIL'})
        self.assertGreater(r['models']['NANO']['f1'], 0.3)
        self.assertEqual(B.runs_summary()[0]['id'], rid)
        s = B.task_series(rid, 0)
        self.assertEqual(len(s['NANO']['hit']), 25)


if __name__ == '__main__':
    unittest.main()
