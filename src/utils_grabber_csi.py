import time
import utils_grabber_av
import utils_sources
# ----------------------------------------------------------------------------------------------------------------------
# Embedded camera of the Raspberry Pi (Camera Module on the CSI connector) via picamera2 / libcamera.
# Source spec: csi://N, N = libcamera camera number (utils_sources.list_csi_cameras lists them as 'picam', 'picam1', ...).
# picamera2 is imported by utils_sources.import_picamera2 (system package, see there).
# ----------------------------------------------------------------------------------------------------------------------
class Grabber_CSI(utils_grabber_av.Grabber_AV):
    """Same interface as Grabber_AV for a live stream (latest frame, stale frames dropped, re-open on errors),
    frames captured by picamera2 as BGR at camera_size / camera_fps. Not mirrored: an onboard camera looks outward."""

    def _run_stream(self):
        picamera2 = utils_sources.import_picamera2()
        if picamera2 is None:
            print('Grabber_CSI: picamera2 is not available')
            return
        w, h = (int(v) for v in str(self.camera_size).lower().split('x'))
        cam = None
        while not self.stop_event.is_set():
            try:
                if cam is None:
                    cam = picamera2.Picamera2(utils_sources.csi_number(self.url))
                    # 'RGB888' in picamera2 is B,G,R byte order per pixel = what OpenCV expects
                    cfg = cam.create_video_configuration(main={'size': (w, h), 'format': 'RGB888'}, buffer_count=4,
                                                         controls={'FrameRate': float(self.camera_fps)})
                    cam.configure(cfg)
                    cam.start()
                img = cam.capture_array('main')
                with self._lock:
                    self._frame = img
                    self._frame_ts = time.time()
            except Exception as e:
                print(f'Grabber_CSI error: {e}')
                try:
                    if cam is not None:
                        cam.close()
                except Exception:
                    pass
                cam = None
                time.sleep(1.0)
        try:
            if cam is not None:
                cam.close()
        except Exception:
            pass
        return
# ----------------------------------------------------------------------------------------------------------------------
