import numpy
import cv2
import sys
import time
import logging
import threading
from flask import Flask, render_template, Response, request
from werkzeug.serving import make_server
import socket
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
#----------------------------------------------------------------------------------------------------------------------
sys.path.append('./tools')
#----------------------------------------------------------------------------------------------------------------------
import tools_draw_numpy
#----------------------------------------------------------------------------------------------------------------------
class WebApp:
    def __init__(self,config):
        self.stop_event  = threading.Event()
        self.folder_out = './output/'
        self.config = config
        self.is_config_updated = False

        self.app = Flask(__name__)
        self.app.logger.disabled = True
        self.target_fps = 10
        self.image = numpy.full((480, 640, 3), 127, dtype=numpy.uint8)
        self.image_id = 0
        self.P = None
        self.E = None
        self.setup_routes()
        self._key_lock = threading.Lock()
        self.key = None
        self.sidebar_visible = True
        return
#----------------------------------------------------------------------------------------------------------------------
    def setup_routes(self):
        @self.app.route('/')
        def index():
            return render_template('index.html')
#----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/video_feed')
        def video_feed():
            return Response(self.gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')
#----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/mouse_click', methods=['POST'])
        def mouse_click():
            x = request.form.get('x')
            y = request.form.get('y')
            current_mouse_pos = [int(float(x)), int(float(y))]
            if self.P is None:
                return 'Pipeline not ready', 503
            box = None
            if request.form.get('x1') is not None:      # box drawn / sized in the browser (frame pixels)
                box = [int(float(request.form.get(k))) for k in ('x1', 'y1', 'x2', 'y2')]
            self.P.update_ROI(current_mouse_pos, box)
            return 'Success', 200
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/sources', methods=['GET'])
        def sources():
            import utils_sources
            current = self.E.current_source_name() if self.E is not None else None
            return {'current': current, 'current_path': str(self.config.source),
                    'sources': [{'name': s['name'], 'label': s['label']} for s in utils_sources.list_sources()]}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/switch_source', methods=['POST'])
        def switch_source():
            data = request.get_json(silent=True) or {}
            spec = str(data.get('source', '')).strip()
            if self.E is None:
                return {'ok': False, 'error': 'pipeline not ready'}, 503
            try:
                name = self.E.request_source(spec)
            except ValueError as e:
                return {'ok': False, 'error': str(e)}, 400
            return {'ok': True, 'name': name}, 200
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/cancel_sot', methods=['POST'])
        def cancel_sot():
            if self.P is not None:
                self.P.cancel_sot()
            return 'Success', 200
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/button_takeoff', methods=['POST'])
        def button_takeoff():
            with self._key_lock:
                self.key = 't'
            return 'Success', 200
    # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/button_mission1', methods=['POST'])
        def button_mission1():
            with self._key_lock:
                self.key = 'm'
            return 'Success', 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/button_land', methods=['POST'])
        def button_land():
            with self._key_lock:
                self.key = 'l'
            return 'Success', 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/button_stop', methods=['POST'])
        def button_stop():
            with self._key_lock:
                self.key = 'Clear'
            return 'Success', 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/button_home', methods=['POST'])
        def button_home():
            with self._key_lock:
                self.key = 'h'
            return 'Success', 200
    # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/toggle_sidebar', methods=['POST'])
        def toggle_sidebar():
            self.sidebar_visible = not self.sidebar_visible
            return {'status': 'success', 'visible': self.sidebar_visible}
    # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/key_press', methods=['POST'])
        def key_press():
            with self._key_lock:
                self.key = request.form.get('key')
            if self.key == 'Escape' or self.key == 'esc':
                print(f'[  UI   ] Escape from {request.remote_addr} ({request.headers.get("User-Agent", "?")[:60]})')

            return 'Success', 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/update_do_detection', methods=['POST'])
        def update_do_detection():
            self.config.do_detection = (request.json.get('enabled'))
            if not self.config.do_detection:
                self.config.do_classification = False
                self.config.do_tracking = False

            self.is_config_updated = True
            return 'Success', 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/update_do_classification', methods=['POST'])
        def update_do_classification():
            self.config.do_classification = (request.json.get('enabled'))
            if self.config.do_classification:
                self.config.do_detection = True

            self.is_config_updated = True
            return 'Success', 200

        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/update_do_tracking', methods=['POST'])
        def update_do_tracking():
            self.config.do_tracking = (request.json.get('enabled'))
            if self.config.do_tracking:
                self.config.do_detection = True

            self.is_config_updated = True
            return 'Success', 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/status', methods=['GET'])
        def status():
            return self.E.status() if self.E is not None else ({'error': 'pipeline not ready'}, 503)
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/shutdown', methods=['POST'])
        def shutdown():
            # graceful stop: the main loop ends, the video and the report are written (Esc stops click-to-track first)
            if self.E is None:
                return {'ok': False}, 503
            self.E.should_be_closed = True
            return {'ok': True}, 200
        # ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/get_config', methods=['GET'])
        def get_config():
            return {'do_detection':self.config.do_detection,'do_classification':self.config.do_classification,'do_tracking':self.config.do_tracking,'do_profiling':self.config.do_profiling,
                    'sot_active': bool(self.P is not None and self.P.SOT.is_active), 'sot_box_ratio': getattr(self.config, 'sot_box_ratio', 0.10),
                    'sot_stop_reason': self.P.SOT.stop_reason if self.P is not None else None,
                    'sot_stop_age': (time.time() - self.P.SOT.stop_time) if self.P is not None and self.P.SOT.stop_reason else None}
        # ----------------------------------------------------------------------------------------------------------------------
    def get_key(self):
        with self._key_lock:
            result = self.key
            self.key = None
        return result
    # ----------------------------------------------------------------------------------------------------------------------
    def get_config(self):
        return self.config
    # ----------------------------------------------------------------------------------------------------------------------
    def config_updated(self):
        return self.is_config_updated
    # ----------------------------------------------------------------------------------------------------------------------
    def clear_config_update_status(self):
        self.is_config_updated = False
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def gen_frames(self):

        self.fps = 0
        self.frame_id = 0
        self.start_frame, self.start_time = 0, time.time()
        last_id = -1
        while not self.stop_event.is_set():
            time.sleep(0.005)
            if self.image_id == last_id:       # encode only new frames (was: re-encode every 10 ms)
                continue
            last_id = self.image_id
            image = self.image if self.image is not None else tools_draw_numpy.random_noise(480, 640, (128, 128, 128))
            yield (b'--frame\r\n'b'Content-Type: image/jpeg\r\n\r\n' + cv2.imencode('.jpg', image)[1].tobytes() + b'\r\n')
    # ----------------------------------------------------------------------------------------------------------------------
    def run_web_server(self):
        self._server = make_server('0.0.0.0', 8509, self.app,threaded=True)
        self._server.serve_forever()
    # ----------------------------------------------------------------------------------------------------------------------
    def run_web_server_old(self):
        self.app.run(host='0.0.0.0', port=8509, debug=False)
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def get_local_ip(self):
        ip = '127.0.0.1'
        # s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # try:
        #     s.connect(("8.8.8.8", 80))
        #     ip = s.getsockname()[0]
        # finally:
        #     s.close()
        return ip
    # ----------------------------------------------------------------------------------------------------------------------
    def get_connection_string(self):
        return f'http://{self.get_local_ip()}:8509/'
    # ----------------------------------------------------------------------------------------------------------------------
    def set_image(self,image):
        self.image = image
        self.image_id += 1
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def stop(self):
        self.stop_event.set()  # stops gen_frames loop
        if hasattr(self, "_server"):
            self._server.shutdown()  # stops the HTTP server
# ----------------------------------------------------------------------------------------------------------------------
