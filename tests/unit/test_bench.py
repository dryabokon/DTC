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
        for cfg, word in (({'models': ['NOPE']}, 'models'), ({'sequences': ['nope']}, 'sequences'), ({'max_frames': 5}, 'max_frames'),
                          ({'order': 'random'}, 'order')):
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
        runs = B.select_runs(self.seq, self.cfg(tracks=5, order='smallest'), (640, 480, 200))
        self.assertEqual([r['track'] for r in runs], [2, 1])

    def test_largest_one_track_two_starts(self):
        runs = B.select_runs(self.seq, self.cfg(tracks=1, order='largest', starts='first+middle', max_frames=50), (640, 480, 200))
        self.assertEqual([(r['track'], r['start']) for r in runs], [(1, 0), (1, 100)])
        self.assertTrue(all(r['frames'] == 50 for r in runs))


class TestMetrics(unittest.TestCase):
    def test_iou(self):
        self.assertAlmostEqual(BW.iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
        self.assertAlmostEqual(BW.iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)
        self.assertEqual(BW.iou([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)

    def test_lost_at_is_the_first_of_five_bad_frames(self):
        ious = [0.8] * 10 + [0.05] * 6
        m = BW.task_metrics(ious, [1.0] * 16, [5.0] * 16, [0.9] * 10, [0.2] * 6, list(range(1, 17)))
        self.assertEqual(m['lost_at'], 11)
        self.assertAlmostEqual(m['success'], 10 / 16, places=3)


@unittest.skipUnless(os.path.isfile(os.path.join(_paths.REPO, 'images', 'v00', 'gt.txt')), 'campus video not found')
class TestRun(unittest.TestCase):
    def setUp(self):
        self.saved = B.RUNS_DIR
        B.RUNS_DIR = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(B.RUNS_DIR, ignore_errors=True)
        B.RUNS_DIR = self.saved

    def test_tiny_run_end_to_end(self):
        rid = B.start({'models': ['NANO', 'MIL'], 'sequences': ['campus'], 'tracks': 1, 'max_frames': 25, 'loss_rules': True})
        t0 = time.time()
        while B.progress(rid)['state'] == 'RUNNING' and time.time() - t0 < 120:
            time.sleep(0.5)
        p = B.progress(rid)
        self.assertEqual(p['state'], 'DONE', open(os.path.join(B.RUNS_DIR, rid, 'log.txt')).read()[-500:])
        self.assertEqual(p['done_frac'], 1.0)
        r = json.load(open(os.path.join(B.RUNS_DIR, rid, 'results.json')))
        self.assertEqual(set(r['models']), {'NANO', 'MIL'})
        self.assertGreater(r['models']['NANO']['iou'], 0.3)
        self.assertEqual(B.runs_summary()[0]['id'], rid)
        s = B.task_series(rid, 0)
        self.assertEqual(len(s['NANO']['iou']), 25)


if __name__ == '__main__':
    unittest.main()
