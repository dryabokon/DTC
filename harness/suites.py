"""Test suites run from this laptop against the Raspberry Pi. Each run -> runs/<suite>/NNNN (see runs.py).

    unit         tests/unit on the Pi (unittest, seconds)
    smoke        ~2 min, before every push: unit + app start, frames, SOT click/Esc, detection on, clean shutdown
    integration  ~5 min: CLI, source switching (file, 1080p, dead URL, camera), SOT loss at the edge, Esc quits;
                 with --sim also the PC simulator stream and the drone detector + DeepSORT on it
    quality      metrics with limits, compared with the previous run: SOT loss detection (PETS ground truth),
                 SOT on reference sim clips (clips/), app FPS per mode
"""
import json
import os
import re
import time

from harness import pi_app, runs
# ----------------------------------------------------------------------------------------------------------------------
ROOT = runs.ROOT
PI_REPO = pi_app.REPO
PI_VENV = 'source ~/venv/p311_dtc/bin/activate'
LIMITS = {                     # one place for every threshold
    'video_fps': 20.0, 'detect_fps': 3.0, 'mot_fps': 4.5, 'drone_mot_fps': 4.0,
    'pets_caught_pct': 50.0, 'pets_false_cancel': 3, 'clip_on_target_pct': 80.0, 'clip_drift_gone': 60,
}
# ----------------------------------------------------------------------------------------------------------------------
def _prepare(R, pi, sync=True):
    R.section('Pi')
    if sync:
        R.check('code synced to the Pi', pi.sync(), f'laptop {R.manifest["laptop"]["branch"]} @ {R.manifest["laptop"]["commit"]}')
    c = pi.commit()
    R.manifest['pi'] = {'host': pi.host, 'commit': c[0] if c else None, 'branch': c[1] if len(c) > 1 else None, 'tools_commit': c[2] if len(c) > 2 else None}
    if pi.app_pid():
        R.log('   stopping the DTC session that was running on the Pi')
        pi.stop()
# ----------------------------------------------------------------------------------------------------------------------
def _app_folder(R):
    return f'{PI_REPO}/runs/harness/{R.suite}-{R.number:04d}/'
# ----------------------------------------------------------------------------------------------------------------------
def _start(R, pi, args, name='app'):
    t0 = time.time()
    st = pi.start(f'{args} --folder_runs {_app_folder(R)}{name}/')
    R.check(f'{name}: starts and answers /status', st is not None, f'{time.time() - t0:.0f} s' if st else pi.logs(15), seconds=time.time() - t0)
    return st
# ----------------------------------------------------------------------------------------------------------------------
def _frames_flow(R, pi, name, seconds=4, min_fps=None):
    a = pi.status(); time.sleep(seconds); b = pi.status()
    ok = a is not None and b is not None and b['frames'] > a['frames']
    fps = (b['frames'] - a['frames']) / seconds if ok else 0.0
    R.check(f'{name}: frames are processed', ok, f'{fps:.1f} fps, {b["frame_size"] if b else "-"}')
    if min_fps is not None:
        R.metric(f'{name}_fps', round(fps, 1), 'fps', min=min_fps)
    return fps
# ----------------------------------------------------------------------------------------------------------------------
def _stop_and_collect(R, pi, name='app', expect_exit=0, files=('df_time.csv', 'df_track.csv', 'config.py', 'video_large.avi'), already_stopping=False):
    code = pi.exit_code() if already_stopping else pi.stop()
    R.check(f'{name}: exits cleanly', code == expect_exit, f'exit code {code}')
    folder = f'{_app_folder(R)}{name}/0001'
    rc, out, _ = pi.sh(f'ls {folder} 2>/dev/null')
    have = out.split()
    missing = [f for f in files if f not in have]
    R.check(f'{name}: report written', not missing, f'{folder}' + (f' missing {missing}' if missing else ''))
    pi.sh(f'cp {pi_app.APP_LOG} {folder}/app.log 2>/dev/null')
    dst = R.path('pi', name)
    os.makedirs(dst, exist_ok=True)
    pi.pull(folder + '/*', dst, recursive=True)
    return folder
