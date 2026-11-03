import os
import glob
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
}
# ----------------------------------------------------------------------------------------------------------------------
def _gt_for(video_path):
    folder = os.path.dirname(video_path)
    gt = os.path.join(folder, 'gt.txt')
    videos = [f for f in os.listdir(folder) if f.lower().endswith(VIDEO_EXT)]
    return gt if (os.path.isfile(gt) and len(videos) == 1) else None
# ----------------------------------------------------------------------------------------------------------------------
def list_cameras():
    """USB/V4L2 cameras on Linux: [(name, device, title)], e.g. ('camera', '/dev/video0', 'HD Pro Webcam C920').
    Only the capture node of each camera is listed (/dev/v4l/by-id/*-video-index0); re-scanned on every call (hot-plug)."""
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
def is_device(spec):
    return str(spec).startswith('/dev/video')
# ----------------------------------------------------------------------------------------------------------------------
def list_sources(images_dir=IMAGES_DIR):
    """Ordered list of dicts {name, label, source, gt}: connected cameras first, then every video file under images_dir."""
    alias_of = {v: k for k, v in ALIASES.items()}
    result = [{'name': name, 'label': f'{name}: {title} ({device})', 'source': device, 'gt': None} for name, device, title in list_cameras()]
    for path in sorted(glob.glob(os.path.join(images_dir, '**', '*'), recursive=True)):
        if not path.lower().endswith(VIDEO_EXT):
            continue
        rel = os.path.relpath(path, images_dir).replace('\\', '/')
        name = os.path.splitext(rel)[0]
        alias = alias_of.get(name)
        result.append({'name': alias or name,
                       'label': f'{alias} ({rel})' if alias else rel,
                       'source': path.replace('\\', '/'),
                       'gt': _gt_for(path)})
    for path in sorted(glob.glob(os.path.join(DATASETS_DIR, '*', '*', '*'))):     # one video per sequence folder
        if not path.lower().endswith(VIDEO_EXT):
            continue
        rel = os.path.relpath(os.path.dirname(path), DATASETS_DIR).replace('\\', '/')     # e.g. visdrone/uav0000086_00000_v
        result.append({'name': rel, 'label': f'{rel} (dataset)' + (' [gt]' if _gt_for(path) else ''),
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

    if is_device(spec) and os.path.exists(spec):
        return next((n for n, d, t in list_cameras() if d == spec), os.path.basename(spec)), spec, None

    if spec.isdigit():                       # webcam index: /dev/videoN on Linux (PyAV), else tools_grabber (e.g. DirectShow on Windows)
        if os.path.exists(f'/dev/video{spec}'):
            return resolve_source(f'/dev/video{spec}', images_dir)
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
