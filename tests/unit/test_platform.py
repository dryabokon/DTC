"""utils_platform: what differs between the Pi (ARM) and an x86 laptop at run time; the benchmark's cancel flag."""
import os, subprocess, sys, tempfile, time, unittest
from tests.unit import _paths  # noqa: F401
import utils_platform as P


class TestPlatform(unittest.TestCase):
    def test_name(self):
        self.assertIn(P.name(), ('pi', 'arm', 'x86'))
        if P.IS_ARM:
            self.assertNotEqual(P.name(), 'x86')

    def test_pid_alive_self_and_garbage(self):
        self.assertTrue(P.pid_alive(os.getpid()))
        for bad in (None, '', 'x', 0, -1):
            self.assertFalse(P.pid_alive(bad))

    def test_pid_alive_does_not_kill(self):
        """On Windows os.kill(pid, 0) is not a check (it sends CTRL_C_EVENT) - the benchmark used it to ask 'is the worker alive?'."""
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **P.detached_kwargs())
        try:
            for _ in range(3):
                self.assertTrue(P.pid_alive(p.pid))
            time.sleep(0.3)
            self.assertIsNone(p.poll(), 'the child must still be running after pid_alive')
        finally:
            p.kill(); p.wait()
        self.assertFalse(P.pid_alive(p.pid))


class TestBenchCancelFlag(unittest.TestCase):
    def test_worker_sees_the_flag(self):
        try:
            import bench_worker
        except ImportError as e:                 # numpy / cv2 missing in this interpreter
            self.skipTest(str(e))
        import utils_bench as B
        w = bench_worker.Worker.__new__(bench_worker.Worker)
        w.d = tempfile.mkdtemp()
        w.cancelled = False
        self.assertFalse(w.cancelled)
        open(os.path.join(w.d, B.CANCEL_FLAG), 'w').close()
        self.assertTrue(w.cancelled)


class TestLocalHarness(unittest.TestCase):
    def test_default_source_like_the_launchers(self):
        sys.path.insert(0, _paths.REPO)
        from harness import local_app
        self.assertEqual(local_app.app_argv(''), ['--source', 'campus'])
        self.assertEqual(local_app.app_argv('--source oxford --do_detection true'), ['--source', 'oxford', '--do_detection', 'true'])
        self.assertEqual(local_app.app_argv('--list-sources'), ['--list-sources'])
        self.assertEqual(local_app._port_of(local_app.app_argv('--ui_port 8510')), 8510)
