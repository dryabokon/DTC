"""Live click-to-track finds its weights whatever the current folder is (the app was started as "python src/main.py" from the
repo root: '../models/' pointed next to the repo, every model showed "(no weights)" and a click ran MIL)."""
import os, tempfile, unittest
from tests.unit import _paths  # noqa: F401


class TestSotModelsDir(unittest.TestCase):
    def test_default_models_dir_from_any_cwd(self):
        try:
            import utils_tracker_cand as C
        except ImportError as e:                 # numpy / cv2 missing in this interpreter
            self.skipTest(str(e))
        cwd = os.getcwd()
        for d in (_paths.REPO, tempfile.gettempdir()):
            os.chdir(d)
            try:
                T = C.Tracker_Live(model='NANO', models_dir='../models/')
                ok = {m['name']: m['ok'] for m in T.available()}
            finally:
                os.chdir(cwd)
            self.assertTrue(ok['NANO'] and ok['NCCF'] and ok['CAND'], f'cwd {d}: {ok}')
            self.assertEqual(os.path.normcase(T.models_dir), os.path.normcase(os.path.join(_paths.REPO, 'models')))


if __name__ == '__main__':
    unittest.main()