# ----------------------------------------------------------------------------------------------------------------------
CAMPUS_STATIC_TARGET = (650, 42, 725, 100)       # the parked white van in PETS09 (768x576): static, contrasty, nobody walks over it
def _sot_click_and_esc(R, pi):
    st = pi.status()
    if st is None or st['frame_size'] != [768, 576]:
        R.check('SOT: click starts tracking', False, f'expected the campus video, got {st and st["source_name"]}'); return
    b = CAMPUS_STATIC_TARGET
    pi.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, b)
    st = pi.wait_for(lambda s: s['sot_active'], 3)
    R.check('SOT: click starts tracking', bool(st and st['sot_active']), f'box {st["sot_box"] if st else None}')
    time.sleep(2)
    st = pi.status()
    if not R.check('SOT: keeps a static target', bool(st and st['sot_active']), f'reason {st and st["sot_stop_reason"]}'):
        return                                   # Esc now would quit the app (nothing tracked) - don't
    pi.key('Escape')
    st = pi.wait_for(lambda s: not s['sot_active'], 3)
    R.check('SOT: Esc stops tracking (and only that)', bool(st and not st['sot_active'] and st['sot_stop_reason'] == 'stopped by user' and pi.app_pid()),
            f'reason {st["sot_stop_reason"] if st else None}, app running {bool(pi.app_pid())}')
# ----------------------------------------------------------------------------------------------------------------------
def unit(R, pi=None, sync=True):
    pi = pi or pi_app.Pi()
    if R.suite == 'unit':
        _prepare(R, pi, sync)
    R.section('unit tests (on the Pi)')
    t0 = time.time()
    rc, out, err = pi.sh(f'cd {PI_REPO} && {PI_VENV} && python -m unittest discover -s tests/unit -t . -v 2>&1 | grep -v "SOT trk"', timeout=600)
    open(R.path('unit.txt'), 'w', encoding='utf-8').write(out)
    m = re.search(r'Ran (\d+) tests', out)
    failed = re.search(r'FAILED \((.*)\)', out)
    R.check('unit tests', m is not None and 'OK' in out.splitlines()[-1] if out else False,
            f'{m.group(1) if m else "?"} tests' + (f', {failed.group(1)}' if failed else '') + f', {time.time() - t0:.0f} s')
    for line in out.splitlines():
        if line.endswith(('FAIL', 'ERROR')):
            R.log('      ' + line)
    return pi
# ----------------------------------------------------------------------------------------------------------------------
def smoke(R, sync=True):
    pi = pi_app.Pi()
    _prepare(R, pi, sync)
    unit(R, pi, sync=False)
    R.section('app on campus')
    if not _start(R, pi, '--source campus'):
        return
    _frames_flow(R, pi, 'video', min_fps=LIMITS['video_fps'])
    _sot_click_and_esc(R, pi)
    R.section('detection + tracking on (YOLO NCNN + DeepSORT)')
    pi.post('update_do_detection', js={'enabled': True}); pi.post('update_do_tracking', js={'enabled': True})
    time.sleep(6)
    _frames_flow(R, pi, 'detect', seconds=8, min_fps=LIMITS['detect_fps'])
    st = pi.status()
    R.check('app alive with detection on', st is not None and pi.app_pid() is not None, pi.logs(8) if st is None else '')
    _stop_and_collect(R, pi)
