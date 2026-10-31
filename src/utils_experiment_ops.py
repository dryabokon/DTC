import pandas as pd
import time
import os
import datetime
import threading
#----------------------------------------------------------------------------------------------------------------------
import tools_IO
import tools_heartbeat
import tools_time_profiler
import tools_draw_numpy
import tools_DF
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
        self.df_det_frame_empty = pd.DataFrame({'class_ids': [], 'class_name': [],'track_id':[],'lifetime':[], 'x1': [], 'y1': [], 'x2': [], 'y2': [], 'conf': []})
        self.df_det_frame_global = pd.DataFrame([])
        self.log_frames = []            # per-frame dfs, concatenated once in construct_report (was O(N^2) pd.concat per frame)
        self._last_frame_ts = None
        self._last_frame_time = time.time()
        self._no_signal_shown = False
        self._source_lock = threading.Lock()
        self._source_request = None
        self.source_segment = 0

        self.HB = tools_heartbeat.tools_HB()
        self.TP = tools_time_profiler.Time_Profiler(verbose=False)
        self.H, self.W = None, None

        self.Grabber = None

        if folder_output_current_experiment is None:
            self.prepare_output_folder()
            self.setup_experiment()
        else:
            self.folder_output_current_experiment = folder_output_current_experiment

        self.df_det_frame = self.df_det_frame_empty
        self.popup_message = None
        self.should_be_closed = False

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def setup_experiment(self):

        self.config.folder_out = self.folder_output_current_experiment
        self.Grabber = self.create_grabber(self.config.source)
        if not (os.path.isfile(self.config.detection_model) or os.path.isdir(self.config.detection_model)):
            print('Model not found:', self.config.detection_model)
            self.config.detection_model = self.config.detection_model_fallback

        self.Perceptioner = utils_DTC_pipeline.Pipeliner(self.folder_output_current_experiment, self.config,self.Grabber)

        self.Saver = None
        if self.config.do_save:
            self.Saver = utils_video_saver.utils_video_saver(self.folder_output_current_experiment)

        self.UI = utils_UI_Flask.WebApp(self.config)
        self.UI.P = self.Perceptioner
        self.UI.E = self
        threading.Thread(target=self.UI.run_web_server).start()
        print('[  UI   ] webapp:',self.UI.get_connection_string())

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def create_grabber(self, source):
        return utils_grabber_av.Grabber_AV(source, max_width=getattr(self.config, 'max_frame_width', None))
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
            old.stop()

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
    def close_experiment(self):

        if self.UI is not None:
            self.UI.stop()

        if self.Saver is not None:
            self.Saver.save_captured_video()

        if self.Grabber is not None:
            self.Grabber.stop()

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
        if not os.path.exists(self.folder_runs):
            os.mkdir(self.folder_runs)

        if recover_last_experiment:
            self.folder_output_current_experiment = tools_IO.get_sub_folder_from_folder(self.folder_runs)[-1]
        else:
            self.folder_output_current_experiment = tools_IO.get_next_folder_out(self.folder_runs)
            os.mkdir(self.folder_output_current_experiment)

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def construct_report(self):
        print('Constructing report ...')

        if len(self.log_frames) > 0:
            self.df_det_frame_global = pd.concat(self.log_frames, ignore_index=True)
        self.df_det_frame_global.to_csv(self.folder_output_current_experiment + 'df_track2.csv', index=False,header=True, float_format='%.2f')

        with open(self.folder_output_current_experiment + 'df_config.csv', 'w') as f:
            df_config = pd.DataFrame([(k, v) for k, v in zip(*self.config.get_keys_values())if k != 'parser'], columns=['param', 'value'])
            f.write(tools_DF.prettify(df_config))

        self.config.save_as_python_script(self.folder_output_current_experiment+'config.py')
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

        df_det_frame['frame_id'] = int(self.HB.get_frame_id())
        df_det_frame = tools_DF.add_column(df_det_frame, 'time', time.time(), pos=1)
        df_det_frame['time'] = df_det_frame['time'].apply(lambda x: (datetime.datetime.fromtimestamp(x).strftime('%H:%M:%S.%f'))[:-4])
        df_det_frame = df_det_frame[['frame_id', 'track_id','lifetime','time', 'x1', 'y1', 'x2', 'y2', 'conf', 'class_ids', 'class_name']]

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

        return
    # ----------------------------------------------------------------------------------------------------------------------

    def run_experiment(self):
        self.HB = tools_heartbeat.tools_HB()

        while not self.should_be_closed:
            if self.UI is not None:
                self.process_key(self.UI.get_key())

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
                if not self._no_signal_shown and time.time() - self._last_frame_time > 2.0:
                    self.show_no_signal()
                    self._no_signal_shown = True
                time.sleep(0.002)
                continue
            self._last_frame_ts = frame_ts
            self._last_frame_time = time.time()
            self._no_signal_shown = False

            self.HB.do_heartbeat()
            self.grab_and_process()

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
            self.UI.set_image(image)
        self.TP.tic('UI')

        return
    # ----------------------------------------------------------------------------------------------------------------------
