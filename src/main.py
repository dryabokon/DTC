import sys
import warnings
warnings.filterwarnings("ignore")
# ----------------------------------------------------------------------------------------------------------------------
sys.path.append('../tools')
sys.path.append('./configs')
# ----------------------------------------------------------------------------------------------------------------------
from configs import config_pipe00
import utils_experiment_ops
# ----------------------------------------------------------------------------------------------------------------------
folder_runs = '../runs/'
# ----------------------------------------------------------------------------------------------------------------------
#config = config_pipe00.get_config_campus()
config = config_pipe00.get_config_boats()
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':

    E = utils_experiment_ops.Experimentor(folder_runs, config)
    E.run_experiment()
    E.close_experiment()
    E.construct_report()