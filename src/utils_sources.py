import os
import sys
import glob
import time
# ----------------------------------------------------------------------------------------------------------------------
# Video source registry shared by the CLI (--source) and the web UI dropdown.
# A source spec can be: a preset alias, a listed name (e.g. 'v10/boats10b'), a file path, or a URL (rtsp://, http://, ...).
# ----------------------------------------------------------------------------------------------------------------------
IMAGES_DIR = '../images'
DATASETS_DIR = '../datasets/videos'          # public datasets built by `dtc data prepare` (local, not in git): <set>/<sequence>/<sequence>.mp4
VIDEO_EXT = ('.mp4', '.avi', '.mkv', '.mov')
ALIASES = {
    'campus':  'v00/PETS09-S2L1',
    'oxford':  'v01/TownCentreXVID',
    'boats_a': 'v10/boats10a',
    'boats_b': 'v10/boats10b',
    'drone_ir': 'v30/IR',                    # Anti-UAV thermal sequence 640x512, one small drone, gt.txt
}
# not offered in the web UI dropdown (too many sources there); still usable by name / alias, by the benchmark and the tests
HIDDEN_IN_UI = {'v10/boats10a', 'v10/boats10b', 'v21/uav0000117_02622_v', 'v22/uav0000137_00458_v', 'v30/IR',
                'antiuav_val/new29_train-new'}
# ----------------------------------------------------------------------------------------------------------------------
def _gt_for(video_path):
    folder = os.path.dirname(video_path)
    gt = os.path.join(folder, 'gt.txt')
    videos = [f for f in os.listdir(folder) if f.lower().endswith(VIDEO_EXT)]
    return gt if (os.path.isfile(gt) and len(videos) == 1) else None
