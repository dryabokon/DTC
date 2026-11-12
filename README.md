# DTC: Detection, Tracking, Classification

Video in (file, USB / CSI camera, RTSP) → YOLO detection, DeepSORT tracking, click-to-track → web UI on http://localhost:8509.
Runs on a Raspberry Pi 5 (CPU) and on a Windows PC (CUDA if present) from the same code.

![DTC](/images/Image1.jpg)

## What you can do

| tab | |
|---|---|
| **Live** | watch the source, turn on detection / tracking, click or drag on an object to follow it (Esc stops) |
| **Label** | make ground truth for one object in any video: draw a box once, the tracker does the rest |
| **Benchmark** | compare click-to-track models (NANO, VIT, SIAMBAN, SIAMFC, MIL) on labeled videos |
| **Runs** | benchmark results: precision / recall / F1 / fps, per-video charts, hit / miss clips |

## Labeling in 30 seconds

![Label tab](/docs/label_tab.jpg)

You open a video and draw one box around the person you need. The tracker takes it from there and follows them
through the frames. When it slips onto someone else, you scroll to that spot and draw the box once more: the
tracker fixes the frames around it and goes on. A quick replay to check, and the video is labeled: it is saved
as you go and is ready to use as ground truth in the benchmark.

## Run

```
git clone --recursive https://github.com/dryabokon/DTC
infrastructureuild_me_x86.bat
app_run_x86.bat --source campus
```

Open http://localhost:8509. Any video file, camera or RTSP stream works as `--source`.
On a Raspberry Pi: `infrastructure/build_me_arm.sh`, then `./app_run_pi.sh`. More: `dtc.py`, `AGENTS.md`.
