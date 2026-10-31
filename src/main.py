import sys
import warnings
warnings.filterwarnings("ignore")
# ----------------------------------------------------------------------------------------------------------------------
sys.path.append('../tools')
sys.path.append('./configs')
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
# every config field is also a CLI flag (config_base), e.g.:
#   python main.py --source oxford
#   python main.py --source rtsp://192.168.50.10:8554/stream --do_detection false
config = config_pipe00.get_config_campus()
#config = config_pipe00.get_config_boats()
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

    E = utils_experiment_ops.Experimentor(folder_runs, config)
    E.run_experiment()
    E.close_experiment()
    E.construct_report()