# ----------------------------------------------------------------------------------------------------------------------
_win_cameras = {'t': -1e9, 'value': []}
def _list_cameras_windows(max_age=60.0):
    """Windows: camera devices (PnP class Camera / Image) as [('dshow://<name>', name)] - opened by name through DirectShow
    (utils_grabber_av, like /dev/videoN on the Pi). Asking takes ~1 s, so the answer is cached."""
    import subprocess
    if time.time() - _win_cameras['t'] < max_age:
        return _win_cameras['value']
    titles = []
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_PnPEntity -Filter \"PNPClass='Camera' OR PNPClass='Image'\" | "
                              "Where-Object { $_.Status -eq 'OK' } | ForEach-Object { $_.Name }"],
                             capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        titles = [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        pass
    _win_cameras.update(t=time.time(), value=[('dshow://' + t, t) for t in titles])
    return _win_cameras['value']
# ----------------------------------------------------------------------------------------------------------------------
def list_cameras():
    """Cameras: [(name, device, title)], e.g. ('camera', '/dev/video0', 'HD Pro Webcam C920').
    Linux (Pi): USB/V4L2, only the capture node of each camera (/dev/v4l/by-id/*-video-index0), re-scanned on every call (hot-plug).
    Windows (x86 laptop): DirectShow devices by name, e.g. ('camera', 'dshow://Integrated Camera', 'Integrated Camera')."""
    if sys.platform == 'win32':
        return [('camera' if i == 0 else f'camera{i}', device, title) for i, (device, title) in enumerate(_list_cameras_windows())]
    result = []
    for link in sorted(glob.glob('/dev/v4l/by-id/*-video-index0')):
        device = os.path.realpath(link)
        try:
            with open(f'/sys/class/video4linux/{os.path.basename(device)}/name') as fh:
                title = fh.read().strip()
        except OSError:
            title = os.path.basename(link)
        result.append((device, title))
    return [('camera' if i == 0 else f'camera{i}', device, title) for i, (device, title) in enumerate(result)]
# ----------------------------------------------------------------------------------------------------------------------
_picamera2 = None
SYSTEM_SITE = '/usr/lib/python3/dist-packages'
def import_picamera2():
    """The picamera2 module, or None (not a Pi / not installed). picamera2 comes with Raspberry Pi OS (python3-picamera2)
    and is not pip-installable together with its libcamera bindings, so when the venv lacks it the system dist-packages
    are appended to sys.path (same Python 3.11; the venv's own packages such as numpy stay first). Imported once."""
    global _picamera2
    if _picamera2 is None:
        _picamera2 = False
        if sys.platform.startswith('linux'):
            try:
                import picamera2
                _picamera2 = picamera2
            except ImportError:
                if os.path.isdir(SYSTEM_SITE) and SYSTEM_SITE not in sys.path:
                    sys.path.append(SYSTEM_SITE)
                    try:
                        import picamera2
                        _picamera2 = picamera2
                    except Exception as e:
                        print(f'picamera2 not usable: {e}')
            except Exception as e:
                print(f'picamera2 not usable: {e}')
    return _picamera2 or None
# ----------------------------------------------------------------------------------------------------------------------
def list_csi_cameras():
    """Embedded cameras (Camera Module on the Pi's CSI connectors): [(name, 'csi://N', title)], e.g. ('picam', 'csi://0', 'imx708').
    libcamera also lists USB cameras; those are skipped here (they come as /dev/videoN through list_cameras)."""
    picamera2 = import_picamera2()
    if picamera2 is None:
        return []
    try:
        info = picamera2.Picamera2.global_camera_info()
    except Exception:
        return []
    result = [(f"csi://{c.get('Num', i)}", str(c.get('Model', 'camera'))) for i, c in enumerate(info) if 'usb' not in str(c.get('Id', '')).lower()]
    return [('picam' if i == 0 else f'picam{i}', url, title) for i, (url, title) in enumerate(result)]
# ----------------------------------------------------------------------------------------------------------------------
def is_csi(spec):
    return str(spec).startswith('csi://')
# ----------------------------------------------------------------------------------------------------------------------
def csi_number(spec):
    return int(str(spec).split('://', 1)[1] or 0)
# ----------------------------------------------------------------------------------------------------------------------
def is_device(spec):
    """A local camera read by utils_grabber_av: V4L2 node (Linux / Pi) or DirectShow device (Windows)."""
    return str(spec).startswith(('/dev/video', 'dshow://'))
# ----------------------------------------------------------------------------------------------------------------------
def list_sources(images_dir=IMAGES_DIR):
    """Ordered list of dicts {name, label, source, gt}: embedded (CSI) and USB cameras first, then every video file under images_dir."""
    alias_of = {v: k for k, v in ALIASES.items()}
    result = [{'name': name, 'label': f'{name}: embedded camera {title} ({url})', 'source': url, 'gt': None} for name, url, title in list_csi_cameras()]
    result += [{'name': name, 'label': f'{name}: {title} ({device})', 'source': device, 'gt': None} for name, device, title in list_cameras()]
    for path in sorted(glob.glob(os.path.join(images_dir, '**', '*'), recursive=True)):
        if not path.lower().endswith(VIDEO_EXT):
            continue
        rel = os.path.relpath(path, images_dir).replace('\\', '/')
        name = os.path.splitext(rel)[0]
        alias = alias_of.get(name)
        result.append({'name': alias or name, 'key': name,
                       'label': f'{alias} ({rel})' if alias else rel,
                       'source': path.replace('\\', '/'),
                       'gt': _gt_for(path)})
    for path in sorted(glob.glob(os.path.join(DATASETS_DIR, '*', '*', '*'))):     # one video per sequence folder
        if not path.lower().endswith(VIDEO_EXT):
            continue
        rel = os.path.relpath(os.path.dirname(path), DATASETS_DIR).replace('\\', '/')     # e.g. visdrone/uav0000086_00000_v
        result.append({'name': rel, 'key': rel, 'label': f'{rel} (dataset)' + (' [gt]' if _gt_for(path) else ''),
                       'source': path.replace('\\', '/'), 'gt': _gt_for(path)})
    return result
# ----------------------------------------------------------------------------------------------------------------------
def is_url(spec):
    return '://' in str(spec)
# ----------------------------------------------------------------------------------------------------------------------
def resolve_source(spec, images_dir=IMAGES_DIR):
    """spec -> (name, source, gt). Raises ValueError if it cannot be resolved."""
    spec = str(spec).strip()
    if spec == '':
        raise ValueError('empty source')

    for s in list_sources(images_dir):
        if spec in (s['name'], ALIASES.get(spec)) or spec == os.path.splitext(os.path.relpath(s['source'], images_dir))[0].replace('\\', '/'):
            return s['name'], s['source'], s['gt']

    if is_url(spec):
        return spec, spec, None

    if is_device(spec) and (os.path.exists(spec) or spec.startswith('dshow://')):
        return next((n for n, d, t in list_cameras() if d == spec), os.path.basename(spec)), spec, None

    if spec.isdigit():                       # webcam index: /dev/videoN on Linux, the Nth camera on Windows (both PyAV), else tools_grabber
        if os.path.exists(f'/dev/video{spec}'):
            return resolve_source(f'/dev/video{spec}', images_dir)
        cams = list_cameras() if sys.platform == 'win32' else []
        if int(spec) < len(cams):
            return cams[int(spec)][0], cams[int(spec)][1], None
        return f'webcam {spec}', spec, None

    if os.path.isdir(spec):                  # folder of images, read by tools_grabber
        return os.path.basename(os.path.normpath(spec)), spec, None

    if os.path.isfile(spec):
        return os.path.splitext(os.path.basename(spec))[0], spec, _gt_for(spec)

    names = ', '.join(s['name'] for s in list_sources(images_dir))
    raise ValueError(f"unknown source '{spec}'. Use a URL, a file path, a folder, a webcam index or one of: {names}")
# ----------------------------------------------------------------------------------------------------------------------
def apply_to_config(config, spec, images_dir=IMAGES_DIR):
    name, source, gt = resolve_source(spec, images_dir)
    config.source = source
    config.gt = gt
    config.source_name = name
    config.exp_name = ('[live] ' if is_url(source) or is_device(source) or str(source).isdigit() else '[offline] ') + name
    return config
# ----------------------------------------------------------------------------------------------------------------------