# ----------------------------------------------------------------------------------------------------------------------
def integration(R, sync=True, with_sim=False):
    pi = pi_app.Pi()
    _prepare(R, pi, sync)
    R.section('command line')
    rc, out, _ = pi.sh(f'cd {PI_REPO} && ./run_me_arm.sh --list-sources', timeout=60)
    R.check('--list-sources lists the presets', 'campus' in out and 'oxford' in out, ', '.join(l.split()[0] for l in out.splitlines() if l.strip())[:120])
    rc, out, _ = pi.sh(f'cd {PI_REPO} && timeout 60 ./run_me_arm.sh --source no-such-source', timeout=90)
    R.check('unknown --source: clear error, exit 2', rc == 2 and 'unknown source' in out, f'exit {rc}')

    R.section('sources (web UI)')
    if not _start(R, pi, '--source campus'):
        return
    _frames_flow(R, pi, 'campus')
    code, src = pi.get('sources')
    names = [s['name'] for s in src.get('sources', [])] if isinstance(src, dict) else []
    R.check('/sources lists presets', code == 200 and 'campus' in names, ', '.join(names))
    code, body = pi.post('switch_source', js={'source': 'no-such-source'})
    R.check('switch to an unknown source: HTTP 400, source unchanged', code == 400 and pi.status()['source_name'] == 'campus', f'HTTP {code}')
    pi.post('switch_source', js={'source': 'oxford'})
    st = pi.wait_for(lambda s: s['source_name'] == 'oxford' and s['frame_size'] == [1280, 720], 10)
    R.check('switch to oxford (1080p, downscaled to 1280x720)', bool(st and st['frame_size'] == [1280, 720]), f'{st["frame_size"] if st else None}')
    _frames_flow(R, pi, 'oxford')
    pi.post('switch_source', js={'source': 'rtsp://127.0.0.1:9/none'})
    st = pi.wait_for(lambda s: s['no_signal'], 8)
    R.check('dead URL: "waiting for source", app keeps running', bool(st and st['no_signal']), '')
    if 'camera' in names:
        pi.post('switch_source', js={'source': 'camera'})
        time.sleep(4)
        _frames_flow(R, pi, 'camera', min_fps=15.0)
    else:
        R.check('camera source', True, 'no camera connected', skip=True)
    pi.post('switch_source', js={'source': 'campus'})
    pi.wait_for(lambda s: s['source_name'] == 'campus' and not s['no_signal'], 8)
    _frames_flow(R, pi, 'back to campus')

    R.section('click-to-track')
    _sot_click_and_esc(R, pi)
    # automatic stops (target left the frame / empty box) are checked deterministically by tests/unit (synthetic
    # frames) and the quality suite (sim clips where the target really leaves the view): a live click has no known target

    R.section('Esc: 1st stops tracking (if any), 2nd quits the app')
    if (pi.status() or {}).get('sot_active'):
        pi.key('Escape'); pi.wait_for(lambda s: not s['sot_active'], 3)
    pi.key('Escape')
    t0 = time.time()
    while time.time() - t0 < 40 and pi.app_pid():
        time.sleep(1)
    _stop_and_collect(R, pi, already_stopping=True)

    if with_sim:
        _integration_sim(R, pi)
    else:
        R.check('PC simulator stream', True, 'not requested (dtc test integration --sim)', skip=True)
# ----------------------------------------------------------------------------------------------------------------------
def _integration_sim(R, pi):
    from harness import sim
    R.section('PC simulator -> RTSP -> Pi (drone detector + DeepSORT)')
    started_here = False
    if not sim.running():
        R.log('   starting the Fly2 simulator on this PC (~40 s) ...')
        sim.start(stream=True); started_here = True
    url = sim.rtsp_url_for(pi)
    args = (f'--source {url} --detection_model /home/pi/sources/Fly2/models/fly_416_ncnn_model --imgsz_hw 256,416 '
            f'--do_detection true --do_tracking true')
    try:
        if _start(R, pi, args, name='sim'):
            time.sleep(5)
            _frames_flow(R, pi, 'sim_stream', seconds=10, min_fps=LIMITS['drone_mot_fps'])
            folder = _stop_and_collect(R, pi, name='sim')
            rc, out, _ = pi.sh(f'{PI_VENV} && python -c "import pandas as pd; d=pd.read_csv(\'{folder}/df_track.csv\'); n=d.frame_id.nunique(); '
                               f'k=d.dropna(subset=[\'x1\']).frame_id.nunique(); print(n, k)"')
            n, k = (int(v) for v in out.split()[-2:]) if len(out.split()) >= 2 else (0, 0)
            R.metric('sim_frames_with_drone_pct', round(100.0 * k / max(1, n), 1), '%', min=50.0)
    finally:
        if started_here:
            sim.stop()
# ----------------------------------------------------------------------------------------------------------------------
def _sync_clips(R, pi):
    local = os.path.join(ROOT, 'clips')
    names = sorted(d for d in os.listdir(local) if os.path.isfile(os.path.join(local, d, 'gt.jsonl'))) if os.path.isdir(local) else []
    if not names:
        return []
    rc, out, _ = pi.sh(f'mkdir -p {PI_REPO}/clips && cd {PI_REPO}/clips && for d in *; do [ -f $d/video.mp4 ] && echo $d $(stat -c %s $d/video.mp4); done')
    have = dict(l.split() for l in out.splitlines() if len(l.split()) == 2)
    for n in names:
        size = os.path.getsize(os.path.join(local, n, 'video.mp4'))
        if have.get(n) != str(size):
            pi.sh(f'mkdir -p {PI_REPO}/clips/{n}')
            for f in ('video.mp4', 'gt.jsonl', 'meta.json'):
                if os.path.isfile(os.path.join(local, n, f)):
                    pi.push(os.path.join(local, n, f), f'{PI_REPO}/clips/{n}/{f}')
            R.log(f'   clip {n} copied to the Pi')
    return names
