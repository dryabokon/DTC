import sys
import os
import warnings
warnings.filterwarnings("ignore")
# ----------------------------------------------------------------------------------------------------------------------
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(_this_dir, '..', 'tools'))
sys.path.append(os.path.join(_this_dir, 'configs'))
# ----------------------------------------------------------------------------------------------------------------------
import tools_IO
# ----------------------------------------------------------------------------------------------------------------------
from configs import config_pipe00
import utils_experiment_ops
# ----------------------------------------------------------------------------------------------------------------------
folder_runs = '../runs/'
# ----------------------------------------------------------------------------------------------------------------------
#config = config_pipe00.get_config_Oxford()
#config = config_pipe00.get_config_campus()
#config = config_pipe00.get_config_boats()
#config = config_pipe00.get_config_live_cam_PC()
config = config_pipe00.get_config_rtsp_stream()
# ----------------------------------------------------------------------------------------------------------------------
def construct_heavy_report(folder_output_current_experiment):
    E = utils_experiment_ops.Experimentor(folder_runs, config,folder_output_current_experiment)
    E.Perceptioner.update_pred(folder_output_current_experiment + 'df_track.csv')
    #E.Perceptioner.visualize_tracks_RAG()
    #E.Perceptioner.visualize_tracks_simple()
    E.Perceptioner.save_profiles()
    #E.Perceptioner.draw_sequence_recall_precision()
    #E.Perceptioner.calc_benchmarks_custom()
    return
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':

    E = utils_experiment_ops.Experimentor(folder_runs, config)
    E.run_experiment()
    E.close_experiment()
    E.construct_report()

    #construct_heavy_report(folder_runs + tools_IO.get_sub_folder_from_folder(folder_runs)[-1] + '/')