"""DTC harness: one command for the everyday operations on the Raspberry Pi, the PC simulator and the tests.

  dtc pi status                    where the Pi is, its commit, is DTC running, source / fps / SOT
  dtc pi sync                      put the Pi on this laptop's branch and commit (GitHub or, offline, from here)
  dtc pi start [--source X ...]    start DTC on the Pi (any app flags; default source: campus)
  dtc pi stop                      stop it cleanly (video and report are written)
  dtc pi logs [-n 40]              the app log without progress noise
  dtc pi open                      open the web UI in the browser
  dtc pi run "<shell command>"     run anything on the Pi

  dtc sim start [--maneuver yaw]   Fly2 camera simulator on this PC -> rtsp://<this PC>:8554/sim
  dtc sim stop
  dtc sim pi                       start DTC on the Pi on the simulator stream

  dtc clips record NAME [--maneuver yaw|yaw_fast|sweep|none] [--seconds 60]   reference clip with ground truth -> clips/NAME
  dtc clips list

  dtc test unit | smoke | integration [--sim] | quality     -> runs/<suite>/NNNN (report.md, metrics.json, manifest.json)
  dtc runs [suite]                 latest runs and their verdicts
  dtc report [suite] [NNNN]        print a run's report (default: the latest run)
"""
import argparse
import json
import os
import subprocess
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from harness import runs  # noqa: E402
# ----------------------------------------------------------------------------------------------------------------------
def cmd_pi(a, extra):
    from harness import pi_app
    pi = pi_app.Pi(verbose=False)
    if a.action == 'status':
        c = pi.commit()
        print(f'Pi        {pi.host}   commit {c[0] if c else "?"} ({c[1] if len(c) > 1 else "?"}), tools {c[2] if len(c) > 2 else "?"}')
        print(f'laptop    commit {runs.git("rev-parse", "--short", "HEAD")} ({runs.git("rev-parse", "--abbrev-ref", "HEAD")})'
              + ('  + uncommitted changes' if runs.git('status', '--porcelain', '--untracked-files=no') else ''))
        st = pi.status()
        if st is None:
            print('DTC       not running' + (' (process alive, UI not answering)' if pi.app_pid() else ''))
        else:
            print(f'DTC       running {st["uptime_s"]:.0f} s, source {st["source_name"]} {st["frame_size"]}, {st["fps"]} fps, '
                  f'detection {"on" if st["do_detection"] else "off"}, SOT {"tracking" if st["sot_active"] else "idle"}')
            print(f'          {pi.url()}   run folder {st["run_folder"]}')
        return 0
    if a.action == 'sync':
        return 0 if pi.sync() else 1
    if a.action == 'start':
        args = ' '.join(extra) if extra else '--source campus'
        print(f'starting DTC on the Pi: {args}')
        st = pi.start(args)
        if st is None:
            print('did not come up:\n' + pi.logs(20)); return 1
        print(f'running: {st["source_name"]} {st["frame_size"]}  ->  {pi.url()}'); return 0
    if a.action == 'stop':
        code = pi.stop()
        print(f'stopped (exit code {code})'); return 0
    if a.action == 'logs':
        print(pi.logs(a.n)); return 0
    if a.action == 'open':
        webbrowser.open(pi.url()); print(pi.url()); return 0
    if a.action == 'run':
        return subprocess.run(pi_app.pi_ssh.plink_args(pi.host) + [' '.join(extra)]).returncode
# ----------------------------------------------------------------------------------------------------------------------
def cmd_sim(a, extra):
    from harness import sim, pi_app
    if a.action == 'start':
        print('starting the Fly2 simulator (~40 s: load + take-off) ...')
        sim.start(stream=True, maneuver=a.maneuver)
        print('streaming rtsp://127.0.0.1:8554/sim   Fly2 UI (move the camera): http://127.0.0.1:8509/   log: ' + sim.LOG); return 0
    if a.action == 'stop':
        print('stopped' if sim.stop() else 'still running?'); return 0
    if a.action == 'pi':
        pi = pi_app.Pi(verbose=False)
        url = sim.rtsp_url_for(pi)
        args = f'--source {url} --detection_model /home/pi/sources/Fly2/models/fly_416_ncnn_model --imgsz_hw 256,416 ' + ' '.join(extra)
        st = pi.start(args)
        print(f'DTC on {url}: ' + (f'running -> {pi.url()}' if st else 'did not come up\n' + pi.logs(20))); return 0 if st else 1
