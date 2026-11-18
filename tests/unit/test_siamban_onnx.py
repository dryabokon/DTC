"""SiamBAN-ONNX models (SBAN_<NAME>): folder discovery, and every model found in models/siamban_onnx/ follows a target."""
import json, os, shutil, tempfile, unittest
from tests.unit import _paths  # noqa: F401
import utils_tracker_sot as U
from tests.unit.test_tracker_sot import frame


def _ort():
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def folder(self, name, spec):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        if spec is not None:
            with open(os.path.join(d, 'model.json'), 'w') as f:
                f.write(spec if isinstance(spec, str) else json.dumps(spec))
        return d

    def test_every_folder_with_model_json_is_a_model(self):
        d = self.folder('mnv4', {'label': 'MNV4 int8', 'pi_ms': 33, 'threads': 2})
        self.folder('no_json', None)
        self.folder('broken', '{not json')
        m = U.find_onnx_models(self.root)
        self.assertEqual(list(m), ['SBAN_MNV4'])
        s = m['SBAN_MNV4']
        self.assertEqual((s['label'], s['pi_ms'], s['threads'], s['allow_spinning'], s['score_th']), ('MNV4 int8', 33.0, 2, False, None))
        self.assertEqual((s['backbone'], s['head']), (os.path.join(d, 'backbone.onnx'), os.path.join(d, 'head.onnx')))

    def test_custom_file_names(self):
        d = self.folder('eff', {'backbone': 'b_int8.onnx', 'head': 'h_int8.onnx'})
        s = U.find_onnx_models(self.root)['SBAN_EFF']
        self.assertEqual((s['backbone'], s['head']), (os.path.join(d, 'b_int8.onnx'), os.path.join(d, 'h_int8.onnx')))

    def test_missing_root_is_no_models(self):
        self.assertEqual(U.find_onnx_models(os.path.join(self.root, 'nope')), {})

    def test_found_models_are_selectable(self):
        for m in U.ONNX_MODELS:
            self.assertIn(m, U.SOT_MODELS)
            self.assertEqual(U.Tracker_SOT(model=m).weights(), [U.ONNX_MODELS[m]['backbone'], U.ONNX_MODELS[m]['head']])


@unittest.skipUnless(_ort(), 'onnxruntime not installed')
class TestFollow(unittest.TestCase):
    def test_every_model_follows_a_moving_target(self):
        if not U.ONNX_MODELS:
            self.skipTest('no models in models/siamban_onnx/')
        for m in U.ONNX_MODELS:
            with self.subTest(model=m):
                T = U.Tracker_SOT(model=m, models_dir=os.path.join(_paths.REPO, 'models'))
                if not all(os.path.isfile(f) for f in T.weights()):
                    self.skipTest(f'{m}: weights not found')
                x = 200
                self.assertTrue(T.start(frame((x, 240)), [x - 24, 240 - 16, x + 24, 240 + 16]))
                self.assertEqual(T.model, m)                                  # not the MIL fallback
                for _ in range(40):
                    x += 2; T.update(frame((x, 240)))
                self.assertTrue(T.is_active, f'{m}: {T.stop_reason}')
                self.assertLess(abs((T.box[0] + T.box[2]) / 2 - x), 8, f'{m}: box {T.box}, target x {x}')


if __name__ == '__main__':
    unittest.main()
