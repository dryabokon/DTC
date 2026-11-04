---
name: pi-access
description: Reach the DTC Raspberry Pi 5 from the Windows laptop - find it (cable, Wi-Fi, VPN through WSL), sync code, start/stop the app, read logs, open the web UI, run a command. Use whenever a task needs the Pi.
---

# Pi access

Everything goes through the harness; it finds the Pi and checks its SSH host key against `.env` `PI_HOSTKEY` first.

```
dtc pi status                 address, Pi commit vs laptop, is DTC running (source, fps, SOT)
dtc pi sync                   Pi -> this laptop's branch + commit (GitHub, or a git bundle over SSH when the Pi is offline);
                              also puts the Pi's tools/ submodule on the pinned commit
dtc pi start --source campus  start the app (any app flags; default source campus); returns the UI URL
dtc pi stop                   clean stop (video + report written)
dtc pi logs -n 40             app log without progress noise
dtc pi open                   UI URL (behind a VPN it opens a WSL tunnel -> http://localhost:8509)
dtc pi run "<shell>"          any command on the Pi (repo: ~/sources/DTC, venv: source ~/venv/p311_dtc/bin/activate)
```
(`dtc` = `python dtc.py` from the repo root.)

## Networks

- **Patch cable** (usual): the Pi holds link-local `169.254.93.37`; laptop gets `169.254.x.x`. `pi.local` resolves via mDNS.
  UI: http://pi.local:8509 or http://169.254.93.37:8509.
- **Wi-Fi**: the Pi may be on another network than the laptop — a scan finds only routers (`SSH-2.0-ROSSSH` = MikroTik).
  If `dtc pi status` says "not found", ask the user how the Pi is connected (cable is the reliable answer).
- **Full-tunnel VPN (WireGuard) on the laptop**: Windows can't reach anything local ("General failure"); WSL can.
  The harness then runs ssh/scp inside WSL and `dtc pi open` tunnels the UI to http://localhost:8509.
  Known gap: `pi.py find` only tries the cached address through WSL — it does not scan from WSL. Don't touch the VPN.

## Notes

- `dtc pi start` keeps `idle_pause_s` (live processing holds 5 s after nobody watches the video — the Pi stays quiet);
  the test harness starts the app with `--idle_pause_s 0`.
- The Pi's fan is temperature driven (~50 C low, ~60 C medium). A running app on a video ~ step 2; benchmarks louder.
- The Pi clock is ~2 days behind on the cable (no NTP) — dates in Pi run folders are off.
- Escape in the UI: 1st stops click-to-track, 2nd quits the app.
