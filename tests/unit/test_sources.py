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
        cams = [i for i, n in enumerate(names) if n.startswith('camera')]
        if cams:
            self.assertEqual(cams, list(range(len(cams))))


if __name__ == '__main__':
    unittest.main()
