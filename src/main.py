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
import utils_sources
# ----------------------------------------------------------------------------------------------------------------------
# --list-sources must be handled before the config is built (config_base parses sys.argv and rejects unknown flags)
if '--list-sources' in sys.argv:
    for s in utils_sources.list_sources():
        print(f"{s['name']:22s} {s['source']}" + ('   [gt]' if s['gt'] else ''))
    sys.exit(0)
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

    # resolve aliases / listed names / paths / URLs; picks gt.txt next to the video unless --gt was given explicitly
    gt_explicit = any(a == '--gt' or a.startswith('--gt=') for a in sys.argv)
    gt_value = config.gt
    try:
        utils_sources.apply_to_config(config, config.source)
    except ValueError as e:
        print('Error:', e)
        sys.exit(2)
    if gt_explicit:
        config.gt = gt_value
    print('[Source ]', config.source)
    if getattr(config, 'imgsz_hw', ''):            # rectangular input (e.g. NCNN exported at 256x416); the CLI cannot pass a tuple to imgsz
        config.imgsz = tuple(int(v) for v in str(config.imgsz_hw).split(','))

    E = utils_experiment_ops.Experimentor(folder_runs, config)
    E.run_experiment()
    E.close_experiment()
    E.construct_report()

    #construct_heavy_report(folder_runs + tools_IO.get_sub_folder_from_folder(folder_runs)[-1] + '/')
