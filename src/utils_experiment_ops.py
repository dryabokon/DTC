import pandas as pd
import time
import os
import datetime
import threading
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
#----------------------------------------------------------------------------------------------------------------------
class Experimentor():
    def __init__(self,folder_runs,config,folder_output_current_experiment=None):
        self.config = config
        self.folder_runs = folder_runs
        self.folder_output_current_experiment = None
        self.config.folder_out = self.folder_output_current_experiment
        self.df_det_frame_global = pd.DataFrame([])

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

        return
    # ----------------------------------------------------------------------------------------------------------------------
    def setup_experiment(self):

        self.config.folder_out = self.folder_output_current_experiment

        if 'rtsp' in self.config.source:
            self.Grabber = utils_grabber_av.Grabber_AV(self.config.source,looped=False)
        else:
            self.Grabber =tools_grabber.Grabber(self.config.source,looped=False)


        if not (os.path.isfile(self.config.detection_model) or os.path.isdir(self.config.detection_model)):
            print('Model not found:', self.config.detection_model)
            self.config.detection_model = self.config.detection_model_fallback

        self.Perceptioner = utils_DTC_pipeline.Pipeliner(self.folder_output_current_experiment, self.config,self.Grabber)

        self.Saver = None
        if self.config.do_save:
            self.Saver = utils_video_saver.utils_video_saver(self.folder_output_current_experiment)

        self.UI = utils_UI_Flask.WebApp(self.config)
        self.UI.P = self.Perceptioner
        print('[  UI   ] webapp:', self.UI.get_connection_string())

        threading.Thread(target=self.UI.run_web_server).start()


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

        #self.Perceptioner.calc_benchmarks_custom()

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
            self.should_be_closed = True

        return
    # ----------------------------------------------------------------------------------------------------------------------

    def run_experiment(self):
        self.HB = tools_heartbeat.tools_HB()

        progress_bar = tqdm(total=self.Grabber.max_frame_id,bar_format="{l_bar}{bar}|{n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
        while not self.should_be_closed and not self.Grabber.is_finished():
            progress_bar.update(1)
            time.sleep(0.01)
            self.HB.do_heartbeat()
            self.grab_and_process()
            if self.UI is not None:
                self.process_key(self.UI.get_key())

            if self.UI is not None and self.UI.config_updated():
                self.config = self.UI.get_config()
                self.Perceptioner.update_config(self.config)
                self.UI.clear_config_update_status()
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def grab_and_process(self):

        self.TP.tic('grab')

        image = self.Grabber.get_frame()

        if image is not None:
            self.H, self.W = image.shape[:2]

        self.TP.tic('perception')
        df_det_frame = self.process_frame(image)

        if self.config.do_save:
            self.TP.tic('IO: save df_track2')
            self.df_det_frame_global = pd.concat([self.df_det_frame_global,df_det_frame],ignore_index=True)

        self.TP.tic('draw_debug_image')
        image = self.Perceptioner.draw_debug_image(image,df_det_frame)

        self.TP.tic('logging: print_debug_info')
        image = self.print_debug_info(image)

        if self.Saver is not None:
            self.TP.tic('logging: append frame')
            self.Saver.append_frame(image)

        self.TP.tic('UI')
        if self.config.do_display:
            self.UI.set_image(image)
        self.TP.tic('UI')

        return
    # ----------------------------------------------------------------------------------------------------------------------
