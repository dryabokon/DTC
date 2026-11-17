"""utils_sources: what --source / the UI dropdown accept."""
import os, unittest
from tests.unit import _paths  # noqa: F401
import utils_sources as S


class TestSources(unittest.TestCase):
    def test_aliases_resolve_to_files(self):
        for alias in ('campus', 'oxford', 'boats_a', 'boats_b'):
            name, source, gt = S.resolve_source(alias)
            self.assertEqual(name, alias)
            self.assertTrue(os.path.isfile(source), source)

    def test_campus_has_ground_truth(self):
        self.assertTrue(S.resolve_source('campus')[2].endswith('gt.txt'))

    def test_url_passes_through(self):
        self.assertEqual(S.resolve_source('rtsp://10.0.0.1:8554/x'), ('rtsp://10.0.0.1:8554/x',) * 2 + (None,))

    def test_unknown_source_is_an_error_listing_names(self):
        with self.assertRaises(ValueError) as e:
            S.resolve_source('no-such-thing')
        self.assertIn('campus', str(e.exception))

    def test_empty_is_an_error(self):
        with self.assertRaises(ValueError):
            S.resolve_source('  ')

    def test_apply_to_config_marks_live_and_offline(self):
        class C: pass
        c = C(); S.apply_to_config(c, 'campus'); self.assertTrue(c.exp_name.startswith('[offline]'))
        c = C(); S.apply_to_config(c, 'rtsp://h/x'); self.assertTrue(c.exp_name.startswith('[live]'))

    def test_list_sources_has_presets_first_cameras_before_files(self):
        names = [s['name'] for s in S.list_sources()]
        self.assertIn('campus', names)
        cams = [i for i, n in enumerate(names) if n.startswith(('camera', 'picam'))]
        if cams:
            self.assertEqual(cams, list(range(len(cams))))


    def test_store_names_and_ground_truth_next_to_the_video(self):
        import shutil, tempfile
        root = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, root, True)
        def touch(rel, text=''):
            p = os.path.join(root, *rel.split('/')); os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w', encoding='utf-8').write(text); return p
        touch('pile/a.mp4'); touch('pile/a.gt.jsonl', '{"frame": 0, "box": [1, 2, 11, 22]}\n'); touch('pile/b.avi')
        touch('set/seq/seq.mp4'); touch('set/seq/seq.gt.txt', '1,1,1,2,10,20,1\n')
        touch('old/x/video.mp4'); touch('old/x/gt.jsonl')                       # the older layout of a sim recording
        touch('.trash/gone.mp4'); touch('_downloads/z.mp4')                     # not scanned
        v = {x['name']: [k for _, k in x['gt']] for x in S.list_videos(root)}
        self.assertEqual(v, {'old/x': ['one'], 'pile/a': ['one'], 'pile/b': [], 'set/seq': ['mot']})
        t = S.gt_table(os.path.join(root, 'pile', 'a.gt.jsonl'))               # the Live overlay reads both kinds as MOT rows
        self.assertEqual(t.values.tolist(), [[1, 1, 1.0, 2.0, 10.0, 20.0, 1.0]])

    def test_embedded_camera_spec(self):
        self.assertTrue(S.is_csi('csi://0')); self.assertFalse(S.is_csi('/dev/video0'))
        self.assertEqual(S.csi_number('csi://1'), 1)
        class C: pass
        c = C(); S.apply_to_config(c, 'csi://0'); self.assertTrue(c.exp_name.startswith('[live]'))
        for name, url, title in S.list_csi_cameras():          # only on a Pi with a Camera Module attached
            self.assertTrue(name.startswith('picam') and S.is_csi(url))

if __name__ == '__main__':
    unittest.main()
