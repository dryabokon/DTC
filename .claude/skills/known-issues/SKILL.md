---
name: known-issues
description: Gotchas in the DTC repo and its Windows/WSL/Raspberry Pi setup that already cost time - shell quoting, pkill, path conversion, line endings, VPN, Pi clock, idle pause, Escape, local test skips. Read before scripting against the Pi or patching files from the shell.
---

# Known issues

- **Bash tool + heredocs with apostrophes** (`app's`, `'...'` inside `<<'EOF'`) fail with "unexpected EOF while looking for
  matching `''`". Write the patch script to a file (Write tool, scratchpad) and run `python <file>`.
- **`pkill -f <pattern>` through `dtc pi run`** also kills its own ssh shell (the pattern is in its command line) — the
  command then returns nothing. Use a bracket pattern: `pkill -f '[b]ench_worker'`, `pgrep -af '[s]ot_'`.
- **Git Bash path conversion**: remote paths like `/tmp/x` get rewritten to `C:/.../Temp/x`. Prefix `MSYS_NO_PATHCONV=1`.
- **Line endings**: working tree CRLF, git LF. Python patchers: read with `newline=''`, replace on `\n`, write back with the
  original ending. The Edit tool is fine.
- **Full-tunnel WireGuard VPN** on the laptop blocks Windows <-> Pi completely ("General failure"); WSL still works and the
  harness uses it. Don't change the VPN — ask the user to switch it off if WSL is not enough.
- **Pi clock** ~2 days behind on the cable (no NTP): dates in Pi run folders and benchmark lists are off.
- **Idle pause**: the app holds live processing 5 s after nobody watches the video (`--idle_pause_s`, 0 = off). Anything
  that reads `/status` without a browser must start the app with `--idle_pause_s 0` (`pi_app.Pi.start` does by default).
- **Escape** in the UI: the second one quits the app — tests send it only while SOT is active.
- **Local tests on Windows**: the system Python has no torch / PyAV — SiamBAN/SiamFC and grabber tests skip or error there;
  `dtc test unit --local` runs them in the x86 venv (`.venv`), `dtc test unit` on the Pi. One flaky local failure was seen
  once and not reproduced (2026-11-04).
- **Port 8509 on the laptop** is also used by the Pi tunnel (`dtc pi open` behind a VPN) and the Fly2 UI (`dtc sim start`):
  `dtc local start` refuses to start then - add `--ui_port 8510`.
- **`os.kill(pid, 0)` on Windows** is not a liveness check (signal 0 = CTRL_C_EVENT to process group pid): use
  `utils_platform.pid_alive`. SIGTERM there is TerminateProcess (no cleanup) - the benchmark cancels through a flag file.
- **SiamBAN input is BGR** (checked: RGB is worse, IoU 0.29 vs 0.38). Torch trackers share the network, not the state.
- **`pi.py find` behind the VPN** tries only the cached address through WSL; with a new address it reports "not found".
