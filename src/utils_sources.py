import os
import sys
import glob
import time
# ----------------------------------------------------------------------------------------------------------------------
# Video source registry shared by the CLI (--source), the web UI (Live dropdown, Label list) and the benchmark.
# A source spec can be: a preset alias, a listed name (e.g. 'boats/boats10b'), a file path, or a URL (rtsp://, http://, ...).
#
# One store for every video: videos/ (not in git) - ours, public datasets, uploads, sim recordings alike. Copy a video
# anywhere under it (any depth, several per folder) and it is listed. Its ground truth lies next to it, same name:
#   a.mp4  a.gt.jsonl   one object: {"frame": i (0-based), "box": [x1, y1, x2, y2] | null, ...} per line (Label tab, sim, ...)
#   a.mp4  a.gt.txt     MOT rows frame (1-based),id,x,y,w,h,... - many objects
#   a.mp4  a.label.json what the Label tab knows about its labels (tracker, counts)
# The older layout - the only video of a folder with gt.jsonl / gt.txt beside it - still counts.
# Folders starting with '.' or '_' (.trash, _downloads) are not scanned.
# ----------------------------------------------------------------------------------------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEOS_DIR = os.path.join(REPO, 'videos')
VIDEO_EXT = ('.mp4', '.avi', '.mkv', '.mov')
GT_ONE, GT_MOT, LABEL_META = '.gt.jsonl', '.gt.txt', '.label.json'
ALIASES = {
    'campus':  'mot15/PETS09-S2L1',
    'oxford':  'oxford/TownCentreXVID',
    'boats_a': 'boats/boats10a',
    'boats_b': 'boats/boats10b',
    'drone_ir': 'antiuav/IR',                # Anti-UAV thermal sequence 640x512, one small drone
}
# not offered in the Live dropdown (too many sources there); still usable by name / alias, by the benchmark and the tests
HIDDEN_IN_UI = {'boats/boats10a', 'boats/boats10b', 'visdrone/uav0000117_02622_v', 'visdrone/uav0000137_00458_v', 'antiuav/IR',
                'antiuav_val/new29_train-new'}
# ----------------------------------------------------------------------------------------------------------------------
def sidecar(video, suffix):
    """videos/x/a.mp4 + '.gt.jsonl' -> videos/x/a.gt.jsonl"""
    return os.path.splitext(video)[0] + suffix
# ----------------------------------------------------------------------------------------------------------------------
def _alone_in_folder(video):
    try:
        return sum(1 for f in os.listdir(os.path.dirname(video) or '.') if f.lower().endswith(VIDEO_EXT)) == 1
    except OSError:
        return False
# ----------------------------------------------------------------------------------------------------------------------
def find_gt(video):
    """Ground truth files of a video: [(path, 'mot' | 'one')], MOT first (more objects). <stem>.gt.txt / <stem>.gt.jsonl, or
    in a folder with only this video gt.txt / gt.jsonl (the older layout)."""
    out = [(p, k) for p, k in ((sidecar(video, GT_MOT), 'mot'), (sidecar(video, GT_ONE), 'one')) if os.path.isfile(p)]
    if _alone_in_folder(video):
        folder = os.path.dirname(video)
        for f, k in (('gt.txt', 'mot'), ('gt.jsonl', 'one')):
            if os.path.isfile(os.path.join(folder, f)) and k not in {kk for _, kk in out}:
                out.append((os.path.join(folder, f), k))
        out.sort(key=lambda x: x[1] != 'mot')
    return out
# ----------------------------------------------------------------------------------------------------------------------
def _gt_for(video_path):
    """The ground truth the Live pipeline overlays (gt_table reads both kinds); None without one."""
    g = find_gt(video_path)
    return g[0][0] if g else None
# ----------------------------------------------------------------------------------------------------------------------
def gt_table(path):
    """Ground truth as MOT rows (DataFrame frame 1-based, id, x, y, w, h, conf) - a .gt.jsonl becomes track id 1."""
    import json
    import pandas as pd
    if not str(path).lower().endswith('.jsonl'):
        return pd.read_csv(path, header=None)
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = r.get('box')
            if b:
                rows.append([int(r['frame']) + 1, 1, float(b[0]), float(b[1]), float(b[2]) - float(b[0]), float(b[3]) - float(b[1]), 1.0])
    return pd.DataFrame(rows, columns=range(7)) if rows else pd.DataFrame(columns=range(7))
