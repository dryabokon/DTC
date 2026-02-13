from DL import config_base
# ----------------------------------------------------------------------------------------------------------------------
class cnfg_common(config_base.cnfg_base):
    exp_name = 'default'
    source = None

    resize_ratio = None
    start = 0
    limit = None
    gt = None
    iou_th = 0.25
    min_object_size = 10


    do_detection = False
    detection_model = 'yolov8n.pt'

    #imgsz = (640, 640)
    detection_model_desc = 'default model'
    confidence_th = None

    do_tracking = False
    tracking_model = 'BOXMOT'
    #tracking_model = 'DEEPSORT'
    track_lifetime = 2

    do_classification = False
    classification_model = 'yolo'
    classification_confidence_th = 0.5

    do_profiling = False

    host_mlflow = None
    port_mlflow = None
    remote_storage_folder = None

    do_save = True
    do_display = True