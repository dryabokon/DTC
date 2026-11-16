"""Label tab (utils_label): frame cache, key frames, clearing, absent, interpolation, tracking forward, labels saved next to the
video and readable by the benchmark, resume, the store list. A synthetic video: a textured patch moving right over a fixed noise background."""
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
        S = UL.LabelSession(self.video, cache_dir=os.path.join(self.tmp, 'cache'), **kw)
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
        S.set_box(15, S.labels[15]['box'])                     # K: the tracked box made a key frame - 16..29 stay
        self.assertEqual(S.labels[15]['src'], 'manual'); self.assertEqual(S.labels[29]['src'], 'track')
        S.undo()
        S.set_box(20, [v + 2 for v in truth(20)])              # a correction (another box): tracked 21..29 are gone, 40 stays
        self.assertTrue(all(S.labels[j] is None for j in range(21, 30)))
        self.assertEqual(S.labels[40]['src'], 'manual')
        self.assertEqual(S.labels[15]['src'], 'track')
        S.labels[35] = {'src': 'track', 'box': [0, 0, 5, 5]}
        self.assertEqual(S.interpolate(25), 19)                # 21..39, the tracked 35 replaced too
        self.assertEqual(S.labels[35]['src'], 'interp')
        S.undo()                                               # Ctrl+Z: the tracked frame is back
        self.assertEqual(S.labels[35]['src'], 'track'); self.assertIsNone(S.labels[30])
        S.interpolate(25)
        np.testing.assert_allclose(S.labels[30]['box'], truth(30), atol=1.1)   # halfway from the +2 box at 20 to 40
        self.assertEqual(S.set_absent(50, to_next_key=True), N - 50)
        self.assertIsNone(S.labels[N - 1]['box'])
        S.labels[60] = {'src': 'track', 'box': truth(60)}; S.labels[61] = {'src': 'track', 'box': truth(61)}
        S.set_absent(60)                                       # N on a tracked frame: this frame only, the tracked tail stays
        self.assertEqual(S.labels[60]['src'], 'absent'); self.assertEqual(S.labels[61]['src'], 'track')
        S.labels[45] = {'src': 'track', 'box': truth(45)}; S.labels[46] = {'src': 'track', 'box': truth(46)}
        S.delete(45)                                           # delete from here on a tracked frame: no hole, the rest goes too
        self.assertIsNone(S.labels[45]); self.assertIsNone(S.labels[46]); self.assertEqual(S.labels[40]['src'], 'manual')
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
        S.start_tracking(0)
        self.wait_tracking(S)
        self.assertFalse(S.tracking)
        self.assertIn('key frame 60', S.stop_reason)
        tracked = [j for j in range(1, 60) if S.labels[j] and S.labels[j]['src'] == 'track']
        self.assertGreater(len(tracked), 50, S.stop_reason)
        ious = [self._iou(S.labels[j]['box'], truth(j)) for j in tracked]
        self.assertGreater(np.median(ious), 0.6)

        S.save()
        self.assertEqual(S.gt_path, os.path.join(self.tmp, 'moving patch.gt.jsonl'))         # next to the video, same name
        meta = json.load(open(os.path.join(self.tmp, 'moving patch.label.json')))
        self.assertEqual(meta['frames'], N); self.assertEqual(meta['counts']['manual'], 2)
        tracks = B.load_tracks({'kind': 'clip', 'gt': S.gt_path})
        self.assertEqual(set(tracks), {0})
        self.assertEqual(tracks[0][60], [float(v) for v in truth(60)])
        n_labeled = S.counts()['labeled']
        S.close()

        R = self.session()                                     # the same source: the same project, labels back
        self.assertEqual(R.name, S.name)
        self.assertEqual(R.counts()['labeled'], n_labeled)
        self.assertEqual(R.labels[60]['src'], 'manual')

    def test_30_ground_truth_from_elsewhere_opens_as_labels(self):
        d = os.path.join(self.tmp, 'gt30')                                     # the older layout: the only video + gt.jsonl
        os.makedirs(d); v = os.path.join(d, 'video.mp4'); shutil.copyfile(self.video, v)
        open(os.path.join(d, 'gt.jsonl'), 'w').write('{"frame": 3, "box": [1, 1, 5, 5]}\n{"frame": 4, "box": null}\n')
        S = UL.LabelSession(v, cache_dir=os.path.join(self.tmp, 'cache30'))
        self.addCleanup(S.close)
        self.assertEqual((S.labels[3]['src'], S.labels[4]['src']), ('manual', 'absent'))
        S.save()                                                               # one file per video: <stem>.gt.jsonl
        self.assertTrue(os.path.isfile(os.path.join(d, 'video.gt.jsonl')))
        self.assertFalse(os.path.isfile(os.path.join(d, 'gt.jsonl')))

    def test_40_upload_never_overwrites(self):
        class FS:                                              # what Flask hands over: .filename, .save(path)
            def __init__(self, name, data): self.filename, self.data = name, data
            def save(self, path):
                with open(path, 'wb') as f: f.write(self.data)
        up = os.path.join(self.tmp, 'uploads')
        a = UL.save_upload(FS('My Drone.MP4', b'one'), up)
        b = UL.save_upload(FS('My Drone.MP4', b'two'), up)
        self.assertEqual(a, os.path.join(up, 'My_Drone.mp4'))
        self.assertEqual(b, os.path.join(up, 'My_Drone_2.mp4'))
        self.assertEqual(open(a, 'rb').read(), b'one')
        c = UL.save_upload(FS('Помилка оператора.avi', b'x'), up)              # any letters stay
        self.assertEqual(c, os.path.join(up, 'Помилка_оператора.avi'))

    def test_50_a_video_from_outside_is_copied_into_the_store(self):
        store = os.path.join(self.tmp, 'store50')
        saved = (UL.utils_sources.VIDEOS_DIR, UL.UPLOAD_DIR)
        self.addCleanup(lambda: (setattr(UL.utils_sources, 'VIDEOS_DIR', saved[0]), setattr(UL, 'UPLOAD_DIR', saved[1])))
        UL.utils_sources.VIDEOS_DIR, UL.UPLOAD_DIR = store, os.path.join(store, 'uploads')
        path = UL.resolve(self.video)
        self.assertEqual(path, os.path.join(store, 'uploads', 'moving_patch.mp4'))
        self.assertEqual(UL.resolve('uploads/moving_patch'), path)            # then it is in the list by its name
        self.assertEqual(UL.resolve(path), path)                              # a path inside the store: used as it is

    def test_60_jump_and_meet_rules(self):
        J = UL.LabelSession._jump
        B = lambda cx, cy: [cx - 10, cy - 20, cx + 10, cy + 20]                 # 20 x 40 box around a centre
        steady = [B(20 + 4 * t, 70) for t in range(5)]                          # 4 px / frame to the right
        self.assertEqual(J(steady, B(40, 70)), 0.0)                              # on course
        self.assertGreater(J(steady, B(70, 70)), UL.JUMP_REL)                    # 30 px off in one frame
        f5 = B(40, 64)                                                           # a swap sliding up over two frames:
        self.assertEqual(J(steady, f5), 0.0)                                     # each step is small ...
        self.assertGreater(J(steady[1:] + [f5], B(44, 54)), UL.JUMP2_REL)        # ... two of them are not

    def test_70_run_that_misses_the_key_frame_is_marked(self):
        S = self.session(model='NANO')
        for i in range(len(S.labels)):
            S.labels[i] = None
        S.set_box(0, truth(0))
        far = [200, 150, 240, 190]                                                # a key frame somewhere else
        S.set_box(20, far)
        S.start_tracking(0)
        self.wait_tracking(S)
        self.assertIn('does not meet the key frame 20', S.stop_reason)
        self.assertNotIn('check', S.labels[19])                                   # no marks: the stop says why, the cursor is there

    def test_80_back_tracking_fixes_where_the_run_left(self):
        S = self.session(model='NANO')
        off = lambda j: [v + 60 * (u % 2 == 0) for u, v in enumerate(truth(j))]      # 60 px to the right: another place

        def setup(bad_from):
            for i in range(len(S.labels)):
                S.labels[i] = None
            S.set_box(10, truth(10))
            for j in range(11, 30):                                                  # a forward run that left the patch at bad_from
                S.labels[j] = {'src': 'track', 'box': [float(v) for v in (off(j) if j >= bad_from else truth(j))]}
            S.set_box(30, truth(30))                                                 # the operator draws where it is plain wrong

        setup(20)
        self.assertTrue(S.start_tracking(30, forward=False)); self.wait_tracking(S)
        self.assertIn('frames 20–29 fixed', S.back_note)
        self.assertTrue(all(self._iou(S.labels[j]['box'], truth(j)) > 0.6 for j in range(20, 30)), S.back_note)
        self.assertEqual(S.labels[15]['box'], [float(v) for v in truth(15)])         # the right part of the run is kept
        S.undo()                                                                     # Ctrl+Z: the fix goes, the box stays
        self.assertEqual(S.labels[25]['box'], [float(v) for v in off(25)]); self.assertEqual(S.labels[30]['src'], 'manual')

        setup(99)                                                                    # the run was right all along
        S.start_tracking(30, forward=False); self.wait_tracking(S)
        self.assertIn('right', S.back_note)
        self.assertEqual(S.labels[25]['box'], [float(v) for v in truth(25)])

        setup(11)                                                                    # wrong from its first frame: fixed down to the key
        S.start_tracking(30, forward=False); self.wait_tracking(S)
        self.assertIn('frames 11–29 fixed', S.back_note)

        setup(20)                                                                    # Track from the drawn box: back, then forward
        S.start_tracking(30); self.wait_tracking(S)
        self.assertIn('frames 20–29 fixed', S.back_note)
        self.assertGreater(sum(1 for j in range(31, N) if S.labels[j]), 40, S.stop_reason)
        S.start_tracking(40)                                                         # from a tracked box: forward only
        self.wait_tracking(S)
        self.assertIsNone(S.back_note)

    def test_90_delete_all_is_guarded(self):
        S = self.session()
        for i in range(len(S.labels)):
            S.labels[i] = None
        S.set_box(5, truth(5)); S.set_absent(50)
        with self.assertRaises(ValueError):                                          # no / a wrong project name: nothing goes
            S.delete_all('other')
        self.assertEqual(S.counts()['labeled'], 2)
        self.assertEqual(S.delete_all(S.name), 2)
        self.assertEqual(S.counts()['labeled'], 0)
        self.assertEqual(open(S.gt_path).read(), '')
        kept = open(os.path.join(self.tmp, 'moving patch.gt.before_delete_all.jsonl')).read()   # the file as it was, after a restart too
        self.assertIn('"frame": 5', kept); self.assertIn('"frame": 50', kept)
        S.undo()                                                                     # Ctrl+Z: all back
        self.assertEqual(S.labels[5]['src'], 'manual'); self.assertEqual(S.labels[50]['src'], 'absent')

    def test_95_store_list_delete_thumb(self):
        store = os.path.join(self.tmp, 'store95')
        saved = {k: getattr(UL, k) for k in ('CACHE_DIR', 'TRASH_DIR', 'UPLOAD_DIR')}
        saved_root = UL.utils_sources.VIDEOS_DIR
        self.addCleanup(lambda: [setattr(UL, k, v) for k, v in saved.items()] + [setattr(UL.utils_sources, 'VIDEOS_DIR', saved_root)])
        UL.utils_sources.VIDEOS_DIR = store
        UL.CACHE_DIR, UL.TRASH_DIR, UL.UPLOAD_DIR = os.path.join(self.tmp, 'cache95'), os.path.join(store, '.trash'), os.path.join(store, 'uploads')
        os.makedirs(os.path.join(store, 'mine')); os.makedirs(os.path.join(store, 'set', 'seq'))
        for p in ('mine/a.mp4', 'mine/b.mp4', 'set/seq/seq.mp4'):                # copied in by hand: several per folder, any depth
            shutil.copyfile(self.video, os.path.join(store, p))
        open(os.path.join(store, 'set', 'seq', 'seq.gt.txt'), 'w').write('1,1,40,100,40,40,1\n1,2,0,0,9,9,1\n')
        names = [s['name'] for s in UL.video_sources()]
        self.assertEqual(sorted(names), ['mine/a', 'mine/b', 'set/seq'])
        S = UL.LabelSession(os.path.join(store, 'mine', 'b.mp4'), cache_dir=UL.CACHE_DIR)
        t0 = time.time()
        while S.total is None and time.time() - t0 < 30:
            time.sleep(0.05)
        self.assertEqual(S.name, 'mine/b')
        S.set_box(20, truth(20)); S.close()
        items = UL.video_sources()
        self.assertEqual(items[0]['name'], 'mine/b')                                 # labeled ones first
        self.assertIn('labels 1 / 90 frames', items[0]['sub'])
        self.assertIn('ground truth (MOT)', {s['name']: s for s in items}['set/seq']['sub'])
        seqs = {s['id']: s for s in B.sequences() if s['video'].startswith(store)}
        self.assertEqual((seqs['mine/b']['kind'], seqs['set/seq']['kind']), ('clip', 'mot'))   # both in the benchmark at once
        self.assertTrue(os.path.isfile(UL.thumb('mine/b')))                          # its first key frame, with the box
        r = UL.remove_source('mine/b')                                               # moved with its labels, never deleted
        self.assertEqual(sorted(os.listdir(r['to'])), ['b.gt.jsonl', 'b.label.json', 'b.mp4'])
        self.assertTrue(os.path.isfile(os.path.join(store, 'mine', 'a.mp4')))        # the neighbour stays
        r = UL.remove_source('set/seq')                                              # the only video of a folder: the folder goes
        self.assertEqual(sorted(os.listdir(r['to'])), ['seq.gt.txt', 'seq.mp4'])
        self.assertEqual([s['name'] for s in UL.video_sources()], ['mine/a'])
        with self.assertRaises(ValueError):
            UL.remove_source('no such video')

    @staticmethod
    def _iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        i = ix * iy
        return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


if __name__ == '__main__':
    unittest.main()
