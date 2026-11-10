"""Test suites run from this laptop against a target: the Raspberry Pi (ARM, default) or this x86 laptop itself
(--local: harness/local_app.Local, the working tree in the x86 venv). Each run -> runs/<suite>/NNNN, local runs ->
runs/<suite>_x86/NNNN (metrics are compared with the previous run on the same platform only). See runs.py.

    unit         tests/unit on the Pi (unittest, seconds)
    smoke        ~2 min, before every push: unit + app start, frames, SOT click/Esc, detection on, clean shutdown
    integration  ~5 min: CLI, source switching (file, 1080p, dead URL, camera), SOT loss at the edge, Esc quits;
                 with --sim also the PC simulator stream and the drone detector + DeepSORT on it
    quality      metrics with limits, compared with the previous run: SOT loss detection (PETS ground truth),
                 SOT on reference sim clips (clips/), app FPS per mode - Pi only (limits are Pi baselines)
"""
import json
import os
import re
import time

from harness import pi_app, runs
# ----------------------------------------------------------------------------------------------------------------------
ROOT = runs.ROOT
PI_REPO = pi_app.REPO
PI_VENV = pi_app.VENV
LIMITS = {                     # one place for every threshold
    'video_fps': 20.0, 'detect_fps': 3.0, 'mot_fps': 4.5, 'drone_mot_fps': 4.0,
    'pets_caught_pct': 50.0, 'pets_false_cancel': 3,
    # clips: set from the 2026-11-03 baseline (worst clip: 92.9 %, 20, 13) so that the old behaviour without the
    # 'empty box' rule (82.2 %, 52, 65 on sim_yaw_fast) fails all three - checked, see quality 0001/0002
    'clip_on_target_pct': 88.0, 'clip_drift_gone': 35, 'clip_drift_visible': 30,
}
# ----------------------------------------------------------------------------------------------------------------------
def target(local=False):
    """The Pi (ARM, over SSH) or this laptop (x86) - the same interface (pi_app.AppClient + file helpers)."""
    if local:
        from harness import local_app
        return local_app.Local()
    return pi_app.Pi()
# ----------------------------------------------------------------------------------------------------------------------
def suite_name(suite, local=False):
    return f'{suite}_x86' if local else suite
# ----------------------------------------------------------------------------------------------------------------------
def _prepare(R, pi, sync=True):
    if pi.name == 'local':
        R.section('this PC (x86)')
        c = pi.commit()
        R.manifest['target'] = 'local'
        R.manifest['local'] = {'python': pi.python, 'commit': c[0], 'branch': c[1], 'tools_commit': c[2], 'working_tree': True}
        R.log(f'   app python {pi.python} - runs the working tree (uncommitted changes included)')
        if pi.app_pid():
            R.log('   stopping the DTC session that was running here')
            pi.stop()
        return
    R.section('Pi')
    R.manifest['target'] = 'pi'
    if sync:
        R.check('code synced to the Pi', pi.sync(), f'laptop {R.manifest["laptop"]["branch"]} @ {R.manifest["laptop"]["commit"]}')
    c = pi.commit()
    R.manifest['pi'] = {'host': pi.host, 'commit': c[0] if c else None, 'branch': c[1] if len(c) > 1 else None, 'tools_commit': c[2] if len(c) > 2 else None}
    if pi.app_pid():
        R.log('   stopping the DTC session that was running on the Pi')
        pi.stop()
# ----------------------------------------------------------------------------------------------------------------------
def _app_folder(R, pi):
    return f'{pi.repo}/runs/harness/{R.suite}-{R.number:04d}/'