# ----------------------------------------------------------------------------------------------------------------------
def _app_fps(R, pi, name, args):
    """Processing capacity: every frame of a file once (--realtime false), --max_frames frames."""
    folder = f'{_app_folder(R)}{name}/'
    t0 = time.time()
    rc, out, _ = pi.sh(f'cd {PI_REPO} && timeout 600 ./run_me_arm.sh {args} --realtime false --max_frames 300 --folder_runs {folder} > /tmp/dtc_{name}.log 2>&1; '
                       f'echo rc=$?; grep "| TOTAL" {folder}0001/df_time.csv', timeout=700)
    m = re.search(r'\|\s*TOTAL\s*\|(?:[^|]*\|){4}\s*([\d.]+)', out)
    fps = float(m.group(1)) if m else None
    if fps is None:
        R.log('      ' + pi.sh(f'tail -5 /tmp/dtc_{name}.log')[1])
    return fps
# ----------------------------------------------------------------------------------------------------------------------
def quality(R, sync=True):
    pi = pi_app.Pi()
    _prepare(R, pi, sync)

    R.section('SOT loss detection vs PETS ground truth (~3 min)')
    rc, out, _ = pi.sh(f'cd {PI_REPO} && {PI_VENV} && python tests/quality/sot_pets.py /tmp/q_pets.json > /tmp/q_pets.log 2>&1; cat /tmp/q_pets.json', timeout=1200)
    try:
        q = json.loads(out); json.dump(q, open(R.path('sot_pets.json'), 'w'), indent=2)
        R.metric('pets_caught_pct', q['caught_pct'], '%', min=LIMITS['pets_caught_pct'])
        R.metric('pets_false_cancel', q['false_cancel'], 'runs', max=LIMITS['pets_false_cancel'], better='lower')
        R.metric('pets_missed', q['missed'], 'runs', better='lower')
        R.metric('pets_latency_frames', q['latency_frames_median'], 'frames', better='lower')
    except Exception as e:
        R.check('sot_pets', False, f'{e}: {pi.sh("tail -5 /tmp/q_pets.log")[1]}')

    R.section('SOT on reference sim clips (clips/)')
    clips = _sync_clips(R, pi)
    if not clips:
        R.check('reference clips', True, 'no clips in clips/ - record some: dtc clips record <name> --maneuver yaw', skip=True)
    else:
        rc, out, _ = pi.sh(f'cd {PI_REPO} && {PI_VENV} && python tests/quality/sot_clips.py clips /tmp/q_clips.json > /tmp/q_clips.log 2>&1; cat /tmp/q_clips.json', timeout=1800)
        try:
            q = json.loads(out); json.dump(q, open(R.path('sot_clips.json'), 'w'), indent=2)
            for name, v in q.items():
                R.metric(f'clip_{name}_on_target_pct', v['on_target_pct'], '%', min=LIMITS['clip_on_target_pct'])
                R.metric(f'clip_{name}_drift_gone', v['drift_gone'], 'frames', max=LIMITS['clip_drift_gone'], better='lower')
                R.metric(f'clip_{name}_drift_visible', v['drift_visible'], 'frames', better='lower')
        except Exception as e:
            R.check('sot_clips', False, f'{e}: {pi.sh("tail -5 /tmp/q_clips.log")[1]}')

    R.section('app FPS (processing capacity, 300 frames, every frame once)')
    R.metric('fps_video_only', _app_fps(R, pi, 'fps_video', '--source campus'), 'fps', min=LIMITS['video_fps'])
    R.metric('fps_mot_yolov8n', _app_fps(R, pi, 'fps_mot', '--source campus --do_detection true --do_tracking true'), 'fps', min=LIMITS['mot_fps'])
    if clips:
        R.metric('fps_drone_mot', _app_fps(R, pi, 'fps_drone', f'--source {PI_REPO}/clips/{clips[0]}/video.mp4 '
                 f'--detection_model /home/pi/sources/Fly2/models/fly_416_ncnn_model --imgsz_hw 256,416 --do_detection true --do_tracking true'),
                 'fps', min=LIMITS['drone_mot_fps'])
