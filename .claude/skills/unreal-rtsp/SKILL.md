---
name: unreal-rtsp
description: Use the Unreal Engine game Night427 (packaged build on this PC) as an RTSP video source for DTC on the Raspberry Pi - what pushes the stream, the start order (mediamtx -> game on Level03 -> check -> Pi), URLs, checks and pitfalls. Use when the user wants an Unreal / "game" / Night427 RTSP stream, or a script that raises it.
---

# Unreal (Night427) as an RTSP source

Status: verified on 2026-11-05 - stream seen on the PC (ffprobe, VLC) and read by DTC on the Pi over the cable
(`rtsp://169.254.132.43:8554/stream`, 30 fps). Scripted in `harness/unreal.py`: `session_start.bat unreal` (= `dtc up unreal`), `session_start.bat` (= `dtc up`, Pi only),
`session_stop.bat` (= `dtc down`); parts: `dtc unreal start|stop|pi`. `-Manual*` flags go to the game, the rest to the Pi app.

## What pushes the stream

- UE 4.27 project `D:\Epic Games\Projects\Night427`, C++ class `ARTSPVideoPusherActor`
  (`Source/Night427/RTSPVideoPusherActor.cpp|.h`). On BeginPlay it starts ffmpeg and writes raw BGRA frames into its stdin;
  ffmpeg encodes H.264 and publishes to an RTSP server. The actor's camera follows the player's view point every frame.
- Defaults (C++; the placed actor does not override them): 1280x720 @ 30 fps, 4000 kbit/s, NVENC (`h264_nvenc`, else
  libx264), publish over **UDP**, aggressive low-latency flags, GOP ~0.5 s.
  - `RTSPUrl = rtsp://127.0.0.1:8554/stream` -> path is **`/stream`**
  - `FFmpegPath = D:/Projects/ffmpeg7/ffmpeg.exe` (must exist, otherwise no stream; the error is only in the game log)
- On-screen STATS line in the game (pushFPS, drops, Mb/s); per-session log `Saved/Logs/rtsp_session_<ts>.log` next to the build.

## Which build, which level

| build | RTSP actor |
|---|---|
| `Night427\Saved\StagedBuilds_smooth\WindowsNoEditor\Night427.exe` (exe of 2026-11-05, smooth manual camera) | **yes - use this one** |
| `Night427\Saved\StagedBuilds\WindowsNoEditor\Night427.exe` (exe of 2026-01-14, original jerky controls) | yes |
| `Night427\Build_dev\...`, `Night427\Build_ship\...` | no (older) |

The actor is **placed only in `Level03`**. Level04 (the default map) and Level05 merely reference the class - no stream there.
So the level must be passed on the command line.

## Smooth manual camera (key M)

- M switches the AirSim CameraDirector to manual mode, driven by `Plugins\AirSim\Source\ManualPoseController.cpp`: A/D yaw,
  W/S pitch, Q/E roll, arrows move, PgUp/PgDn up/down, Shift/Ctrl speed x1.5 / /1.5 per press.
- Original AirSim code turned 1 deg per *rendered frame* (rate = game FPS, e.g. 120 deg/s) and started/stopped instantly -
  SOT lost the target. Patched 2026-11-05: rates per second, linear ramp to the target rate and back to 0 on release.
- Launch flags (defaults): `-ManualYawRate=20 -ManualPitchRate=20 -ManualRollRate=20` (deg/s) `-ManualSpeed=400` (cm/s)
  `-ManualRamp=0.5` (s). The game log prints `ManualPoseController smooth: ...` with the values in use.
- Originals: `Night427\_backup_2026-11-05\` (controller sources, old Binaries exe).
- Rebuild after a C++ change (no cook needed if content is unchanged): VS 2022 Build Tools (MSVC 14.44, SDK 10.0.26100,
  installed 2026-11-05) -> `D:\Epic Games\UE_4.27\Engine\Build\BatchFiles\Build.bat Night427 Win64 Development -Project=<uproject>`
  (~70 s), then copy `Binaries\Win64\Night427.exe/.pdb` into `StagedBuilds_smooth\WindowsNoEditor\Night427\Binaries\Win64\`.

## Start order

The RTSP server must listen **before** the game starts: ffmpeg connects once at BeginPlay and does not retry.

1. RTSP server on :8554 - either of:
   - the mediamtx docker container DTC already uses: `docker start mediamtx` (`harness/sim.py:ensure_mediamtx`);
   - or the standalone exe: `D:\source\digits2\infrastructure\mediamtx\mediamtx.exe` (cwd = its folder, `mediamtx.yml`
     there: `rtspAddress: 0.0.0.0:8554`, transports udp / multicast / tcp).
   Only one of them can own :8554.
2. Game: `"D:\Epic Games\Projects\Night427\Saved\StagedBuilds_smooth\WindowsNoEditor\Night427.exe" /Game/Level03 -windowed -ResX=1600 -ResY=900`
   (window flags optional). ~10-20 s later an `ffmpeg.exe` child process from `D:\Projects\ffmpeg7` appears.
3. Check on the PC: `D:\Projects\ffmpeg7\ffprobe.exe -v error -rtsp_transport tcp -show_entries stream=codec_name,width,height -of csv=p=0 rtsp://127.0.0.1:8554/stream`
   -> `h264,1280,720`. Visual: `vlc --network-caching=100 rtsp://127.0.0.1:8554/stream`.
4. Pi: the URL is `rtsp://<PC LAN IP>:8554/stream` (`pi.laptop_ip()` gives the address the Pi sees, as in `sim.rtsp_url_for`),
   e.g. `dtc pi start --source rtsp://<PC IP>:8554/stream`.

Stop: close the game window (EndPlay closes ffmpeg's stdin, then kills it after 2 s), then mediamtx if you started it.

## Pitfalls

- No `ffmpeg.exe` process after the game loaded -> wrong level (not Level03), wrong build, or ffmpeg path missing. Look in
  `...\StagedBuilds\WindowsNoEditor\Night427\Saved\Logs\Night427.log` for `LogRTSPPusher` / `LoadMap`.
- Game started before the RTSP server -> restart the game (no reconnect).
- From the Pi: Windows firewall must allow inbound TCP 8554 (+ UDP RTP ports if the Pi reads over UDP) for whichever
  mediamtx runs; reading with `rtsp_transport tcp` on the Pi is the safer choice over Wi-Fi.
- The stream exists only while the game window lives and renders; a minimised window may slow or stall capture.
- Changing URL / resolution / ffmpeg path needs the UE 4.27 editor and a new package - nothing is read from config at runtime.