# ----------------------------------------------------------------------------------------------------------------------
def _start(R, pi, args, name='app'):
    t0 = time.time()
    st = pi.start(f'{args} --folder_runs {_app_folder(R, pi)}{name}/')
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
    folder = f'{_app_folder(R, pi)}{name}/0001'
    files = tuple(pi.video_name if f == 'video_large.avi' else f for f in files)
    have = pi.ls(folder)
    missing = [f for f in files if f not in have]
    R.check(f'{name}: report written', not missing, f'{folder}' + (f' missing {missing}' if missing else ''))
    pi.save_log(f'{folder}/app.log')
    dst = R.path(pi.name, name)
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
def _sot_models_check(R, pi, models=('VIT', 'SIAMBAN', 'SIAMFC')):
    """The UI model switch: every alternative click-to-track model starts, holds the static van, Esc stops it; back to NANO."""
    b = CAMPUS_STATIC_TARGET
    for m in models:
        code, _ = pi.post('/sot_model', js={'model': m})
        st = pi.wait_for(lambda s: s.get('sot_model') == m, 5)
        if not R.check(f'SOT model {m}: selected', code == 200 and st is not None, f'HTTP {code}'):
            continue
        pi.click((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, b)
        st = pi.wait_for(lambda s: s['sot_active'], 20)              # torch models load on first use (a few s on the Pi)
        time.sleep(3)
        st = pi.status()
        ok = bool(st and st['sot_active'] and st['sot_model'] == m)
        R.check(f'SOT model {m}: tracks the static target', ok, f'{st and st.get("sot_ms")} ms/frame, {st and st["fps"]} fps, reason {st and st["sot_stop_reason"]}')
        if st and st.get('sot_ms') is not None:
            R.metric(f'sot_ms_{m.lower()}', st['sot_ms'], 'ms', better='lower')
        if ok:
            pi.key('Escape'); pi.wait_for(lambda s: not s['sot_active'], 3)
    pi.post('/sot_model', js={'model': 'NANO'})
    st = pi.wait_for(lambda s: s.get('sot_model') == 'NANO', 5)
    R.check('SOT model: back to NANO', st is not None)
# ----------------------------------------------------------------------------------------------------------------------
def _bench_check(R, pi):
    """A tiny click-to-track benchmark through the web API: live holds while it runs, results come back, live resumes."""
    code, r = pi.post('/bench/start', js={'models': ['NANO'], 'sequences': ['campus'], 'tracks': 1, 'max_frames': 40})
    if not R.check('bench: starts from the web API', code == 200 and isinstance(r, dict) and r.get('ok'), f'HTTP {code} {r}'):
        return
    rid = r['id']
    st = pi.wait_for(lambda s: s.get('bench_hold'), 5)
    R.check('bench: live holds while it runs', st is not None)
    done = None
    for _ in range(90):
        _, pr = pi.get('/bench/progress?id=' + rid)
        p = (pr or {}).get('progress') if isinstance(pr, dict) else None
        if p and p.get('state') != 'RUNNING':
            done = p; break
        time.sleep(1)
    _, d = pi.get('/bench/run?id=' + rid)
    models = ((d or {}).get('results') or {}).get('models', {}) if isinstance(d, dict) else {}
    R.check('bench: finishes with results', bool(done and done['state'] == 'DONE' and 'NANO' in models),
            f'run {rid}: {done and done["state"]} in {done and done.get("elapsed_s")} s, NANO F1 {models.get("NANO", {}).get("f1")}')
    a = pi.wait_for(lambda s: not s.get('bench_hold'), 5); time.sleep(1.5); b = pi.status()
    R.check('bench: live resumes afterwards', bool(a and b and b['frames'] > a['frames']), f'{(b["frames"] - a["frames"]) if a and b else "?"} frames in 1.5 s')
    pi.rm(f'{pi.repo}/runs/{pi.bench_folder}/{rid}')
# ----------------------------------------------------------------------------------------------------------------------
def _pause_check(R, pi):
    """Space pauses a video file, a click while paused starts SOT on the frame on screen, -> steps one frame, Space resumes."""
    pi.key('Space')
    st = pi.wait_for(lambda s: s['paused'], 3)
    a = pi.status(); time.sleep(1.5); b = pi.status()
    R.check('pause: Space holds the video', bool(st and a and b and b['paused'] and b['frames'] - a['frames'] <= 1),
            f'{b["frames"] - a["frames"] if a and b else "?"} frames in 1.5 s while paused')
    bx = CAMPUS_STATIC_TARGET
    pi.click((bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2, bx)
    st = pi.wait_for(lambda s: s['sot_active'], 3)
    R.check('pause: a click starts SOT on the paused frame', bool(st and st['sot_active'] and st['paused']), f'box {st and st["sot_box"]}')
    a = pi.status(); pi.key('ArrowRight'); time.sleep(1.0); b = pi.status()
    R.check('pause: -> shows exactly the next frame', bool(a and b and b['frames'] - a['frames'] == 1 and b['paused']), f'{b["frames"] - a["frames"] if a and b else "?"} frame(s)')
    pi.key('Space')
    st = pi.wait_for(lambda s: not s['paused'], 3)
    a = pi.status(); time.sleep(2); b = pi.status()
    R.check('pause: Space resumes at source speed, SOT keeps tracking', bool(st and b and not b['paused'] and b['frames'] - a['frames'] >= 30 and b['sot_active']),
            f'{(b["frames"] - a["frames"]) / 2 if a and b else 0:.0f} fps, SOT {"on" if b and b["sot_active"] else "off"}')
    pi.key('Escape'); pi.wait_for(lambda s: not s['sot_active'], 3)
    time.sleep(1)
    st = pi.wait_for(lambda s: (s.get('video_pos') or 0) > 30, 3)      # the video may just have looped to its start
    pos = (st or pi.status() or {}).get('video_pos') or 0
    pi.key('r')
    st = pi.wait_for(lambda s: (s.get('video_pos') or 999) < 15, 3)
    R.check('R: video from the start', bool(st and pos > 15 and st['video_pos'] < 15), f'frame {pos} -> {st and st["video_pos"]}')
# ----------------------------------------------------------------------------------------------------------------------
def _label_check(R, pi):
    """Label tab through the web API: open campus as a throw-away project, a box on frame 0, the tracker follows it, the clip
    is saved for the benchmark; the project (clips/smoke_label, runs/labels/smoke_label) is removed afterwards."""
    name = 'smoke_label'
    code, r = pi.post('/label/open', js={'source': 'campus', 'name': name, 'model': 'NANO'})
    if not R.check('label: opens a video', code == 200 and isinstance(r, dict) and r.get('ok'), f'HTTP {code} {r}'):
        return
    for _ in range(40):
        _, st = pi.get('/label/state')
        if ((st or {}).get('state') or {}).get('decoded', 0) >= 60:
            break
        time.sleep(0.25)
    pi.post('/label/speed', js={'speed': 0})
    code, r = pi.post('/label/box', js={'i': 0, 'box': list(CAMPUS_STATIC_TARGET)})
    R.check('label: a box on frame 0 is a key frame', code == 200 and r.get('ok') and r['state']['counts']['manual'] == 1, f'HTTP {code} {r}')
    code, r = pi.post('/label/track', js={'i': 0})
    time.sleep(2.0)
    pi.post('/label/stop', js={})
    _, st = pi.get('/label/state')
    st = (st or {}).get('state') or {}
    n = (st.get('counts') or {}).get('track', 0)
    R.check('label: the tracker follows the box', n >= 30, f'{n} tracked frames in 2 s, stopped: {st.get("stop_reason")}')
    seqs = []
    for _ in range(20):                            # video.mp4 is written once the whole video is decoded
        _, opts = pi.get('/bench/options')
        seqs = [s['id'] for s in ((opts or {}).get('sequences') or [])] if isinstance(opts, dict) else []
        if 'clip:' + name in seqs:
            break
        time.sleep(0.5)
    R.check('label: the clip is a benchmark sequence', 'clip:' + name in seqs)
    pi.post('/label/close', js={'discard': True})
    pi.rm(f'{pi.repo}/clips/{name}')
    pi.rm(f'{pi.repo}/runs/labels/{name}')
# ----------------------------------------------------------------------------------------------------------------------
def unit(R, pi=None, sync=True, local=False):
    standalone = pi is None
    pi = pi or target(local)
    if standalone:
        _prepare(R, pi, sync)
    R.section('unit tests (on the Pi)' if pi.name == 'pi' else 'unit tests (on this PC)')
    t0 = time.time()
    rc, out = pi.unit_tests(timeout=600)
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
def smoke(R, sync=True, local=False):
    pi = target(local)
    _prepare(R, pi, sync)
    unit(R, pi, sync=False)
    R.section('app on campus')
    if not _start(R, pi, '--source campus'):
        return
    _frames_flow(R, pi, 'video', min_fps=LIMITS['video_fps'])
    _sot_click_and_esc(R, pi)
    R.section('click-to-track models (UI switch)')
    _sot_models_check(R, pi)
    R.section('click-to-track benchmark (web API)')
    _bench_check(R, pi)
    R.section('labeling (Label tab, web API)')
    _label_check(R, pi)
    R.section('pause (video file)')
    _pause_check(R, pi)
    R.section('detection + tracking on (YOLO NCNN + DeepSORT)' if pi.name == 'pi' else 'detection + tracking on (YOLO .pt + DeepSORT)')
    pi.post('update_do_detection', js={'enabled': True}); pi.post('update_do_tracking', js={'enabled': True})
    time.sleep(6)
    _frames_flow(R, pi, 'detect', seconds=8, min_fps=LIMITS['detect_fps'])
    st = pi.status()
    R.check('app alive with detection on', st is not None and pi.app_pid() is not None, pi.logs(8) if st is None else '')
    _stop_and_collect(R, pi)
# ----------------------------------------------------------------------------------------------------------------------
def integration(R, sync=True, with_sim=False, local=False):
    pi = target(local)
    _prepare(R, pi, sync)
    R.section('command line')
    rc, out = pi.app_cli('--list-sources', timeout=60)
    R.check('--list-sources lists the presets', 'campus' in out and 'oxford' in out, ', '.join(l.split()[0] for l in out.splitlines() if l.strip())[:120])
    rc, out = pi.app_cli('--source no-such-source', timeout=60)
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

    if with_sim and pi.name == 'local':
        R.check('PC simulator stream', True, 'Pi only (drone NCNN detector on the Pi)', skip=True)
    elif with_sim:
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
    folder = f'{_app_folder(R, pi)}{name}/'
    t0 = time.time()
    rc, out, _ = pi.sh(f'cd {PI_REPO} && timeout 600 {pi_app.APP_SCRIPT} {args} --realtime false --max_frames 300 --folder_runs {folder} > /tmp/dtc_{name}.log 2>&1; '
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
                gated = name.startswith('sim_')       # limits were set on the sim clips; dataset clips are tracked as a baseline (no limit yet)
                R.metric(f'clip_{name}_on_target_pct', v['on_target_pct'], '%', min=LIMITS['clip_on_target_pct'] if gated else None)
                R.metric(f'clip_{name}_drift_gone', v['drift_gone'], 'frames', max=LIMITS['clip_drift_gone'] if gated else None, better='lower')
                R.metric(f'clip_{name}_drift_visible', v['drift_visible'], 'frames', max=LIMITS['clip_drift_visible'] if gated else None, better='lower')
        except Exception as e:
            R.check('sot_clips', False, f'{e}: {pi.sh("tail -5 /tmp/q_clips.log")[1]}')

    R.section('app FPS (processing capacity, 300 frames, every frame once)')
    R.metric('fps_video_only', _app_fps(R, pi, 'fps_video', '--source campus'), 'fps', min=LIMITS['video_fps'])
    R.metric('fps_mot_yolov8n', _app_fps(R, pi, 'fps_mot', '--source campus --do_detection true --do_tracking true'), 'fps', min=LIMITS['mot_fps'])
    if clips:
        R.metric('fps_drone_mot', _app_fps(R, pi, 'fps_drone', f'--source {PI_REPO}/clips/{clips[0]}/video.mp4 '
                 f'--detection_model /home/pi/sources/Fly2/models/fly_416_ncnn_model --imgsz_hw 256,416 --do_detection true --do_tracking true'),
                 'fps', min=LIMITS['drone_mot_fps'])
