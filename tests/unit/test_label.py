"""Label tab (utils_label): frame cache, key frames, clearing, absent, interpolation, tracking forward, saved clip readable by
the benchmark, resume. A synthetic video: a textured patch moving right over a fixed noise background."""
import json, os, shutil, tempfile, time, unittest
from tests.unit import _paths
import cv2
import numpy as np
import utils_label as UL
import utils_bench as B

N, W, H, SIDE = 90, 320, 240, 40


def truth(i):
    x = 40 + 2 * i
    return [x, 100, x + SIDE, 100 + SIDE]


def make_video(path):
    rng = np.random.default_rng(0)
    bg = rng.integers(0, 120, (H, W, 3), dtype=np.uint8)
    patch = np.kron((np.indices((4, 4)).sum(0) % 2) * 255, np.ones((10, 10)))[..., None].repeat(3, 2).astype(np.uint8)
    patch[:, :, 2] = 40
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), 25, (W, H))
    for i in range(N):
        f = bg.copy(); x1, y1, x2, y2 = truth(i); f[y1:y2, x1:x2] = patch
        w.write(f)
    w.release()


class TestLabel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.video = os.path.join(cls.tmp, 'moving patch.mp4')
        make_video(cls.video)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def session(self, **kw):
        S = UL.LabelSession(self.video, clips_dir=os.path.join(self.tmp, 'clips'), cache_dir=os.path.join(self.tmp, 'cache'), **kw)
        t0 = time.time()
        while S.total is None and time.time() - t0 < 30:
            time.sleep(0.05)
        self.addCleanup(S.close)
        return S

    def wait_tracking(self, S, timeout=60):
        t0 = time.time()
        while S.tracking and time.time() - t0 < timeout:
            time.sleep(0.05)

    def test_00_cache_and_name(self):
        S = self.session()
        self.assertEqual(S.total, N)
        self.assertEqual(S.name, 'moving_patch')
        self.assertTrue(os.path.isfile(S.frame_file(N - 1)))
        self.assertIsNone(S.frame_file(N))
        self.assertEqual(S.frame(0).shape, (H, W, 3))

    def test_10_keys_clear_absent_interp(self):
        S = self.session()
        for i in range(len(S.labels)):
            S.labels[i] = None
        S.set_box(10, truth(10))
        for j in range(11, 30):                                # pretend the tracker ran
            S.labels[j] = {'src': 'track', 'box': truth(j)}
        S.set_box(40, truth(40))
        S.set_box(20, truth(20))                               # a correction: tracked 21..29 are gone, 40 stays
        self.assertTrue(all(S.labels[j] is None for j in range(21, 30)))
        self.assertEqual(S.labels[40]['src'], 'manual')
        self.assertEqual(S.labels[15]['src'], 'track')
        S.labels[35] = {'src': 'track', 'box': truth(35)}
        self.assertEqual(S.interpolate(25), 18)                # 21..39 without the tracked 35
        self.assertEqual(S.labels[35]['src'], 'track')
        np.testing.assert_allclose(S.labels[30]['box'], truth(30), atol=0.6)
        self.assertEqual(S.set_absent(50, to_next_key=True), N - 50)
        self.assertIsNone(S.labels[N - 1]['box'])
        S.delete(20)                                           # a key frame removed: what hung on it goes too
        self.assertIsNone(S.labels[20]); self.assertIsNone(S.labels[30]); self.assertEqual(S.labels[40]['src'], 'manual')
        with self.assertRaises(ValueError):
            S.set_box(N + 5, truth(0))
        with self.assertRaises(ValueError):
            S.start_tracking(5)                                # no box there

    def test_20_track_save_resume(self):
        S = self.session(model='NANO')
        for i in range(len(S.labels)):
            S.labels[i] = None
        S.set_box(0, truth(0))
        S.set_box(60, truth(60))
        S.speed = 0
        S.start_tracking(0)
        self.wait_tracking(S)
        self.assertFalse(S.tracking)
        self.assertIn('key frame 60', S.stop_reason)
        tracked = [j for j in range(1, 60) if S.labels[j] and S.labels[j]['src'] == 'track']
        self.assertGreater(len(tracked), 50, S.stop_reason)
        ious = [self._iou(S.labels[j]['box'], truth(j)) for j in tracked]
        self.assertGreater(np.median(ious), 0.6)

        S.save()
        d = S.out_dir
        self.assertTrue(os.path.isfile(os.path.join(d, 'video.mp4')))
        meta = json.load(open(os.path.join(d, 'label.json')))
        self.assertEqual(meta['frames'], N); self.assertEqual(meta['counts']['manual'], 2)
        tracks = B.load_tracks({'kind': 'clip', 'gt': os.path.join(d, 'gt.jsonl')})
        self.assertEqual(set(tracks), {0})
        self.assertEqual(tracks[0][60], [float(v) for v in truth(60)])
        n_labeled = S.counts()['labeled']
        S.close()

        R = self.session()                                     # the same source: the same project, labels back
        self.assertEqual(R.name, S.name)
        self.assertEqual(R.counts()['labeled'], n_labeled)
        self.assertEqual(R.labels[60]['src'], 'manual')

    def test_30_never_writes_into_a_foreign_clip(self):
        S = UL.LabelSession(self.video, name=None, clips_dir=os.path.join(self.tmp, 'clips2'), cache_dir=os.path.join(self.tmp, 'cache2'))
        self.addCleanup(S.close)
        os.makedirs(os.path.join(self.tmp, 'clips2', 'other'), exist_ok=True)
        open(os.path.join(self.tmp, 'clips2', 'other', 'gt.jsonl'), 'w').write('{"frame": 0, "box": [1, 1, 5, 5]}\n')
        S.clips_dir = os.path.join(self.tmp, 'clips2')
        self.assertEqual(S._free_name('other'), 'other_2')

    @staticmethod
    def _iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        i = ix * iy
        return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


if __name__ == '__main__':
    unittest.main()
