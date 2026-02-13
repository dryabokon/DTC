from config_common import cnfg_common
# ----------------------------------------------------------------------------------------------------------------------
def get_config_campus():
    class cnfg(cnfg_common):
        exp_name = '[offline] campus'
        source = '../images/v00/PETS09-S2L1.mp4'
        gt     = '../images/v00/gt.txt'

        do_detection = True
        do_tracking = True
        do_classification = True
        do_profiling = False

    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
def get_config_Oxford():
    class cnfg(cnfg_common):
        exp_name = '[offline] Oxford'
        source = '../images/v01/TownCentreXVID.mp4'

        do_detection = True
        do_tracking = True
        do_classification = True
        do_profiling = False

    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
def get_config_boats():
    class cnfg(cnfg_common):
        exp_name = '[offline] boats'
        source = '../images/v10/boats10a.mp4'

        do_detection = True
        do_tracking = True
        do_classification = True
        do_profiling = False

        detection_model = '../models/yolov11x_marine_v8.pt'
        detection_model_desc = 'yolo fine-tune'

    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
def get_config_live_cam_PC():
    class cnfg(cnfg_common):
        exp_name = '[live] PC cam'
        source = 0
        do_detection = True
        do_tracking = True
        do_classification = True
        do_profiling = False

        detection_model = '../models/yolov8n.pt'
        detection_model_desc = 'Airborne'
        imgsz = (384, 640)



    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
def get_config_rtsp_stream():
    class cnfg(cnfg_common):
        exp_name = '[live] udp://127.0.0.1:5000'
        source = 'rtsp://127.0.0.1:8554/stream'

        resize_ratio = 0.5
        do_detection = False
        do_tracking = False
        do_classification = False

    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
def get_config_youtube():
    class cnfg(cnfg_common):
        exp_name = '[live] YouTube'
        #source = 'https://www.youtube.com/watch?v=IG6hIkxq0JU'     #CZ
        #source = 'https://www.youtube.com/watch?v=xLax2gCihh0'     #CZ
        #source = 'https://www.youtube.com/watch?v=bBubIPZYVt0'      #Aquarium
        #source = 'https://www.youtube.com/watch?v=EbVlhVeD3jA'        #Bayfront
        source = 'https://www.youtube.com/watch?v=zTVWJ3Mc0Ag'  #Silver Bay

        do_detection = False
        do_tracking = False
        do_classification = False
    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
def get_config_empty():
    class cnfg(cnfg_common):
        exp_name = '[offline] empty'
        detection_model = 'Detectron'
        detection_model_desc = 'Detectron'
        source = None
        do_detection = False
        do_tracking = False
        do_classification = False
    return cnfg()
# ----------------------------------------------------------------------------------------------------------------------
