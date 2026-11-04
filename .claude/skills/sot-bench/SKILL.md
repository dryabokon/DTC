---
name: sot-bench
description: Run and read the DTC click-to-track (SOT) benchmark on the Raspberry Pi - presets, models, sequences, precision/recall/F1/fps, the recall/precision Gantt charts and hit/miss clips in the Runs tab, re-rendering clips, pulling runs to the laptop. Use when comparing trackers or showing tracking quality.
---

# SOT benchmark

**Where**: web UI tab **Benchmark** (configure, readiness + time estimate, Run, progress with time left, Cancel) and
**Runs** (`runs/bench/NNNN/` on the Pi). Same from the laptop:
```
dtc bench run --preset quick|standard|full [--models NANO,VIT] [--seqs campus,antiuav_val/<seq>] [--objects 3|0] [--min-size 8]
dtc bench list | pull [NNNN|all] | cancel        # pull copies the run (with clips) to runs/bench/NNNN on the laptop
```
Live video holds while a benchmark runs (fair timings) and resumes by itself. Only one benchmark at a time.

**Method** (also in `bench_worker.py`):
- Each object (the N smallest per video that are fully visible, >= min size px, for 50 frames) is tracked from its first
  full view to the end of its track, initialised once with its ground-truth box — as the app tracks (model + loss rules).
- Per frame: **hit** = box on the target (IoU >= 0.5, or centre within half the target size + 2 px — fair to tiny drones);
  **false positive** = a box not on the target; **miss** = the target without a box on it.
- precision = hit / (hit + fp), recall = hit / (hit + miss), F1; fps = 1000 / median ms of one update on the Pi.
- MOT ground truth (PETS, VisDrone) = one task per track ID; jumping to a neighbour counts as fp + miss.

**Reading a run**: models table (precision, recall, F1, fps), F1 by video, then per video two Gantt charts side by side
(left recall: turquoise hit / red miss; right precision: green hit / orange false positive; white tick = the tracker
dropped its box) and one clip `video/<seq>__<model>.mp4` (left half recall boxes, right half precision boxes).
Typical picture: precision ~0.97+, recall limited by losses the tracker never recovers from (no re-detection yet).

**Facts worth knowing**
- Pi ms/frame: NANO ~12, VIT ~16, SIAMBAN ~45, SIAMFC ~400 (minutes per task — keep it out unless asked).
- `drone_ir` (images/v30): the camera pans in jerks (~40 px/frame at frames ~34/46/64) — every tracker loses it; not a
  fair showcase. Good showcase: run 0012 on `datasets/videos/antiuav_val/*` (NANO F1 0.915 @ 81 fps).
- Sim clips (`clip:sim_*`) have 1–3 px targets: need `--min-size 2`.
- Re-make clips of a run without re-running: `dtc pi run "cd ~/sources/DTC/src && . ~/venv/p311_dtc/bin/activate && python bench_render.py ../runs/bench/NNNN"`.
- Runs older than results `version: 2` used other metrics; the UI says so — re-run to compare.
