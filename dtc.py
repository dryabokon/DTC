"""DTC harness: one command for the everyday operations on the Raspberry Pi (ARM), on this x86 laptop, the PC simulator
and the tests. Two targets, one interface: "pi" (harness/pi_app.py, over SSH) and "local" (harness/local_app.py, this PC).

  dtc pi status                    where the Pi is, its commit, is DTC running, source / fps / SOT
  dtc pi sync                      put the Pi on this laptop's branch and commit (GitHub or, offline, from here)
  dtc pi start [--source X ...]    start DTC on the Pi (any app flags; default source: campus)
  dtc pi stop                      stop it cleanly (video and report are written)
  dtc pi logs [-n 40]              the app log without progress noise
  dtc pi open                      open the web UI in the browser
  dtc pi run "<shell command>"     run anything on the Pi

  dtc local status | start [--source X ...] | stop | logs [-n 40] | open
                                   the same on this x86 laptop (x86 venv: infrastructure/build_me_x86.bat), the working
                                   tree runs, nothing is synced; UI http://localhost:8509 (--ui_port N if that is taken)

  dtc sim start [--maneuver yaw]   Fly2 camera simulator on this PC -> rtsp://<this PC>:8554/sim
  dtc sim stop
  dtc sim pi                       start DTC on the Pi on the simulator stream

  dtc unreal start [--res 960x540] [--borderless] [-ManualYawRate=20 ...]   Unreal game Night427 on this PC: mediamtx + game (Level03)
                                   -> rtsp://<this PC>:8554/stream (-Manual* flags go to the game)
  dtc unreal stop                  close the game, its ffmpeg and mediamtx
  dtc unreal pi [app flags]        start DTC on the Pi on the Unreal stream
  dtc unreal local [app flags]     start DTC on this PC on the Unreal stream

  dtc up [local] [unreal] [app flags]   everything in one go: (Unreal + mediamtx +) DTC on the Pi (with local: on this PC);
                                   without unreal the usual source (default campus, or --source X)
  dtc down                         stop all of it: DTC on the Pi and on this PC, the game, ffmpeg, mediamtx

  dtc clips record NAME [--maneuver yaw|yaw_fast|sweep|none] [--seconds 60]   sim recording with ground truth -> videos/sim/NAME/
  dtc clips list

  videos/ is the one store for every video (not in git): copy files in (any subfolder), ground truth lies next to a video
  as <name>.gt.jsonl (one object) or <name>.gt.txt (MOT) - see src/utils_sources.py
  dtc data list                    every video of the store, its ground truth / labels
  dtc data push [PREFIX ...]       copy the store (or the videos under the prefixes, e.g. 001/ mot15/) to the Pi
  dtc data migrate [--apply]       the older layouts (images/, datasets/videos/, clips/) -> videos/ (copies; plan first)
  dtc data prepare [mot15|visdrone|antiuav|all]   public datasets (datasets/_downloads) -> videos/<set>/<sequence>/
  dtc data convert FOLDER [--fps 25] [--keep-frames]   frames + Anti-UAV json -> FOLDER/<name>.mp4 + <name>.gt.jsonl

  dtc test unit | smoke | integration [--sim] | quality     -> runs/<suite>/NNNN (report.md, metrics.json, manifest.json)
  dtc test unit | smoke | integration --local               the same on this PC (x86) -> runs/<suite>_x86/NNNN
  dtc bench run [--preset quick|standard|full] [--models NANO,VIT] [--seqs campus,drone_ir] [--objects N]
                                   click-to-track benchmark on the Pi (as the web UI's Benchmark tab) -> runs/bench/NNNN
  dtc bench list | pull [NNNN|all] | cancel      (run / list / cancel --local: on this PC, results already in runs/bench/)
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
        st = pi.start(args, idle_pause=True)
        if st is None:
            print('did not come up:\n' + pi.logs(20)); return 1
        print(f'running: {st["source_name"]} {st["frame_size"]}  ->  {pi.url()}'); return 0
    if a.action == 'stop':
        code = pi.stop()
        print(f'stopped (exit code {code})'); return 0
    if a.action == 'logs':
        print(pi.logs(a.n)); return 0
    if a.action == 'open':
        u = pi.browser_tunnel(); webbrowser.open(u); print(u); return 0
    if a.action == 'run':
        return subprocess.run(pi_app.pi_ssh.plink_args(pi.host) + [' '.join(extra)], env=pi_app.pi_ssh.ssh_env()).returncode
# ----------------------------------------------------------------------------------------------------------------------
def cmd_local(a, extra):
    """DTC on this x86 laptop: the same actions as dtc pi (no sync / run: the working tree runs, the shell is here)."""
    from harness import local_app
    try:
        loc = local_app.Local(verbose=False)
    except RuntimeError as e:
        print('ERROR: ' + str(e)); return 1
    if a.action == 'status':
        c = loc.commit()
        print(f'this PC   python {loc.python}   commit {c[0]} ({c[1]}), tools {c[2]}'
              + ('  + uncommitted changes' if runs.git('status', '--porcelain', '--untracked-files=no') else ''))
        st = loc.status()
        if st is None:
            print('DTC       not running' + (' (process alive, UI not answering - another --ui_port?)' if loc.app_pid() else ''))
        else:
            print(f'DTC       running {st["uptime_s"]:.0f} s, source {st["source_name"]} {st["frame_size"]}, {st["fps"]} fps, '
                  f'detection {"on" if st["do_detection"] else "off"}, SOT {"tracking" if st["sot_active"] else "idle"}')
            print(f'          {loc.url()}   run folder {st["run_folder"]}')
        return 0
    if a.action == 'start':
        return _local_start(loc, extra)
    if a.action == 'stop':
        print(f'stopped (exit code {loc.stop()})'); return 0
    if a.action == 'logs':
        print(loc.logs(a.n)); return 0
    if a.action == 'open':
        u = loc.url(); webbrowser.open(u); print(u); return 0
# ----------------------------------------------------------------------------------------------------------------------
def _local_start(loc, extra):
    args = ' '.join(extra) if extra else '--source campus'
    print(f'starting DTC on this PC: {args}')
    try:
        st = loc.start(args, idle_pause=True)
    except RuntimeError as e:
        print('ERROR: ' + str(e)); return 1
    if st is None:
        print('did not come up:\n' + loc.logs(20)); return 1
    print(f'running: {st["source_name"]} {st["frame_size"]}  ->  {loc.url()}'); return 0
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
        st = pi.start(args, idle_pause=True)
        print(f'DTC on {url}: ' + (f'running -> {pi.url()}' if st else 'did not come up\n' + pi.logs(20))); return 0 if st else 1
# ----------------------------------------------------------------------------------------------------------------------
def _split_game_args(extra):
    """-ManualYawRate=20 etc. go to the game, everything else to the app on the Pi."""
    game = [x for x in extra if x.lower().startswith('-manual')]
    return game, [x for x in extra if x not in game]
# ----------------------------------------------------------------------------------------------------------------------
def _pi_start(pi, args):
    c = pi.commit()
    here = runs.git('rev-parse', '--short', 'HEAD')
    if c and c[0] != here:
        print(f'note: the Pi is on commit {c[0]}, this laptop on {here} (dtc pi sync to align)')
    print(f'Pi {pi.host}: starting DTC {args}')
    st = pi.start(args, idle_pause=True)
    if st is None:
        print('DTC did not come up:\n' + pi.logs(20)); return 1
    print(f'DTC running: {st["source_name"]} {st["frame_size"]}, {st["fps"]} fps')
    print(f'UI: {pi.url()}'); return 0
# ----------------------------------------------------------------------------------------------------------------------
def _unreal_pi(pi, app_args):
    from harness import unreal
    return _pi_start(pi, f'--source {unreal.rtsp_url_for(pi)} ' + ' '.join(app_args))
# ----------------------------------------------------------------------------------------------------------------------
def cmd_unreal(a, extra):
    from harness import unreal, pi_app
    game_args, app_args = _split_game_args(extra)
    try:
        if a.action == 'start':
            unreal.start(res=a.res, game_args=game_args, borderless=a.borderless); return 0
        if a.action == 'stop':
            return 0 if unreal.stop() else 1
        if a.action == 'pi':
            if not unreal.stream_ok():
                print('no Unreal stream on this PC - dtc unreal start first'); return 1
            return _unreal_pi(pi_app.Pi(verbose=False), app_args)
        if a.action == 'local':
            if not unreal.stream_ok():
                print('no Unreal stream on this PC - dtc unreal start first'); return 1
            from harness import local_app
            return _local_start(local_app.Local(verbose=False), ['--source', unreal.LOCAL_URL] + app_args)
    except RuntimeError as e:
        print('ERROR: ' + str(e)); return 1
# ----------------------------------------------------------------------------------------------------------------------
def cmd_up(a, extra):
    """One command for a session: with --unreal mediamtx -> game -> stream check -> DTC on the Pi on that stream."""
    from harness import unreal, pi_app
    if 'unreal' in extra:                           # dtc up unreal  ==  dtc up --unreal
        extra = [x for x in extra if x != 'unreal']; a.unreal = True
    if 'local' in extra:                            # dtc up local   ==  dtc up --local
        extra = [x for x in extra if x != 'local']; a.local = True
    game_args, app_args = _split_game_args(extra)
    try:
        if a.local:
            from harness import local_app
            loc = local_app.Local(verbose=False)
            if a.unreal:
                unreal.start(res=a.res, game_args=game_args, borderless=a.borderless)
                return _local_start(loc, ['--source', unreal.LOCAL_URL] + app_args)
            return _local_start(loc, app_args)
        pi = pi_app.Pi(verbose=False)               # finds the Pi first: no point starting the game if it is not there
        if a.unreal:
            unreal.start(res=a.res, game_args=game_args, borderless=a.borderless)
            return _unreal_pi(pi, app_args)
        return _pi_start(pi, ' '.join(app_args) if app_args else '--source campus')
    except RuntimeError as e:
        print('ERROR: ' + str(e)); return 1
# ----------------------------------------------------------------------------------------------------------------------
def cmd_down(a, extra):
    from harness import unreal, pi_app, local_app
    ok = True
    try:
        loc = local_app.Local(verbose=False)
        if loc.app_pid():
            print(f'DTC on this PC: stopped (exit code {loc.stop()})')
    except RuntimeError:                            # no x86 venv: nothing can run here
        pass
    if a.local:
        return 0 if unreal.stop() else 1
    try:
        pi = pi_app.Pi(verbose=False)
        print(f'DTC on the Pi: stopped (exit code {pi.stop()})')
    except Exception as e:                          # Pi unreachable: still stop the local side
        print(f'DTC on the Pi: not stopped ({e})'); ok = False
    return 0 if unreal.stop() and ok else 1
# ----------------------------------------------------------------------------------------------------------------------
def cmd_clips(a, extra):
    from harness import sim
    if a.action == 'list':
        if not os.path.isdir(sim.CLIPS):
            print('no clips yet (dtc clips record NAME)'); return 0
        for n in sorted(os.listdir(sim.CLIPS)):
            m = os.path.join(sim.CLIPS, n, n + '.meta.json')
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
        for old, new in (('video.mp4', a.name + '.mp4'), ('gt.jsonl', a.name + '.gt.jsonl'), ('meta.json', a.name + '.meta.json')):
            if os.path.isfile(os.path.join(out, old)):          # sim_fly2 writes fixed names: the store's <name>.<suffix>
                os.replace(os.path.join(out, old), os.path.join(out, new))
        rows = [json.loads(l) for l in open(os.path.join(out, a.name + '.gt.jsonl'))]
        print(f'done: {len(rows)} frames, target visible in {sum(1 for r in rows if r["box"])} -> sim/{a.name}'); return 0
# ----------------------------------------------------------------------------------------------------------------------
def cmd_data(a, extra):
    from harness import datasets
    if a.action == 'prepare':
        todo = ['mot15', 'visdrone', 'antiuav'] if a.name in (None, 'all') else [a.name]
        for d in todo:
            print(f'{d}:')
            try:
                getattr(datasets, 'prepare_' + d)()
            except FileNotFoundError as e:
                print(f'   skipped - not downloaded: {e.filename}')
        return 0
    if a.action == 'convert':
        if not a.name:
            print('usage: dtc data convert FOLDER'); return 1
        datasets.frames_folder_to_source(a.name, fps=a.fps, keep_frames=a.keep_frames); return 0
    if a.action == 'list':
        sys.path.insert(0, os.path.join(ROOT, 'src'))
        import utils_sources
        vids = utils_sources.list_videos()
        if not vids:
            print('videos/ is empty: copy video files in, or dtc data migrate / prepare'); return 0
        for v in vids:
            mb = os.path.getsize(v['path']) / 1e6
            meta = os.path.isfile(utils_sources.sidecar(v['path'], utils_sources.LABEL_META))
            gt = ', '.join(('labels' if meta and k == 'one' else 'one object' if k == 'one' else 'MOT') for _, k in v['gt'])
            print(f"{v['name']:56s} {mb:8.1f} MB  {gt}")
        return 0
    if a.action == 'push':
        from harness import pi_app
        pi = pi_app.Pi(verbose=False)
        prefixes = [a.name] + list(extra) if a.name else None
        copied, failed = datasets.push(pi, prefixes)
        print(f'{copied} copied to the Pi' + (f', failed: {failed}' if failed else ', the rest was there already'))
        return 1 if failed else 0
    if a.action == 'migrate':
        todo = datasets.migrate(apply=a.apply)
        if not todo:
            print('videos/ has all of it already')
        else:
            print(f'{todo} copied / converted' if a.apply else f'{todo} to copy / convert - run again with --apply (copies only: the old folders stay)')
        return 0
# ----------------------------------------------------------------------------------------------------------------------
def cmd_test(a, extra):
    from harness import suites
    if a.local and a.suite == 'quality':
        print('quality runs on the Pi only (its limits are Pi baselines)'); return 2
    with runs.Run(suites.suite_name(a.suite, a.local)) as R:
        if a.suite == 'unit':
            suites.unit(R, sync=not a.no_sync, local=a.local)
        elif a.suite == 'smoke':
            suites.smoke(R, sync=not a.no_sync, local=a.local)
        elif a.suite == 'integration':
            suites.integration(R, sync=not a.no_sync, with_sim=a.sim, local=a.local)
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
    s = sub.add_parser('local'); s.add_argument('action', choices=['status', 'start', 'stop', 'logs', 'open']); s.add_argument('-n', type=int, default=40)
    s = sub.add_parser('sim'); s.add_argument('action', choices=['start', 'stop', 'pi']); s.add_argument('--maneuver', default='none')
    s = sub.add_parser('unreal'); s.add_argument('action', choices=['start', 'stop', 'pi', 'local']); s.add_argument('--res')
    s.add_argument('--borderless', action='store_true', help='game window without a frame')
    s = sub.add_parser('up'); s.add_argument('--unreal', action='store_true'); s.add_argument('--local', action='store_true', help='DTC on this PC, not the Pi'); s.add_argument('--res')
    s.add_argument('--borderless', action='store_true', help='game window without a frame')
    s = sub.add_parser('down'); s.add_argument('--local', action='store_true', help='this PC only, do not look for the Pi')
    s = sub.add_parser('clips'); s.add_argument('action', choices=['record', 'list']); s.add_argument('name', nargs='?')
    s.add_argument('--maneuver', default='yaw'); s.add_argument('--seconds', type=float, default=60)
    s = sub.add_parser('data'); s.add_argument('action', choices=['prepare', 'list', 'push', 'convert', 'migrate']); s.add_argument('name', nargs='?')
    s.add_argument('--fps', type=float, default=25); s.add_argument('--keep-frames', action='store_true')
    s.add_argument('--apply', action='store_true', help='migrate: do it (without: only the plan)')
    s = sub.add_parser('test'); s.add_argument('suite', choices=['unit', 'smoke', 'integration', 'quality'])
    s.add_argument('--sim', action='store_true', help='integration: also the PC simulator stream'); s.add_argument('--no-sync', action='store_true')
    s.add_argument('--local', action='store_true', help='on this x86 PC instead of the Pi -> runs/<suite>_x86/')
    from harness import bench as bench_cli
    bench_cli.add_parser(sub)
    s = sub.add_parser('runs'); s.add_argument('suite', nargs='?'); s.add_argument('-n', type=int, default=5)
    s = sub.add_parser('report'); s.add_argument('suite', nargs='?'); s.add_argument('number', nargs='?')
    a, extra = p.parse_known_args()
    if a.cmd is None:
        print(__doc__); return 0
    return {'pi': cmd_pi, 'local': cmd_local, 'sim': cmd_sim, 'unreal': cmd_unreal, 'up': cmd_up, 'down': cmd_down, 'clips': cmd_clips, 'data': cmd_data, 'test': cmd_test, 'runs': cmd_runs, 'report': cmd_report,
            'bench': lambda a, extra: bench_cli.main(a)}[a.cmd](a, extra)
# ----------------------------------------------------------------------------------------------------------------------
if __name__ == '__main__':
    sys.exit(main())
