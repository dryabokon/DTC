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
            self.P.update_ROI(current_mouse_pos)
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
                pass

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
        @self.app.route('/get_config', methods=['GET'])
        def get_config():
            return {'do_detection':self.config.do_detection,'do_classification':self.config.do_classification,'do_tracking':self.config.do_tracking,'do_profiling':self.config.do_profiling}
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
        while not self.stop_event.is_set():
            time.sleep(0.01)
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
        return
    # ----------------------------------------------------------------------------------------------------------------------
    def stop(self):
        self.stop_event.set()  # stops gen_frames loop
        if hasattr(self, "_server"):
            self._server.shutdown()  # stops the HTTP server
# ----------------------------------------------------------------------------------------------------------------------
