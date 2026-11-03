"""Run folders for the test harness: runs/<suite>/0001, 0002, ...

Every run gets
    manifest.json   what was run: commits (laptop, tools, Pi), dirty flag, host, command, times, verdict
    metrics.json    the numbers checks are judged on (compared with the previous run of the same suite)
    report.md       human-readable summary: checks table, metrics vs previous run, artifacts
    log.txt         everything the harness printed
plus whatever artifacts the checks save (pi/ logs, app run folders, frames, videos).
"""
import datetime
import json
import os
import platform
import socket
import subprocess
import sys
import time
# ----------------------------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, 'runs')
# ----------------------------------------------------------------------------------------------------------------------
def git(*args, cwd=ROOT):
    r = subprocess.run(['git', '-C', cwd] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout.strip()
# ----------------------------------------------------------------------------------------------------------------------
def numbered(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(int(d) for d in os.listdir(folder) if d.isdigit() and os.path.isdir(os.path.join(folder, d)))
# ----------------------------------------------------------------------------------------------------------------------
def redact(text):
    """Never write the Pi password into logs or reports (plink gets it on its command line)."""
    try:
        sys.path.insert(0, os.path.join(ROOT, 'infrastructure'))
        import pi
        pw = pi.ENV.get('PI_PASSWORD')
    except Exception:
        pw = None
    return text.replace(pw, '***') if pw else text
# ----------------------------------------------------------------------------------------------------------------------
class Run:
    """One harness run. Use as a context manager; checks are recorded with check()."""
    def __init__(self, suite, argv=None):
        self.suite = suite
        base = os.path.join(RUNS, suite)
        os.makedirs(base, exist_ok=True)
        n = numbered(base)
        self.number = (n[-1] + 1) if n else 1
        self.folder = os.path.join(base, '%04d' % self.number)
        os.makedirs(self.folder)
        prev = [k for k in n if os.path.isfile(os.path.join(base, '%04d' % k, 'metrics.json')) and os.path.getsize(os.path.join(base, '%04d' % k, 'metrics.json')) > 2]
        self.previous = os.path.join(base, '%04d' % prev[-1]) if prev else None
        self.checks = []                       # (name, status, detail, seconds)  status: PASS | FAIL | SKIP
        self.metrics = {}                      # name -> {'value': v, 'unit': u, 'min': lo, 'max': hi, 'better': 'higher'|'lower'}
        self.notes = []
        self.t0 = time.time()
        self._log = open(os.path.join(self.folder, 'log.txt'), 'w', encoding='utf-8')
        self.manifest = {
            'suite': suite, 'run': '%04d' % self.number,
            'started': datetime.datetime.now().isoformat(timespec='seconds'),
            'command': ' '.join(argv or sys.argv),
            'laptop': {'host': socket.gethostname(), 'os': platform.platform(), 'python': platform.python_version(),
                       'branch': git('rev-parse', '--abbrev-ref', 'HEAD'), 'commit': git('rev-parse', '--short', 'HEAD'),
                       'dirty': bool(git('status', '--porcelain', '--untracked-files=no')),
                       'tools_commit': git('rev-parse', '--short', 'HEAD', cwd=os.path.join(ROOT, 'tools')) if os.path.isdir(os.path.join(ROOT, 'tools')) else None},
        }
        self.log(f'== {suite} run {self.number:04d} -> {self.folder}')
        if self.manifest['laptop']['dirty']:
            self.log('   note: uncommitted local changes - the Pi runs the last COMMIT, not your working tree')

    # ------------------------------------------------------------------------------------------------------------------
    def log(self, *a):
        line = redact(' '.join(str(x) for x in a))
        print(line, flush=True)
        self._log.write(line + '\n'); self._log.flush()

    def path(self, *parts):
        p = os.path.join(self.folder, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    # ------------------------------------------------------------------------------------------------------------------
    def check(self, name, ok, detail='', skip=False, seconds=None):
        status = 'SKIP' if skip else ('PASS' if ok else 'FAIL')
        self.checks.append((name, status, str(detail), seconds))
        mark = {'PASS': 'ok  ', 'FAIL': 'FAIL', 'SKIP': 'skip'}[status]
        self.log(f'   [{mark}] {name}' + (f' - {detail}' if detail else ''))
        return ok

    def metric(self, name, value, unit='', min=None, max=None, better='higher'):
        """Record a number; with min/max it is also a check."""
        self.metrics[name] = {'value': value, 'unit': unit, 'min': min, 'max': max, 'better': better}
        if min is not None or max is not None:
            ok = value is not None and (min is None or value >= min) and (max is None or value <= max)
            lim = ' '.join(x for x in [f'>= {min}' if min is not None else '', f'<= {max}' if max is not None else ''] if x)
            self.check(f'metric {name}', ok, f'{value} {unit} (needs {lim})'.strip())
        else:
            self.log(f'   [info] {name} = {value} {unit}')

    def section(self, title):
        self.log(f'-- {title}')

    # ------------------------------------------------------------------------------------------------------------------
    def verdict(self):
        if any(s == 'FAIL' for _, s, _, _ in self.checks):
            return 'FAIL'
        return 'PASS' if any(s == 'PASS' for _, s, _, _ in self.checks) else 'EMPTY'

    def _previous_metrics(self):
        if not self.previous:
            return {}
        try:
            return json.load(open(os.path.join(self.previous, 'metrics.json')))
        except Exception:
            return {}

    def finish(self):
        verdict = self.verdict()
        self.manifest.update(finished=datetime.datetime.now().isoformat(timespec='seconds'),
                             duration_s=round(time.time() - self.t0, 1), verdict=verdict,
                             checks={s: sum(1 for _, x, _, _ in self.checks if x == s) for s in ('PASS', 'FAIL', 'SKIP')})
        json.dump(self.manifest, open(os.path.join(self.folder, 'manifest.json'), 'w'), indent=2)
        json.dump(self.metrics, open(os.path.join(self.folder, 'metrics.json'), 'w'), indent=2)
        self._write_report(verdict)
        self.log(f'== {self.suite} {self.number:04d}: {verdict}  '
                 f'({self.manifest["checks"]["PASS"]} pass, {self.manifest["checks"]["FAIL"]} fail, {self.manifest["checks"]["SKIP"]} skip, '
                 f'{self.manifest["duration_s"]} s)  report: {os.path.join(self.folder, "report.md")}')
        self._log.close()
        return verdict

    def _write_report(self, verdict):
        m, prev = self.manifest, self._previous_metrics()
        L = [f'# {self.suite} {m["run"]}: **{verdict}**', '',
             f'- started {m["started"]}, {m.get("duration_s")} s',
             f'- laptop `{m["laptop"]["branch"]}` @ `{m["laptop"]["commit"]}`' + (' (+ uncommitted changes, not on the Pi)' if m['laptop']['dirty'] else '') +
             f', tools `{m["laptop"]["tools_commit"]}`']
        if 'pi' in m:
            L.append(f'- Pi `{m["pi"].get("host")}` @ `{m["pi"].get("commit")}`')
        L += [f'- command: `{m["command"]}`', '', '## Checks', '', '| | check | detail |', '|---|---|---|']
        L += [f'| {s} | {n} | {d.replace("|", "/")} |' for n, s, d, _ in self.checks]
        if self.metrics:
            L += ['', '## Metrics' + (f' (vs {self.suite} {os.path.basename(self.previous)})' if self.previous else ''), '',
                  '| metric | value | limit | previous | change |', '|---|---|---|---|---|']
            for k, v in self.metrics.items():
                lim = ' '.join(x for x in [f'>= {v["min"]}' if v['min'] is not None else '', f'<= {v["max"]}' if v['max'] is not None else ''] if x)
                p = prev.get(k, {}).get('value')
                ch = ''
                if isinstance(p, (int, float)) and isinstance(v['value'], (int, float)) and p:
                    d = (v['value'] - p) / abs(p) * 100
                    worse = d < -5 if v['better'] == 'higher' else d > 5
                    ch = f'{d:+.0f}%' + (' **worse**' if worse else '')
                L.append(f'| {k} | {v["value"]} {v["unit"]} | {lim} | {"" if p is None else p} | {ch} |')
        if self.notes:
            L += ['', '## Notes', ''] + [f'- {n}' for n in self.notes]
        arts = sorted(os.path.relpath(os.path.join(d, f), self.folder).replace('\\', '/')
                      for d, _, fs in os.walk(self.folder) for f in fs if f not in ('report.md',))
        L += ['', '## Artifacts', ''] + [f'- `{a}`' for a in arts[:60]] + ([f'- ... {len(arts) - 60} more'] if len(arts) > 60 else [])
        text = redact('\n'.join(L) + '\n')
        open(os.path.join(self.folder, 'report.md'), 'w', encoding='utf-8').write(text)

    # ------------------------------------------------------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, et, e, tb):
        if et is not None and et is not KeyboardInterrupt:
            import traceback
            self.check('harness', False, f'{et.__name__}: {e}')
            self.log(''.join(traceback.format_exception(et, e, tb)))
        elif et is KeyboardInterrupt:
            self.check('harness', False, 'interrupted (Ctrl+C)')
        self.finish()
        return et is not None and et is not KeyboardInterrupt