# ----------------------------------------------------------------------------------------------------------------------
def video_name(path, root=None):
    """The name a video is listed under: its path in videos/ without the extension; a video named like its folder (or
    'video') is named by the folder: mot15/PETS09-S2L1/PETS09-S2L1.mp4 -> mot15/PETS09-S2L1."""
    rel = os.path.relpath(os.path.splitext(os.path.abspath(path))[0], root or VIDEOS_DIR).replace('\\', '/')
    parts = rel.split('/')
    if len(parts) > 1 and parts[-1] in (parts[-2], 'video') and _alone_in_folder(path):
        rel = '/'.join(parts[:-1])
    return rel
# ----------------------------------------------------------------------------------------------------------------------
def in_store(path, root=None):
    root = os.path.abspath(root or VIDEOS_DIR)
    return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(root) + os.sep)
# ----------------------------------------------------------------------------------------------------------------------
def list_videos(root=None):
    """Every video file in the store: [{name, path, gt: [(path, kind)]}], by name."""
    root = root or VIDEOS_DIR
    out, seen = [], set()
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(('.', '_')))
        for f in sorted(files):
            if not f.lower().endswith(VIDEO_EXT) or '.tmp' in f.lower():
                continue
            path = os.path.join(d, f)
            name = video_name(path, root)
            if name in seen:                                  # a.mp4 and a.avi side by side
                name = os.path.relpath(path, root).replace('\\', '/')
            seen.add(name)
            out.append({'name': name, 'path': path, 'gt': find_gt(path)})
    out.sort(key=lambda v: v['name'].lower())
    return out
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
def list_sources(root=None):
    """Ordered list of dicts {name, key, label, source, gt, gts}: embedded (CSI) and USB cameras first, then every video of
    the store (name = its alias if it has one, key = its store name; gt = the file the Live overlay reads, gts = all)."""
    alias_of = {v: k for k, v in ALIASES.items()}
    result = [{'name': name, 'label': f'{name}: embedded camera {title} ({url})', 'source': url, 'gt': None} for name, url, title in list_csi_cameras()]
    result += [{'name': name, 'label': f'{name}: {title} ({device})', 'source': device, 'gt': None} for name, device, title in list_cameras()]
    for v in list_videos(root):
        alias = alias_of.get(v['name'])
        result.append({'name': alias or v['name'], 'key': v['name'],
                       'label': (f"{alias} ({v['name']})" if alias else v['name']) + (' [gt]' if v['gt'] else ''),
                       'source': v['path'].replace('\\', '/'), 'gt': v['gt'][0][0] if v['gt'] else None, 'gts': v['gt']})
    return result
# ----------------------------------------------------------------------------------------------------------------------
def is_url(spec):
    return '://' in str(spec)
# ----------------------------------------------------------------------------------------------------------------------
def resolve_source(spec, root=None):
    """spec -> (name, source, gt). Raises ValueError if it cannot be resolved."""
    spec = str(spec).strip()
    if spec == '':
        raise ValueError('empty source')

    for s in list_sources(root):
        if spec in (s['name'], s.get('key')) or (s.get('key') and ALIASES.get(spec) == s['key']):
            return s['name'], s['source'], s['gt']

    if is_url(spec):
        return spec, spec, None

    if is_device(spec) and (os.path.exists(spec) or spec.startswith('dshow://')):
        return next((n for n, d, t in list_cameras() if d == spec), os.path.basename(spec)), spec, None

    if spec.isdigit():                       # webcam index: /dev/videoN on Linux, the Nth camera on Windows (both PyAV), else tools_grabber
        if os.path.exists(f'/dev/video{spec}'):
            return resolve_source(f'/dev/video{spec}', root)
        cams = list_cameras() if sys.platform == 'win32' else []
        if int(spec) < len(cams):
            return cams[int(spec)][0], cams[int(spec)][1], None
        return f'webcam {spec}', spec, None

    if os.path.isdir(spec):                  # folder of images, read by tools_grabber
        return os.path.basename(os.path.normpath(spec)), spec, None

    if os.path.isfile(spec):
        return os.path.splitext(os.path.basename(spec))[0], spec, _gt_for(spec)

    names = ', '.join(s['name'] for s in list_sources(root))
    raise ValueError(f"unknown source '{spec}'. Use a URL, a file path, a folder, a webcam index or one of: {names}")
# ----------------------------------------------------------------------------------------------------------------------
def apply_to_config(config, spec, root=None):
    name, source, gt = resolve_source(spec, root)
    config.source = source
    config.gt = gt
    config.source_name = name
    config.exp_name = ('[live] ' if is_url(source) or is_device(source) or str(source).isdigit() else '[offline] ') + name
    return config
# ----------------------------------------------------------------------------------------------------------------------
