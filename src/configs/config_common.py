from DL import config_base
# ----------------------------------------------------------------------------------------------------------------------
class cnfg_common(config_base.cnfg_base):
    exp_name = 'default'
    source = None
    realtime = True                             # True: files paced to source FPS and looped (PyAV); False: read once, as fast as possible (offline runs)
    camera_size = '1280x720'                    # V4L2 cameras (/dev/videoN, MJPEG): capture size and fps
    camera_fps = 30
    camera_mirror = True                        # cameras (/dev/videoN, webcam index) flipped left-right, like a mirror; --camera_mirror false: as captured
    folder_runs = '../runs/sessions/'           # every start writes runs/sessions/NNNN/; the test harness points it elsewhere
    max_seconds = 0                             # stop by itself after this many seconds / processed frames (0 = run until Esc / UI shutdown)
    max_frames = 0
    max_frame_width = 1280                      # frames wider than this are downscaled by the grabber (e.g. 1080p -> 1280x720)

    resize_ratio = None
    start = 0
    limit = None
    gt = None
    iou_th = 0.25
    min_object_size = 10

    do_detection = False
    detection_model_fallback = 'yolov8n.pt'
    detection_model = detection_model_fallback
    #detection_model = '../models/yolov8n_ncnn_model'

    imgsz = 640
    imgsz_hw = ''                               # 'H,W' for rectangular models, e.g. --imgsz_hw 256,416 (overrides imgsz)
    detection_model_desc = 'default model'
    confidence_th = None

    do_tracking = False
    #tracking_model = 'BOXMOT'
    tracking_model = 'DEEPSORT'
    track_lifetime = 2

    # click-to-track single-object tracking (SOT): detector/MOT are bypassed while SOT is active
    sot_tracking_model = 'NANO'                 # 'NANO' (cv2.TrackerNano) | 'MIL'
    sot_models_dir = '../models/nanotrack/'
    sot_box_ratio = 0.10                        # ROI on click: this fraction of frame width and height
    sot_score_th = 0.30                         # below this NanoTrack score the frame counts as "lost"
    sot_lost_th = 15                            # consecutive lost frames before SOT is cancelled
    sot_lost_edge_frames = 8                    # box touching the frame border this many frames in a row -> target left the frame (0 = off)
    sot_lost_appearance = 0.60                  # appearance similarity (median over sot_lost_window) below this x start value -> lost (0 = off)
    sot_lost_window = 30                        # frames
    sot_lost_empty = 0.35                       # box contrast below this x start contrast for 5 frames -> lost (target gone from sky/water) (0 = off)

    do_classification = False
    classification_model = 'yolo'
    classification_confidence_th = 0.5

    do_profiling = False

    host_mlflow = None
    port_mlflow = None
    remote_storage_folder = None

    do_save = True
    do_display = True