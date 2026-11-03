"""Fly2 as a camera simulator for DTC (runs in Fly2's venv; Fly2's sources are not modified).

Fly2 standalone (Native controller, no PX4) renders the drone camera and flies a target; its web UI (:8509 on this PC)
moves the camera (arrows / PageUp/Down, a/d yaw, w/s pitch, T/t target speed, r reset target, Esc quits).
This wrapper takes the clean rendered frame (before Fly2 draws its overlay) and
    --rtsp URL       streams it (libx264 zerolatency) to mediamtx, e.g. rtsp://127.0.0.1:8554/sim
    --record DIR     and/or writes a reference clip: DIR/video.mp4 + DIR/gt.jsonl (target box per frame) + DIR/meta.json
    --maneuver NAME  scripted camera moves through Fly2's own key handler: none | yaw | yaw_fast | sweep
    --seconds N      stop after N seconds (0 = until Esc in the Fly2 UI)
The target flies a circle in front of the camera (the camera looks along +X): --circle CX,CY,R,Z  [m], --speed m/s.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import numpy
# ----------------------------------------------------------------------------------------------------------------------
FLY2 = os.environ.get('FLY2_ROOT', r'D:\source\digits\Fly2')
FPS = 25
MANEUVERS = {           # (key, presses per second, seconds) steps; ('-', 0, s) = pause
    'none': [],
    'yaw': [('-', 0, 6)] + [('d', 4, 3), ('-', 0, 2), ('a', 4, 3), ('-', 0, 2)] * 4,
    'yaw_fast': [('-', 0, 6)] + [('d', 10, 2), ('-', 0, 2), ('a', 10, 2), ('-', 0, 2)] * 5,
    'sweep': [('-', 0, 6), ('d', 6, 6), ('-', 0, 3), ('a', 6, 12), ('-', 0, 3), ('d', 6, 6), ('-', 0, 4)],   # target leaves and re-enters the view
}
# ----------------------------------------------------------------------------------------------------------------------
def parse():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--rtsp', default='')
    p.add_argument('--record', default='')
    p.add_argument('--maneuver', default='none', choices=sorted(MANEUVERS))
    p.add_argument('--seconds', type=float, default=0)
    p.add_argument('--circle', default='20,0,8,6')
    p.add_argument('--speed', type=float, default=2.0)
    p.add_argument('--ui_port', type=int, default=8509)
    a = p.parse_args()
    sys.argv = sys.argv[:1]                      # Fly2's config_base parses argv on construction
    return a
# ----------------------------------------------------------------------------------------------------------------------
def circle_csv(cx, cy, r, z):
    path = os.path.join(tempfile.gettempdir(), f'dtc_sim_circle_{cx}_{cy}_{r}_{z}.csv')
    pts = [(cx + r * math.cos(a), cy + r * math.sin(a), z + 1.0 * math.sin(2 * a)) for a in [2 * math.pi * i / 32 for i in range(32)]]
    open(path, 'w').write('\n'.join(f'{x:.2f},{y:.2f},{zz:.2f}' for x, y, zz in pts) + '\n')
    return path
# ----------------------------------------------------------------------------------------------------------------------
def main():
    args = parse()
    os.chdir(os.path.join(FLY2, 'src'))
    sys.path.insert(0, os.path.join(FLY2, 'src')); sys.path.append(os.path.join(FLY2, 'tools'))
    from configs import config_air, PID_params_follow_me
    import utils_experiment_ops

    cx, cy, r, z = [float(v) for v in args.circle.split(',')]
    config = config_air.cnfg_experiment()
    for k, v in dict(connection_flight_controller=None, mission='manual', do_detection=False, do_save_video=False,
                     circuit_target=circle_csv(cx, cy, r, z), SITL_target_X=cx + r, SITL_target_Y=cy, SITL_target_Z=z, v_max_target=args.speed).items():
        setattr(config, k, v)
    E = utils_experiment_ops.Experimentor('../shared_runs/', config, PID_params_follow_me.pid_params())
    W, H = config.SITL_W, config.SITL_H
    print(f'[  SIM  ] Fly2 controller: {E.Controller.name}, {W}x{H}@{FPS}, target circle {args.circle} @ {args.speed} m/s', flush=True)

    ff = None
    if args.rtsp:
        ff = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                               '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-g', str(FPS), '-bf', '0', '-pix_fmt', 'yuv420p',
                               '-f', 'rtsp', '-rtsp_transport', 'tcp', args.rtsp], stdin=subprocess.PIPE)
    rec = gt = None
    if args.record:
        os.makedirs(args.record, exist_ok=True)
        rec = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-g', str(FPS), '-bf', '0', '-pix_fmt', 'yuv420p',
                                os.path.join(args.record, 'video.mp4')], stdin=subprocess.PIPE)
        gt = open(os.path.join(args.record, 'gt.jsonl'), 'w')

    latest = [None]                              # (clean frame, target box or None)
    def target_box():
        rect = E.Grabber_SITL.get_rect()         # projected target outline; None when behind the camera
        if rect is None:
            return None
        x1, y1 = numpy.min(rect, axis=0); x2, y2 = numpy.max(rect, axis=0)
        if x2 < 0 or y2 < 0 or x1 >= W or y1 >= H:
            return None
        return [float(max(0, x1)), float(max(0, y1)), float(min(W - 1, x2)), float(min(H - 1, y2))]
    grab = E.grab_frame
    def grab_and_tap(*a, **k):
        img = grab(*a, **k)
        if img is not None:
            latest[0] = (img.copy(), target_box())   # copy: Fly2 draws its overlay into the returned array afterwards
        return img
    E.grab_frame = grab_and_tap

    done = threading.Event()
    armed = threading.Event()                    # set once the drone has taken off and Fly2's manual loop runs
    cycle = E.MissionManager.cycle_fly_manual
    def cycle_armed(*a, **k):
        print('[  SIM  ] airborne: streaming / recording starts', flush=True)
        armed.set()
        return cycle(*a, **k)
    E.MissionManager.cycle_fly_manual = cycle_armed
    n = [0]
    def pump():                                   # fixed 25 fps: the same frames go to the stream and to the clip
        t0 = None
        while not done.is_set():
            item = latest[0]
            if item is None or not armed.is_set():
                time.sleep(0.02); continue
            t0 = t0 or time.time()
            img, box = item
            buf = numpy.ascontiguousarray(img[:H, :W]).tobytes()
            try:
                if ff is not None: ff.stdin.write(buf)
                if rec is not None:
                    rec.stdin.write(buf)
                    gt.write(json.dumps({'frame': n[0], 't': round(n[0] / FPS, 3), 'box': box}) + '\n')
            except (BrokenPipeError, OSError):
                break
            n[0] += 1
            if args.seconds and n[0] >= args.seconds * FPS:
                done.set(); E.should_be_closed = True; break
            time.sleep(max(0.0, t0 + n[0] / FPS - time.time()))
    threading.Thread(target=pump, daemon=True).start()

    def maneuver():                               # keys through Fly2's own web UI, as a user would press them
        armed.wait()
        for key, rate, secs in MANEUVERS[args.maneuver]:
            end = time.time() + secs
            while time.time() < end and not done.is_set():
                if key != '-':
                    try:
                        urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{args.ui_port}/key_press', data=urllib.parse.urlencode({'key': key}).encode()), timeout=2).read()
                    except Exception:
                        pass
                    time.sleep(1.0 / rate)
                else:
                    time.sleep(0.1)
    threading.Thread(target=maneuver, daemon=True).start()

    out = ', '.join(x for x in [f'stream -> {args.rtsp}' if args.rtsp else '', f'clip -> {args.record}' if args.record else ''] if x)
    print(f'[  SIM  ] running: {out or "no output"}; maneuver {args.maneuver}; Fly2 UI http://127.0.0.1:{args.ui_port}/', flush=True)
    try:
        E.MissionManager.run_mission_manual()     # returns on Esc in the Fly2 UI or when --seconds is reached
    finally:
        done.set(); time.sleep(0.2)
        for p in (ff, rec):
            if p is not None:
                try: p.stdin.close()
                except Exception: pass
        if rec is not None:
            rec.wait(timeout=30); gt.close()
            json.dump({'frames': n[0], 'fps': FPS, 'size': [W, H], 'maneuver': args.maneuver, 'circle': args.circle, 'speed': args.speed,
                       'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': 'Fly2 sim (harness/sim_fly2.py)'},
                      open(os.path.join(args.record, 'meta.json'), 'w'), indent=2)
        if ff is not None:
            ff.terminate()
        print(f'[  SIM  ] stopped after {n[0]} frames', flush=True)
        os._exit(0)                               # Fly2's UI / grabber threads are not daemons
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    main()
