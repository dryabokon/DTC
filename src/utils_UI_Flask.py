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
        self.viewers = 0                            # open /video_feed streams (browsers showing the Live tab)
        self.last_viewer_time = time.time()         # when the last one left (or the app started)
        self._viewers_lock = threading.Lock()
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
        @self.app.route('/sot_models', methods=['GET'])
        def sot_models():
            if self.P is None:
                return {'error': 'pipeline not ready'}, 503
            return {'current': self.P.SOT.model, 'models': self.P.SOT.available()}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/sot_model', methods=['POST'])
        def sot_model():
            data = request.get_json(silent=True) or {}
            if self.P is None:
                return {'ok': False, 'error': 'pipeline not ready'}, 503
            try:
                name = self.P.request_sot_model(data.get('model', ''))
            except ValueError as e:
                return {'ok': False, 'error': str(e)}, 400
            return {'ok': True, 'model': name}, 200
# ----------------------------------------------------------------------------------------------------------------------
        # SOT benchmark (utils_bench / bench_worker.py): Benchmark and Runs tabs
        @self.app.route('/bench/options', methods=['GET'])
        def bench_options():
            import utils_bench
            return utils_bench.options()
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/check', methods=['POST'])
        def bench_check():
            import utils_bench
            checks, p = utils_bench.readiness(request.get_json(silent=True) or {})
            ok = not any(c['status'] == 'fail' for c in checks)
            return {'ok': ok, 'checks': checks, 'tasks': len(p['runs']) if p else 0, 'frames': p['frames'] if p else 0,
                    'estimate_s': p['estimate_s'] if p else None, 'estimate': utils_bench.fmt_s(p['estimate_s']) if p else None,
                    'per_sequence': p['per_sequence'] if p else []}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/start', methods=['POST'])
        def bench_start():
            import utils_bench
            try:
                rid = utils_bench.start(request.get_json(silent=True) or {})
            except ValueError as e:
                return {'ok': False, 'error': str(e)}, 400
            return {'ok': True, 'id': rid}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/progress', methods=['GET'])
        def bench_progress():
            import utils_bench
            rid = request.args.get('id')
            p = utils_bench.progress(rid) if rid and rid in utils_bench.run_ids() else utils_bench.current(max_age=0)
            return {'progress': p}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/cancel', methods=['POST'])
        def bench_cancel():
            import utils_bench
            return {'ok': utils_bench.cancel((request.get_json(silent=True) or {}).get('id'))}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/runs', methods=['GET'])
        def bench_runs():
            import utils_bench
            return {'runs': utils_bench.runs_summary()}
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/run', methods=['GET'])
        def bench_run():
            import utils_bench
            d = utils_bench.run_detail(request.args.get('id', ''))
            return d if d is not None else ({'error': 'no such run'}, 404)
# ----------------------------------------------------------------------------------------------------------------------
        @self.app.route('/bench/series', methods=['GET'])
        def bench_series():
            import utils_bench
            s = utils_bench.task_series(request.args.get('id', ''), request.args.get('task', '0'))
            return s if s is not None else ({'error': 'no per-frame data (run made by an older version)'}, 404)
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
                self.key_thumb = request.form.get('thumb')       # Space: 32x18 grey thumbnail of the frame the browser showed
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
                    'sot_active': bool(self.P is not None and self.P.SOT.is_active), 'sot_model': self.P.SOT.model if self.P is not None else None,
                    'sot_ms': round(self.P.SOT.last_ms) if self.P is not None and self.P.SOT.is_active else None, 'sot_box_ratio': getattr(self.config, 'sot_box_ratio', 0.10),
                    'sot_stop_reason': self.P.SOT.stop_reason if self.P is not None else None,
                    'paused': bool(self.E is not None and self.E.is_paused()), 'bench_hold': bool(self.E is not None and self.E._bench_held),
                    'idle_hold': bool(self.E is not None and self.E._idle_held),
                    'can_pause': bool(self.E is not None and getattr(self.E.Grabber, 'can_pause', False)),
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
        with self._viewers_lock:
            self.viewers += 1
        try:
            yield from self._gen_frames()
        finally:                                    # the browser closed the stream (other tab, hidden window, closed)
            with self._viewers_lock:
                self.viewers -= 1
                self.last_viewer_time = time.time()
    # ----------------------------------------------------------------------------------------------------------------------
    def idle_for(self):
        """Seconds nobody has been watching the video (0 while somebody is)."""
        with self._viewers_lock:
            return 0.0 if self.viewers > 0 else time.time() - self.last_viewer_time
    # ----------------------------------------------------------------------------------------------------------------------
    def _gen_frames(self):

        self.fps = 0
        self.frame_id = 0
        self.start_frame, self.start_time = 0, time.time()
        last_id, last_sent = -1, 0.0
        while not self.stop_event.is_set():
            time.sleep(0.005)
            # encode only new frames; when nothing new comes (paused video) repeat the last one every 0.5 s
            if self.image_id == last_id and time.time() - last_sent < 0.5:
                continue
            last_id, last_sent = self.image_id, time.time()
            image = self.image if self.image is not None else tools_draw_numpy.random_noise(480, 640, (128, 128, 128))
            jpg = cv2.imencode('.jpg', image)[1].tobytes()
            # Content-Length: the browser shows a part as soon as it is complete. Without it a part is shown only when the
            # next boundary arrives, i.e. the screen lags one frame - visible on pause (the frame jumped after a click)
            yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpg)).encode() + b'\r\n\r\n' + jpg + b'\r\n'
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
