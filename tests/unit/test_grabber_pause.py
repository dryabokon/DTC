"""Grabber_AV pause / step / resume on a video file (Space and -> in the web UI)."""
import os, time, unittest
from tests.unit import _paths
import utils_grabber_av as G

VIDEO = os.path.join(_paths.REPO, 'images', 'v00', 'PETS09-S2L1.mp4')


@unittest.skipUnless(os.path.isfile(VIDEO), 'campus video not found')
class TestPause(unittest.TestCase):
    def setUp(self):
        self.g = G.Grabber_AV(VIDEO, looped=True)
        t0 = time.time()
        while self.g.frame_ts is None and time.time() - t0 < 5:
            time.sleep(0.05)

    def tearDown(self):
        self.g.stop()

    def test_a_file_can_pause_a_stream_cannot(self):
        self.assertTrue(self.g.can_pause)
        s = G.Grabber_AV('rtsp://127.0.0.1:9/none'); self.assertFalse(s.can_pause); self.assertFalse(s.set_paused(True)); s.stop()

    def test_pause_holds_the_frame(self):
        self.g.set_paused(True); time.sleep(0.3)
        ts = self.g.frame_ts; time.sleep(0.5)
        self.assertEqual(ts, self.g.frame_ts, 'a new frame was published while paused')
        self.assertIsNotNone(self.g.get_frame(), 'paused file must still return its frame')

    def test_step_releases_exactly_one_frame(self):
        self.g.set_paused(True); time.sleep(0.3)
        ts = self.g.frame_ts
        self.g.step(); time.sleep(0.4)
        ts1 = self.g.frame_ts
        self.assertNotEqual(ts, ts1); time.sleep(0.4)
        self.assertEqual(ts1, self.g.frame_ts, 'more than one frame after a step')
        self.assertTrue(self.g.paused)

    def test_resume_plays_at_source_rate(self):
        self.g.set_paused(True); time.sleep(0.5); self.g.set_paused(False)
        seen, last, t0 = 0, self.g.frame_ts, time.time()
        while time.time() - t0 < 1.0:
            if self.g.frame_ts != last:
                seen += 1; last = self.g.frame_ts
            time.sleep(0.005)
        self.assertGreater(seen, 15); self.assertLess(seen, 40)        # ~25 fps, no catch-up burst after the pause

    def test_restart_goes_back_to_the_first_frame(self):
        time.sleep(1.5)
        self.assertGreater(self.g.position, 20)
        self.g.restart(); time.sleep(0.3)
        self.assertLess(self.g.position, 12, 'not back at the start')

    def test_restart_while_paused_shows_frame_1_and_stays_paused(self):
        time.sleep(1.0); self.g.set_paused(True); time.sleep(0.3)
        self.g.restart(); time.sleep(0.6)
        self.assertEqual(self.g.position, 1)
        self.assertTrue(self.g.paused)
        p = self.g.position; time.sleep(0.5)
        self.assertEqual(p, self.g.position, 'moved on while paused')


if __name__ == '__main__':
    unittest.main()
