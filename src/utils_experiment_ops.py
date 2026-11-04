import cv2
import pandas as pd
import time
import os
import datetime
import threading
import collections
from tqdm import tqdm
#----------------------------------------------------------------------------------------------------------------------
import tools_IO
import tools_heartbeat
import tools_time_profiler
import tools_draw_numpy
import tools_DF
import tools_grabber
# ----------------------------------------------------------------------------------------------------------------------
import utils_video_saver
import utils_UI_Flask
import utils_DTC_pipeline
import utils_grabber_av
import utils_sources
#----------------------------------------------------------------------------------------------------------------------
class Experimentor():
    def __init__(self,folder_runs,config,folder_output_current_experiment=None):
        self.config = config
        self.folder_runs = folder_runs
        self.folder_output_current_experiment = None
        self.config.folder_out = self.folder_output_current_experiment
        self.df_det_frame_global = pd.DataFrame([])
        self.log_frames = []            # per-frame dfs, concatenated once in construct_report (was O(N^2) pd.concat per frame)
        self._last_frame_ts = None
        self._last_frame_time = time.time()
        self._no_signal_shown = False
        self._source_lock = threading.Lock()
        self._source_request = None
        self.source_segment = 0
        self._shown = collections.deque(maxlen=12)  # video files: (position, raw frame, thumbnail of what was displayed)

        self.HB = tools_heartbeat.tools_HB()
        self.TP = tools_time_profiler.Time_Profiler(verbose=False)
        self.H, self.W = None, None

        self.Grabber = None

        if folder_output_current_experiment is None:
            self.prepare_output_folder()
            self.setup_experiment()
        else:
            self.folder_output_current_experiment = folder_output_current_experiment
            self.Grabber = tools_grabber.Grabber(self.config.source, looped=False)
            self.Perceptioner = utils_DTC_pipeline.Pipeliner(self.folder_output_current_experiment, self.config,init_detectors=False,Grabber = self.Grabber)

        self.popup_message = None
        self.should_be_closed = False
        self._bench_held = False                    # a SOT benchmark runs: live processing holds (fair timings, see bench_hold)
        self._bench_paused_grabber = False
        self._idle_held = False                     # nobody watches the video: live processing holds (see idle_hold)
        self._idle_paused_grabber = False

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def setup_experiment(self):

        self.config.folder_out = self.folder_output_current_experiment
        self.Grabber = self.create_grabber(self.config.source)
        if not (os.path.isfile(self.config.detection_model) or os.path.isdir(self.config.detection_model)):
            print('Model not found:', self.config.detection_model)
            self.config.detection_model = self.config.detection_model_fallback

        self.Perceptioner = utils_DTC_pipeline.Pipeliner(self.folder_output_current_experiment, self.config,Grabber=self.Grabber)

        self.Saver = None
        if self.config.do_save:
            self.Saver = utils_video_saver.utils_video_saver(self.folder_output_current_experiment)

        self.UI = utils_UI_Flask.WebApp(self.config)
        self.UI.P = self.Perceptioner
        print('[  UI   ] webapp:', self.UI.get_connection_string())

        self.UI.E = self
        threading.Thread(target=self.UI.run_web_server, daemon=True).start()     # daemon: a crash must not leave the web server holding the port


        return
    # ----------------------------------------------------------------------------------------------------------------------
    def create_grabber(self, source):
        # realtime (default): files and URLs via PyAV - paced to the source FPS, files looped, wide frames downscaled.
        # --realtime false: files are read frame by frame as fast as possible, once, by tools_grabber (offline runs).
        # V4L2 cameras (/dev/videoN) via PyAV as MJPEG; webcam indices, image folders etc. go to tools_grabber.
        # Cameras (V4L2 devices and webcam indices) are mirrored left-right when config.camera_mirror (selfie view).
        src = str(source)
        is_url, is_file = '://' in src or utils_sources.is_device(src), os.path.isfile(src)
        mirror = getattr(self.config, 'camera_mirror', False) and (utils_sources.is_device(src) or src.isdigit())
        if is_url or (is_file and getattr(self.config, 'realtime', True)):
            return utils_grabber_av.Grabber_AV(src, looped=is_file, max_width=getattr(self.config, 'max_frame_width', None),
                                               camera_size=getattr(self.config, 'camera_size', '1280x720'), camera_fps=getattr(self.config, 'camera_fps', 30),
                                               mirror=mirror)
        G = tools_grabber.Grabber(source, looped=False)
        if mirror:
            get_frame = G.get_frame
            def get_frame_mirrored(*args, **kwargs):
                frame = get_frame(*args, **kwargs)
                return None if frame is None else cv2.flip(frame, 1)
            G.get_frame = get_frame_mirrored
        return G
    # ----------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def stop_grabber(G):
        if G is None: return
        if hasattr(G, 'stop'): G.stop()
        elif hasattr(G, 'stop_processing'): G.stop_processing()
    # ----------------------------------------------------------------------------------------------------------------------
    def current_source_name(self):
        return getattr(self.config, 'source_name', None) or str(self.config.source)
    # ----------------------------------------------------------------------------------------------------------------------
    def request_source(self, spec):
        """Called from the web UI thread. Validates the spec; the switch itself happens in the main loop."""
        name, source, gt = utils_sources.resolve_source(spec)      # raises ValueError for bad specs
        with self._source_lock:
            self._source_request = spec
        return name
    # ----------------------------------------------------------------------------------------------------------------------
    def switch_source(self, spec):
        name, source, gt = utils_sources.resolve_source(spec)
        print('[Source ] switching to', source)

        old = self.Grabber
        self.Grabber = self.create_grabber(source)
        if old is not None:
            self.stop_grabber(old)

        utils_sources.apply_to_config(self.config, spec)

        P = self.Perceptioner
        with P._sot_lock:
            P._sot_request = None
        P.SOT.stop('source switched')
        P.update_grabber(self.Grabber)
        P.Tracker = P.init_tracker()
        P.df_true = None
        P.update_config(self.config)

        if self.Saver is not None:          # new video file per source (frame size may differ)
            if self.Saver.writer_initialized:
                self.Saver.save_captured_video()
            else:                           # nothing was recorded (e.g. dead URL): drop the empty segment
                self.Saver.stop()
                if os.path.normpath(self.Saver.folder_output) != os.path.normpath(self.folder_output_current_experiment):
                    try:
                        os.rmdir(self.Saver.folder_output)
                    except OSError:
                        pass
            self.source_segment += 1
            safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)[:40]
            folder = os.path.join(self.folder_output_current_experiment, f'src{self.source_segment:02d}_{safe}/')
            os.makedirs(folder, exist_ok=True)
            self.Saver = utils_video_saver.utils_video_saver(folder)

        self._last_frame_ts = None
        self._last_frame_time = time.time()
        self._no_signal_shown = False
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def show_no_signal(self):
        import numpy, cv2
        H, W = (self.H, self.W) if self.H is not None else (480, 640)
        image = numpy.full((H, W, 3), 48, dtype=numpy.uint8)
        cv2.putText(image, 'Waiting for source:', (20, H // 2 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(image, str(self.config.source)[:80], (20, H // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 1, cv2.LINE_AA)
        if self.config.do_display:
            self.UI.set_image(image)
        return
    # ----------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def thumb(image):
        import cv2
        return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype('float32')
    # ----------------------------------------------------------------------------------------------------------------------
    def pause_on_seen_frame(self, thumb_csv):
        """The key reaches the Pi 1-2 frames after the operator saw the frame: go back to the frame whose thumbnail matches
        what the browser displayed (sent with the key), so the paused picture does not move and a click lands on it."""
        import numpy
        if not thumb_csv or not self._shown:
            return
        try:
            seen = numpy.array([float(v) for v in thumb_csv.split(',')], dtype='float32').reshape(36, 64)
        except ValueError:
            return
        time.sleep(0.06)                                   # let the grabber thread reach its pause wait
        errs = [float(numpy.abs(t - seen).mean()) for _, _, t in self._shown]
        k = int(numpy.argmin(errs))
        print('[ Video ] pause match errors (oldest..newest): ' + ' '.join(f'{e:.1f}' for e in errs), flush=True)
        if k != len(self._shown) - 1:
            pos, raw, _ = self._shown[k]
            self.Grabber.show(raw, pos)
            print(f'[ Video ] paused on the frame on screen: {len(self._shown) - 1 - k} frame(s) back', flush=True)
    # ----------------------------------------------------------------------------------------------------------------------
    def bench_hold(self):
        """While a SOT benchmark (bench_worker.py) runs on this machine the live pipeline holds: it would share the CPU and make
        the benchmark's timings wrong. Click-to-track stops, a video file pauses, the UI shows a notice. Returns True while holding."""
        import utils_bench
        bench = utils_bench.current()
        if bench is not None and not self._bench_held:
            self._bench_held = True
            self.Perceptioner.SOT.stop('benchmark started')
            if getattr(self.Grabber, 'can_pause', False) and not self.Grabber.paused:
                self.Grabber.set_paused(True)
                self._bench_paused_grabber = True
            print(f'[ Bench ] benchmark {bench["id"]} running - live processing holds', flush=True)
            self.show_notice('Benchmark ' + str(bench['id']) + ' is running', 'live video holds until it ends (fair timings) - see the Benchmark tab')
        elif bench is None and self._bench_held:
            self._bench_held = False
            if self._bench_paused_grabber:
                self.Grabber.set_paused(False)
                self._bench_paused_grabber = False
            print('[ Bench ] benchmark ended - live processing resumes', flush=True)
        if self._bench_held:
            time.sleep(0.2)
        return self._bench_held
    # ----------------------------------------------------------------------------------------------------------------------
    def idle_hold(self):
        """Nobody has watched the video for idle_pause_s (no browser on the Live tab, hidden window, UI closed): stop processing
        frames - the Pi stays cool and quiet. Never while click-to-track runs (its output may be used without anyone watching).
        A video file pauses on its frame; a camera / stream keeps being received but is not processed. Off: --idle_pause_s 0."""
        limit = float(getattr(self.config, 'idle_pause_s', 0) or 0)
        idle = limit > 0 and self.UI is not None and self.UI.idle_for() >= limit and not self.Perceptioner.SOT.is_active
        if idle and not self._idle_held:
            self._idle_held = True
            if getattr(self.Grabber, 'can_pause', False) and not self.Grabber.paused:
                self.Grabber.set_paused(True)
                self._idle_paused_grabber = True
            print(f'[ Idle  ] nobody watches the video for {limit:.0f} s - live processing holds', flush=True)
        elif not idle and self._idle_held:
            self._idle_held = False
            if self._idle_paused_grabber:
                self.Grabber.set_paused(False)
                self._idle_paused_grabber = False
            self._last_frame_time = time.time()              # no "no signal" screen for the time we held
            print('[ Idle  ] a viewer is back - live processing resumes', flush=True)
        if self._idle_held:
            time.sleep(0.1)
        return self._idle_held
    # ----------------------------------------------------------------------------------------------------------------------
    def show_notice(self, title, text):
        import numpy, cv2
        H, W = (self.H, self.W) if self.H is not None else (480, 640)
        image = numpy.full((H, W, 3), 48, dtype=numpy.uint8)
        cv2.putText(image, title, (20, H // 2 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(image, text[:90], (20, H // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
        if self.config.do_display:
            self.UI.set_image(image)
    # ----------------------------------------------------------------------------------------------------------------------
    def is_paused(self):
        return bool(getattr(self.Grabber, 'paused', False))
    # ----------------------------------------------------------------------------------------------------------------------
    def _paused_needs_frame(self):
        """While paused, a click (or a cancel) is applied to the frame on screen: the same frame is processed once more."""
        P = self.Perceptioner
        with P._sot_lock:
            return P._sot_request is not None
    # ----------------------------------------------------------------------------------------------------------------------
    def status(self):
        """Machine-readable state for the test harness (/status)."""
        P = getattr(self, 'Perceptioner', None)
        t0 = getattr(self, 't_start', None)
        return {'run_folder': os.path.abspath(self.folder_output_current_experiment) if self.folder_output_current_experiment else None,
                'source': str(self.config.source), 'source_name': self.current_source_name(),
                'frame_size': [self.W, self.H] if self.W else None,
                'frames': getattr(self, 'frames_processed', 0), 'uptime_s': round(time.time() - t0, 1) if t0 else 0.0,
                'fps': round(float(self.HB.get_fps()), 2) if getattr(self, 'frames_processed', 0) else 0.0,
                'no_signal': bool(self._no_signal_shown),
                'paused': self.is_paused(), 'can_pause': bool(getattr(self.Grabber, 'can_pause', False)),
                'video_pos': getattr(self.Grabber, 'position', None),
                'do_detection': bool(self.config.do_detection), 'do_tracking': bool(self.config.do_tracking), 'do_classification': bool(self.config.do_classification),
                'sot_active': bool(P is not None and P.SOT.is_active), 'sot_box': [int(v) for v in P.SOT.box] if P is not None and P.SOT.box is not None else None,
                'sot_stop_reason': P.SOT.stop_reason if P is not None else None,
                'sot_model': P.SOT.model if P is not None else None, 'bench_hold': self._bench_held, 'idle_hold': self._idle_held,
                'sot_ms': round(P.SOT.last_ms, 1) if P is not None and P.SOT.is_active else None}
    # ----------------------------------------------------------------------------------------------------------------------
    def close_experiment(self):

        if self.UI is not None:
            self.UI.stop()

        if self.Saver is not None:
            self.Saver.save_captured_video()

        self.stop_grabber(self.Grabber)

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def print_debug_info(self,image, brief_info=False,font_size=16):
        if image is None:
            return image
        image = image.copy()

        frame_id = self.HB.get_frame_id()
        delta_time = self.HB.get_delta_time()
        fps = self.HB.get_fps()

        light_bg = image[:127, :200].mean() > 127
        clr_bg = (255, 255, 255) if light_bg else (32, 32, 32)
        color_fg = (32, 32, 32) if light_bg else (255, 255, 255)
        space = font_size + 2

        msg = '%06d | %.1f sec | %.1f fps @ %dp' % (frame_id, delta_time, fps, image.shape[0])
        image = tools_draw_numpy.draw_text_super_fast(image, msg,(0, space * 1), color_fg=color_fg,clr_bg=clr_bg, font_size=font_size)
        return image
    # ----------------------------------------------------------------------------------------------------------------------
    def prepare_output_folder(self,recover_last_experiment=False):
        os.makedirs(self.folder_runs, exist_ok=True)
        numbered = sorted(int(d) for d in os.listdir(self.folder_runs) if d.isdigit() and os.path.isdir(os.path.join(self.folder_runs, d)))

        if recover_last_experiment and numbered:
            self.folder_output_current_experiment = os.path.join(self.folder_runs, '%04d' % numbered[-1]) + '/'
        else:
            self.folder_output_current_experiment = os.path.join(self.folder_runs, '%04d' % ((numbered[-1] + 1) if numbered else 1)) + '/'
            os.makedirs(self.folder_output_current_experiment)

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def construct_report(self):
        print('Constructing report ...')

        if len(self.log_frames) > 0:
            self.df_det_frame_global = pd.concat(self.log_frames, ignore_index=True)
        self.df_det_frame_global.to_csv(self.folder_output_current_experiment + 'df_track.csv', index=False,header=True, float_format='%.2f')

        with open(self.folder_output_current_experiment + 'df_config.csv', 'w') as f:
            df_config = pd.DataFrame([(k, v) for k, v in zip(*self.config.get_keys_values())if k != 'parser'], columns=['param', 'value'])
            f.write(tools_DF.prettify(df_config))

        self.config.save_as_python_script(self.folder_output_current_experiment+'config.py',class_name='config')
        self.TP.stage_stats(self.folder_output_current_experiment + 'df_time.csv')
        self.Perceptioner.TP.stage_stats(self.folder_output_current_experiment + 'df_time_perception.csv')
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def process_frame(self,image):
        if not self.config.do_detection:
            return None

        self.Perceptioner.HB.do_heartbeat()
        self.Perceptioner.process_frame(image,do_debug=False)
        df_det_frame = self.Perceptioner.df_det_frame

        return df_det_frame
    # ----------------------------------------------------------------------------------------------------------------------
    def process_key(self,key):
        if key is None: return
        #self.UI.is_config_updated = True

        if key in ['Escape','esc']:
            if self.Perceptioner.SOT.is_active:         # first Escape only stops click-to-track
                self.Perceptioner.cancel_sot()
            else:
                self.should_be_closed = True

        if key in ['c','C','Delete']:
            self.Perceptioner.cancel_sot()

        # pre-recorded video only (cameras / RTSP / simulator keep running): pause to aim at a small target, then resume
        if key in ['Space', ' '] and getattr(self.Grabber, 'can_pause', False):
            pausing = not self.Grabber.paused
            self.Grabber.set_paused(pausing)
            if pausing:
                self.pause_on_seen_frame(getattr(self.UI, 'key_thumb', None))
            print('[ Video ] ' + ('paused' if self.Grabber.paused else 'playing'), flush=True)

        if key == 'ArrowRight' and self.is_paused():
            self.Grabber.step()

        if key in ['r', 'R', 'Home'] and getattr(self.Grabber, 'can_pause', False):     # video from the start (stays paused if paused)
            self.Perceptioner.cancel_sot()
            self.Perceptioner.Tracker = self.Perceptioner.init_tracker()                # tracks of the old pass would continue into frame 1
            self.Grabber.restart()
            print('[ Video ] from the start', flush=True)

        return
    # ----------------------------------------------------------------------------------------------------------------------

    def run_experiment(self):
        self.HB = tools_heartbeat.tools_HB()

        progress_bar = tqdm(total=self.Grabber.max_frame_id,bar_format="{l_bar}{bar}|{n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
        self.t_start, self.frames_processed = time.time(), 0
        max_s, max_f = float(getattr(self.config, 'max_seconds', 0) or 0), int(getattr(self.config, 'max_frames', 0) or 0)
        while not self.should_be_closed and not self.Grabber.is_finished():
            if (max_s and time.time() - self.t_start >= max_s) or (max_f and self.frames_processed >= max_f):
                print(f'[  RUN  ] limit reached: {time.time() - self.t_start:.1f} s, {self.frames_processed} frames', flush=True)
                break
            if self.UI is not None:
                self.process_key(self.UI.get_key())
            if self.bench_hold() or self.idle_hold():
                continue

            with self._source_lock:
                source_request, self._source_request = self._source_request, None
            if source_request is not None:
                try:
                    self.switch_source(source_request)
                except Exception as e:
                    print('[Source ] switch failed:', e)

            # process each grabbed frame once: skip until the grabber delivers a new one
            frame_ts = getattr(self.Grabber, 'frame_ts', 'n/a')
            if frame_ts != 'n/a' and (frame_ts is None or frame_ts == self._last_frame_ts):
                paused = self.is_paused()
                if not (paused and frame_ts is not None and self._paused_needs_frame()):
                    if not paused and not self._no_signal_shown and time.time() - self._last_frame_time > 2.0:
                        self.show_no_signal()
                        self._no_signal_shown = True
                    time.sleep(0.002)
                    continue
            self._last_frame_ts = frame_ts
            self._last_frame_time = time.time()
            self._no_signal_shown = False

            progress_bar.update(1)
            self.HB.do_heartbeat()
            self.grab_and_process()
            self.frames_processed += 1

            if self.UI is not None and self.UI.config_updated():
                self.config = self.UI.get_config()
                self.Perceptioner.update_config(self.config)
                self.UI.clear_config_update_status()
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def sot_to_log(self, df_sot):
        df = df_sot.copy()
        df.insert(0, 'frame_id', int(self.HB.get_frame_id()))
        df['lifetime'] = None
        df['time'] = (datetime.datetime.fromtimestamp(time.time()).strftime('%H:%M:%S.%f'))[:-4]
        df['class_ids'] = None
        df['class_name'] = 'SOT'
        return df[['frame_id', 'track_id', 'lifetime', 'time', 'x1', 'y1', 'x2', 'y2', 'conf', 'class_ids', 'class_name']]
    # ----------------------------------------------------------------------------------------------------------------------
    def grab_and_process(self):

        self.TP.tic('grab')

        image = self.Grabber.get_frame()
        raw, pos = image, getattr(self.Grabber, 'position', None)

        if image is not None:
            self.H, self.W = image.shape[:2]

        self.TP.tic('perception')
        df_sot = self.Perceptioner.sot_step(image)
        if df_sot is not None:
            # SOT mode: tracker only, detector / MOT / heavy drawing are bypassed
            df_det_frame = self.sot_to_log(df_sot)
            self.TP.tic('draw_debug_image')
            image = self.Perceptioner.draw_sot(image, df_sot)
        else:
            df_det_frame = self.process_frame(image)
            self.TP.tic('draw_debug_image')
            image = self.Perceptioner.draw_debug_image(image,df_det_frame)

        self.TP.tic('logging: print_debug_info')
        image = self.print_debug_info(image)

        if self.config.do_save:
            self.TP.tic('IO: save df_track2')
            if df_det_frame is None or df_det_frame.empty:
                df_det_frame = pd.DataFrame(columns=['frame_id','track_id','lifetime','time','x1','y1','x2','y2','conf','class_ids','class_name','x','y','z','x_trg','y_trg','z_trg','roll','pitch','yaw'])
                df_det_frame.loc[0] = [int(self.HB.get_frame_id()), None, None, (datetime.datetime.fromtimestamp(time.time()).strftime('%H:%M:%S.%f'))[:-4], None, None, None, None, None, None, None,None, None, None, None, None, None,None, None, None]

            self.log_frames.append(df_det_frame)

        if self.Saver is not None:
            self.TP.tic('logging: append frame')
            self.Saver.append_frame(image)

        self.TP.tic('UI')
        if self.config.do_display:
            if raw is not None and getattr(self.Grabber, 'can_pause', False):
                self._shown.append((pos, raw, self.thumb(image)))   # raw is not drawn on (draw_* return copies)
            self.UI.set_image(image)
        self.TP.tic('UI')

        return
    # ----------------------------------------------------------------------------------------------------------------------
