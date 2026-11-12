# Agent instructions

Operational rules for any coding agent (Claude Code, Codex, ...) working in this repository. Keep this file short and
current: point to the docs instead of copying them, and fix a rule here the moment it goes stale. Longer, task-shaped
know-how lives in `.claude/skills/<name>/SKILL.md` (Claude Code loads them by their description; other agents: read the
file named below before doing that task).

## What this is

DTC = detection, tracking, classification. The app (`src/main.py`) grabs a source (video file, camera, RTSP), runs YOLO
detection / DeepSORT / click-to-track (SOT) and serves a web UI on port 8509 with tabs **Live · Benchmark · Runs · Label**.
The laptop (Windows) is where code is edited and where the harness (`dtc`) drives the app. Two platforms, one code base:

| | ARM: Raspberry Pi 5 (8 GB, CPU only) | x86: this laptop (Windows, CUDA if present) |
|---|---|---|
| venv | `~/venv/p311_dtc` ← `infrastructure/build_me_arm.sh` + `requirements_arm.txt` | `<repo>\.venv` (or `DTC_VENV` / `DTC_PYTHON`) ← `infrastructure\build_me_x86.bat` + `requirements_x86.txt` |
| launcher | `app_run_pi.sh` (detector: `models/yolov8n_ncnn_model`) | `app_run_x86.bat` (detector: `yolov8n.pt`) |
| harness | `dtc pi …`, `dtc test …`, `dtc bench …` (`harness/pi_app.py`, over SSH) | `dtc local …`, `dtc test … --local`, `dtc bench … --local` (`harness/local_app.py`) |
| code | the last *commit* (git → `dtc pi sync`) | the working tree as it is |
| cameras | `/dev/videoN` (PyAV, MJPEG), `csi://N` (picamera2) | webcam indices (`camera` = `0`, DirectShow) |

Run-time differences live in `src/utils_platform.py` (process checks, detached children, platform name in `/status`);
start-up differences only in the launchers. Don't add `if platform` branches elsewhere in `src/` without need.

## Where the real docs live

- `README.md` — short overview: what the app does, labeling in brief, how to run it. The details live in the files below.
- `dtc.py` docstring — every harness command.
- `src/configs/config_common.py` — every app setting; each one is also a CLI flag (`--name value`).
- `src/utils_tracker_sot.py` — click-to-track models (`SOT_MODELS`: NANO default, VIT, SIAMBAN, SIAMFC, MIL), loss rules.
- `src/utils_bench.py`, `src/bench_worker.py`, `src/bench_render.py` — the SOT benchmark (method in their docstrings).
- `src/utils_label.py`, `src/static/label.js` — the Label tab: one object per frame, operator + tracker -> `clips/<name>/gt.jsonl`.
- `src/utils_sources.py` — what `--source` accepts (aliases, `images/`, `datasets/videos/`, cameras, URLs).
- `harness/` — `pi_app.py` (Pi control), `suites.py` (tests and `LIMITS`), `bench.py`, `datasets.py`; `infrastructure/pi.py`
  (find / sync / run on the Pi).

## Layout

| path | what | in git |
|---|---|---|
| `src/` | the app (Flask UI: `utils_UI_Flask.py`, `templates/index.html`, `static/bench.js`) | yes |
| `tools/` | submodule github.com/dryabokon/tools (DL wrappers, grabbers, drawing) — pinned commit | yes (submodule) |
| `images/vNN/` | sources: one video + `gt.txt` (MOT: frame,id,x,y,w,h,...) per folder; aliases in `utils_sources.ALIASES` | yes |
| `models/` | tracker weights (nanotrack, vittrack, siamban) | yes |
| `datasets/videos/<set>/<seq>/` | public datasets as sources (`dtc data prepare / convert / push`) | no |
| `clips/<name>/` | single-target reference clips (`video.mp4 + gt.jsonl`) | no |
| `runs/` | `runs/<suite>/NNNN/` test runs (laptop), `runs/sessions/`, `runs/bench/NNNN/` (Pi) | no |
| `.env` | Pi address / host key / user / password, `GH_PAT` | **never** |

## Hard rules

- **Git is the only way code reaches the Pi**: edit here → commit → push → `dtc pi sync`. Never edit files on the Pi
  (pscp, sed). Tests run the last *commit* on the Pi, not uncommitted changes. See `.claude/skills/ship-change`.
- Work on a feature branch; merge to `main` only when the user says so. Don't push or merge without being asked.
- Never print, commit or send `.env` values. SSH to the Pi only after its host key matched `PI_HOSTKEY` (the harness does it).
- Don't delete or overwrite data you did not create (user datasets, `images/`, frames, runs). Copy into scratch first.
- Don't change the user's VPN, routes or the Pi's network settings; never reactivate the Pi's only link remotely.
- Don't change defaults (e.g. the default SOT model) on your own — ask. Say what a measurement really measured.
- A benchmark or full test makes the Pi's fan loud; stop what you started (`dtc pi stop`, `dtc bench cancel`) when done.
  Same on the laptop (`dtc local stop`). Numbers from the laptop are not Pi numbers: say which platform measured them.

## Testing ladder

1. local: `python -m unittest discover -s tests/unit -t .` (some tests skip without torch / PyAV), or `dtc test unit --local`
   (the x86 venv, nothing skips) — seconds.
2. `dtc test unit` — the same on the Pi.
3. `dtc test smoke --local` (x86, working tree, no commit needed) while iterating; `dtc test smoke` (Pi) before every push
   that touches `src/`: app start, frames, SOT models, benchmark API, pause, detection. Local runs → `runs/<suite>_x86/`.
4. `dtc test integration [--sim] [--local]`, `dtc test quality` (Pi only) — when sources, grabbers or SOT loss rules change.
5. UI changes: open the page in Chrome and look (a syntax check misses runtime errors); `node --check src/static/bench.js`.

## Conventions

- Python: long lines (~150), `# ---...---` separators between functions, docstrings say *why*; English in code and UI.
- Files on Windows are CRLF in the working tree, LF in git (autocrlf) — patch scripts must keep the file's line endings.
- Reply to the user in Russian (their preference); keep UI text plain, numbers over judgement, no clutter.

## Skills (`.claude/skills/`)

| skill | use it to |
|---|---|
| `pi-access` | find / reach the Pi (cable, Wi-Fi, VPN through WSL), sync, start / stop the app, logs, UI URL |
| `ship-change` | the edit → test → commit → push → sync → smoke loop |
| `sot-bench` | run the click-to-track benchmark, read precision / recall / F1 / Gantt / clips, re-render clips |
| `add-dataset` | turn frames + Anti-UAV json or MOT data into a source with ground truth; screen many sequences fast |
| `known-issues` | the gotchas that already cost time (shell quoting, pkill, VPN, clock, idle pause, ...) |
| `unreal-rtsp` | use the Unreal game Night427 (packaged build on the PC) as an RTSP source `rtsp://<PC>:8554/stream` for the Pi |