# ----------------------------------------------------------------------------------------------------------------------
def cmd_clips(a, extra):
    from harness import sim
    if a.action == 'list':
        if not os.path.isdir(sim.CLIPS):
            print('no clips yet (dtc clips record NAME)'); return 0
        for n in sorted(os.listdir(sim.CLIPS)):
            m = os.path.join(sim.CLIPS, n, 'meta.json')
            meta = json.load(open(m)) if os.path.isfile(m) else {}
            print(f'{n:20s} {meta.get("frames", "?"):>5} frames  maneuver {meta.get("maneuver", "?"):9s} {meta.get("created", "")}')
        return 0
    if a.action == 'record':
        if not a.name:
            print('dtc clips record NAME'); return 2
        out = os.path.join(sim.CLIPS, a.name)
        if os.path.exists(out):
            print(f'{out} exists - pick another name or delete it'); return 1
        print(f'recording {a.seconds:.0f} s, maneuver {a.maneuver} -> {out} (simulator start ~40 s first)')
        sim.start(stream=False, record=out, maneuver=a.maneuver, seconds=a.seconds)
        rows = [json.loads(l) for l in open(os.path.join(out, 'gt.jsonl'))]
        print(f'done: {len(rows)} frames, target visible in {sum(1 for r in rows if r["box"])}'); return 0
# ----------------------------------------------------------------------------------------------------------------------
def cmd_test(a, extra):
    from harness import suites
    with runs.Run(a.suite) as R:
        if a.suite == 'unit':
            suites.unit(R, sync=not a.no_sync)
        elif a.suite == 'smoke':
            suites.smoke(R, sync=not a.no_sync)
        elif a.suite == 'integration':
            suites.integration(R, sync=not a.no_sync, with_sim=a.sim)
        elif a.suite == 'quality':
            suites.quality(R, sync=not a.no_sync)
    return 0 if R.verdict() == 'PASS' else 1
# ----------------------------------------------------------------------------------------------------------------------
def cmd_runs(a, extra):
    suites = [a.suite] if a.suite else sorted(d for d in os.listdir(runs.RUNS) if os.path.isdir(os.path.join(runs.RUNS, d)) and d not in ('harness', 'sessions')) if os.path.isdir(runs.RUNS) else []
    for s in suites:
        for n in runs.numbered(os.path.join(runs.RUNS, s))[-(a.n):]:
            m = os.path.join(runs.RUNS, s, '%04d' % n, 'manifest.json')
            j = json.load(open(m)) if os.path.isfile(m) else {}
            c = j.get('checks', {})
            print(f'{s:12s} {n:04d}  {j.get("verdict", "?"):5s}  {j.get("started", ""):19s}  {j.get("duration_s", "?"):>6} s  '
                  f'commit {j.get("laptop", {}).get("commit", "?")}  {c.get("PASS", 0)} pass {c.get("FAIL", 0)} fail {c.get("SKIP", 0)} skip')
    return 0
# ----------------------------------------------------------------------------------------------------------------------
def cmd_report(a, extra):
    suite = a.suite
    if suite is None:
        latest = max(((os.path.getmtime(os.path.join(runs.RUNS, s, '%04d' % n)), s, n) for s in os.listdir(runs.RUNS)
                      if os.path.isdir(os.path.join(runs.RUNS, s)) for n in runs.numbered(os.path.join(runs.RUNS, s))), default=None)
        if latest is None:
            print('no runs yet'); return 1
        _, suite, n = latest
    else:
        nums = runs.numbered(os.path.join(runs.RUNS, suite))
        if not nums:
            print(f'no {suite} runs'); return 1
        n = int(a.number) if a.number else nums[-1]
    p = os.path.join(runs.RUNS, suite, '%04d' % n, 'report.md')
    print(open(p, encoding='utf-8').read()); print(p); return 0
# ----------------------------------------------------------------------------------------------------------------------
def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    p = argparse.ArgumentParser(prog='dtc', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd')
    s = sub.add_parser('pi'); s.add_argument('action', choices=['status', 'sync', 'start', 'stop', 'logs', 'open', 'run']); s.add_argument('-n', type=int, default=40)
    s = sub.add_parser('sim'); s.add_argument('action', choices=['start', 'stop', 'pi']); s.add_argument('--maneuver', default='none')
    s = sub.add_parser('clips'); s.add_argument('action', choices=['record', 'list']); s.add_argument('name', nargs='?')
    s.add_argument('--maneuver', default='yaw'); s.add_argument('--seconds', type=float, default=60)
    s = sub.add_parser('test'); s.add_argument('suite', choices=['unit', 'smoke', 'integration', 'quality'])
    s.add_argument('--sim', action='store_true', help='integration: also the PC simulator stream'); s.add_argument('--no-sync', action='store_true')
    s = sub.add_parser('runs'); s.add_argument('suite', nargs='?'); s.add_argument('-n', type=int, default=5)
    s = sub.add_parser('report'); s.add_argument('suite', nargs='?'); s.add_argument('number', nargs='?')
    a, extra = p.parse_known_args()
    if a.cmd is None:
        print(__doc__); return 0
    return {'pi': cmd_pi, 'sim': cmd_sim, 'clips': cmd_clips, 'test': cmd_test, 'runs': cmd_runs, 'report': cmd_report}[a.cmd](a, extra)
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    sys.exit(main())
