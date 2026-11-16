---
name: add-dataset
description: Add video with ground truth to DTC as a source (for the app and the SOT benchmark) - Anti-UAV style folders of frames + IR_label.json, MOT gt.txt, public datasets; and screen many sequences quickly with the app's tracker before converting. Use when the user points at a dataset folder.
---

# Add a dataset

**The store** (`utils_sources`): every video lives under `videos/` (any depth, several per folder); its ground truth lies
next to it with the same name - `<name>.gt.jsonl` (one object: `{"frame": 0-based, "box": [x1,y1,x2,y2]}` per line) or
`<name>.gt.txt` (MOT `frame(1-based),id,x,y,w,h,...`, many objects). Frames without the target have no row. A video
named like its folder is listed by the folder: `videos/set/seq/seq.mp4` -> `set/seq`.

**Anti-UAV style folder** (`000001.jpg ... + IR_label.json` with `exist`, `gt_rect` [x,y,w,h], optional attributes
LR/OC/FM/SV/DBC/... per frame):
```
dtc data convert <folder> [--fps 25] [--keep-frames]     # -> <folder>/IR.mp4 (name from the json) + IR.gt.jsonl
```
or from Python `harness.datasets.frames_folder_to_source(folder, name=..., fps=25, keep_frames=True)`.
Without `--keep-frames` it deletes the frames — never run that on the user's folder: copy into scratch first, convert there,
move `<name>.mp4` + `<name>.gt.jsonl` into `videos/<set>/<seq>/`.

**Where to put it**
- always `videos/<set>/<seq>/` (not in git); optional short alias in `utils_sources.ALIASES`. It shows up at once in
  Live, Label and Benchmark. `dtc data push <set>/` copies it to the Pi (add the set to `datasets.FOR_TESTS` if the tests
  or presets need it there).
- public sets (MOT15, VisDrone, Anti-UAV300 from `datasets/_downloads`): `dtc data prepare [mot15|visdrone|antiuav|all]`.

**Many sequences — screen first, convert only the useful ones** (no conversion needed to screen): run the app's tracker
(`utils_tracker_sot.Tracker_SOT`, loss rules on) straight over the frames + json on the laptop and score each frame with
`bench_worker.frame_state` / `prf`; rank by F1, keep target size, max centre jump and attributes per sequence. 90
Anti-UAV val sequences x 600 frames took ~10 min for NANO on the laptop (33 had F1 >= 0.95, 32 < 0.3).
Then convert the chosen ones and run `dtc bench run --seqs <set>/<seq>,... --objects 1` on the Pi.
