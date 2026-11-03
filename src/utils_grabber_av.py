import av
import cv2
import os
import threading
import time
# ----------------------------------------------------------------------------------------------------------------------
class Grabber_AV:
    def __init__(self, url: str, *, stale_s: float = 0.7, looped: bool = False, max_width=None, camera_size='1280x720', camera_fps=30, mirror=False):
        self.url = url
        self.mirror = mirror                            # left-right flip (selfie view), applied in the decoder thread
        self.camera_size = camera_size
        self.camera_fps = camera_fps
        self.max_width = max_width
        self.stale_s = stale_s
        self.looped = looped
        self.is_file = os.path.isfile(url)

        self.max_frame_id = None
        self._lock = threading.Lock()
        self._frame = None
        self._frame_ts = 0.0
        self._finished = threading.Event()
        self._paused = False                    # files only: playback holds the current frame
        self._step = False                      # while paused: release exactly one more frame
        self.stop_event = threading.Event()
        threading.Thread(target=self._run_file if self.is_file else self._run_stream, daemon=True).start()
        return

    # ------------------------------------------------------------------------------------------------------------------
    def _open_stream(self):
        if self.url.startswith('/dev/video'):           # V4L2 camera: MJPEG gives 720p/1080p at 30 fps (raw YUYV only 10/5 fps)
            container = av.open(self.url, mode="r", format="v4l2",
                                options={"input_format": "mjpeg", "video_size": self.camera_size, "framerate": str(self.camera_fps)})
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            return container, stream
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
    def _to_bgr(self, frame):
        # downscale in the decoder thread (swscale) so the main loop never sees oversized frames
        if self.max_width and frame.width > self.max_width:
            h = int(round(frame.height * self.max_width / frame.width / 2.0)) * 2
            img = frame.to_ndarray(width=int(self.max_width), height=h, format="bgr24")
        else:
            img = frame.to_ndarray(format="bgr24")
        return cv2.flip(img, 1) if self.mirror else img

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
                        img = self._to_bgr(frame)
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

                        img = self._to_bgr(frame)
                        if self._paused and not self._step:          # hold the frame on screen until resumed / stepped
                            while self._paused and not self._step and not self.stop_event.is_set():
                                time.sleep(0.02)
                            t0 = time.time() - frame_idx * interval  # keep pacing from here, no catch-up burst
                        self._step = False
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

            if not self.looped:
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
    def frame_ts(self):
        # timestamp of the latest decoded frame (changes once per new frame)
        with self._lock:
            return self._frame_ts if self._frame is not None else None

    # ------------------------------------------------------------------------------------------------------------------
    @property
    def can_pause(self):
        return self.is_file

    @property
    def paused(self):
        return self._paused

    def set_paused(self, paused):
        if self.can_pause:
            self._paused = bool(paused)
        return self._paused

    def step(self):
        """While paused: show the next frame and stay paused."""
        if self._paused:
            self._step = True

    # ------------------------------------------------------------------------------------------------------------------
    def is_finished(self):
        return self._finished.is_set()

    # ------------------------------------------------------------------------------------------------------------------
    def stop(self):
        self.stop_event.set()
        return
# ----------------------------------------------------------------------------------------------------------------------