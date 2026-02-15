import av
import os
import threading
import time
# ----------------------------------------------------------------------------------------------------------------------
class Grabber_AV:
    def __init__(self, url: str, *, stale_s: float = 0.7, loop: bool = False):
        self.url = url
        self.stale_s = stale_s
        self.loop = loop
        self.is_file = os.path.isfile(url)

        self._lock = threading.Lock()
        self._frame = None
        self._frame_ts = 0.0
        self._finished = threading.Event()
        self.stop_event = threading.Event()
        threading.Thread(target=self._run_file if self.is_file else self._run_stream, daemon=True).start()
        return

    # ------------------------------------------------------------------------------------------------------------------
    def _open_stream(self):
        options = {
            "rtsp_transport": "tcp",
            "stimeout": "2000000",
            "fflags": "nobuffer",
            "flags": "low_delay",
            "max_delay": "0",
            "probesize": "32",
            "analyzeduration": "0",
        }
        container = av.open(self.url, mode="r", options=options)
        stream = next(s for s in container.streams if s.type == "video")
        stream.thread_type = "AUTO"
        stream.codec_context.skip_frame = "NONREF"
        return container, stream

    # ------------------------------------------------------------------------------------------------------------------
    def _open_file(self):
        container = av.open(self.url, mode="r")
        stream = next(s for s in container.streams if s.type == "video")
        stream.thread_type = "AUTO"
        fps = float(stream.average_rate or stream.guessed_rate or 25)
        return container, stream, fps

    # ------------------------------------------------------------------------------------------------------------------
    def _run_stream(self):
        container, stream = None, None
        last_ok = time.time()

        while not self.stop_event.is_set():
            try:
                if container is None:
                    container, stream = self._open_stream()
                    last_ok = time.time()

                for packet in container.demux(stream):
                    if self.stop_event.is_set():
                        break
                    for frame in packet.decode():
                        img = frame.to_ndarray(format="bgr24")
                        now = time.time()
                        with self._lock:
                            self._frame = img
                            self._frame_ts = now
                        last_ok = now

                raise RuntimeError("Demux ended")

            except Exception:
                try:
                    if container is not None:
                        container.close()
                except Exception:
                    pass
                container, stream = None, None
                time.sleep(0.2)

            if (time.time() - last_ok) > self.stale_s:
                try:
                    if container is not None:
                        container.close()
                except Exception:
                    pass
                container, stream = None, None
                time.sleep(0.2)
        return

    # ------------------------------------------------------------------------------------------------------------------
    def _run_file(self):
        while not self.stop_event.is_set():
            try:
                container, stream, fps = self._open_file()
                interval = 1.0 / fps
                t0 = time.time()
                frame_idx = 0

                for packet in container.demux(stream):
                    if self.stop_event.is_set():
                        break
                    for frame in packet.decode():
                        if self.stop_event.is_set():
                            break

                        img = frame.to_ndarray(format="bgr24")
                        now = time.time()
                        with self._lock:
                            self._frame = img
                            self._frame_ts = now

                        # Pace playback to match source FPS
                        frame_idx += 1
                        target = t0 + frame_idx * interval
                        sleep_time = target - time.time()
                        if sleep_time > 0:
                            time.sleep(sleep_time)

                container.close()

            except Exception as e:
                print(f"Grabber_AV file error: {e}")

            if not self.loop:
                self._finished.set()
                break

        return

    # ------------------------------------------------------------------------------------------------------------------
    def get_frame(self, max_age_s: float = 0.5):
        with self._lock:
            if self._frame is None:
                return None
            if not self.is_file and (time.time() - self._frame_ts) > max_age_s:
                return None
            return self._frame.copy()

    # ------------------------------------------------------------------------------------------------------------------
    @property
    def finished(self):
        """True when a local file has been fully played (non-looping mode)."""
        return self._finished.is_set()

    # ------------------------------------------------------------------------------------------------------------------
    def stop(self):
        self.stop_event.set()
        return
# ----------------------------------------------------------------------------------------------------------------------