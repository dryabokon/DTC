# DTC: Detection, Tracking, and Classification 
![alt text](/images/Image1.jpg)


## Setup
`tools/` is a git submodule (github.com/dryabokon/tools, pinned commit):
```
git clone --recursive https://github.com/dryabokon/DTC      # or, in an existing clone: git submodule update --init
```
Raspberry Pi: `python infrastructure/pi.py sync` puts the Pi's checkout and its `tools/` on this laptop's branch and commits
(from GitHub, or as git bundles over SSH when the Pi is offline).

## Harness: everyday operations
One command, `dtc` (`dtc.bat` on Windows, or `python dtc.py`). The Pi is found by itself (`.env`: `PI_HOST`, `PI_HOSTKEY`,
`PI_MACS`, `PI_USER`, `PI_PASSWORD`): at home, over a direct cable, on a phone hotspot.
```
dtc pi status                     where the Pi is, its commit vs the laptop's, is DTC running (source, fps, SOT)
dtc pi sync                       Pi -> this laptop's branch and commit
dtc pi start --source camera      start DTC on the Pi (any app flags), dtc pi stop / logs / open / run "<cmd>"
dtc sim start                     Fly2 camera simulator on this PC -> rtsp://<PC>:8554/sim (camera moved in the Fly2 UI :8509)
dtc sim pi                        DTC on the Pi on the simulator stream (drone detector ready)
dtc clips record NAME --maneuver yaw|yaw_fast|sweep|none --seconds 60     reference clip -> clips/NAME
```

## Web UI keys
Click / drag on the video: track a box (SOT) · right click, Esc, C: stop tracking (Esc again quits) ·
Space: pause / resume (video files) · ->: next frame while paused · R / Home / ⏮: video from the start · H: hide the panel.
Click-to-track model: the "Click-to-track" dropdown in the panel, or `--sot_tracking_model NANO|VIT|SIAMBAN|SIAMFC|MIL`
(default NANO; `--sot_score_th X` overrides the per-model loss threshold). Details and Pi timings: `utils_tracker_sot.SOT_MODELS`,
comparison: `python tests/quality/sot_models.py out.json` (on the Pi).
Behind a full-tunnel VPN (Windows blocked, WSL not) the harness reaches the Pi through WSL; `dtc pi open` gives the UI on
http://localhost:8509.

## Tests
```
dtc test unit          tests/unit on the Pi (unittest, seconds)
dtc test smoke         ~1 min, before every push: unit + start, frames, SOT click/Esc, detection on, clean exit
dtc test integration   ~2 min: CLI, sources (file, 1080p, dead URL, camera), SOT, Esc; --sim: PC simulator + drone MOT
dtc test quality       ~6 min: metrics with limits vs the previous run - SOT loss detection (PETS ground truth),
                       SOT on reference clips (clips/), app FPS per mode
dtc runs               latest runs and verdicts;   dtc report [suite] [NNNN]
```
Every run writes `runs/<suite>/NNNN/`: `report.md` (checks, metrics vs previous run), `metrics.json`, `manifest.json`
(laptop / tools / Pi commits, dirty flag, command, times, verdict), `log.txt`, and the Pi artifacts (`pi/`: app log,
df_time / df_track, video). Interactive app sessions go to `runs/sessions/NNNN/` on the Pi.
Thresholds live in `harness/suites.py` (`LIMITS`). The tests run the last **commit** on the Pi, not uncommitted changes.

Reference clips (`clips/<name>/video.mp4 + gt.jsonl + meta.json`, exact target box per frame from Fly2) are local only,
not in git; `dtc test quality` copies new ones to the Pi.
