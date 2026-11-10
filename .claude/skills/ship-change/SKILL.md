---
name: ship-change
description: The DTC change loop - edit on the laptop, test locally, commit on a feature branch, push, sync the Pi, run the smoke suite on the Pi, look at the UI. Use for any code change that must run on the Raspberry Pi.
---

# Ship a change

1. **Branch**: work on `feature/<topic>`; `main` only on the user's word (merge = fast-forward, then `dtc pi sync`).
2. **Edit locally** (never on the Pi). Keep the file's line endings (CRLF in the working tree).
3. **Local checks**
   ```
   python -m unittest discover -s tests/unit -t .        # some tests skip without torch / PyAV - fine
   dtc test smoke --local                                 # the app on this x86 laptop (working tree, no commit needed)
   node --check src/static/bench.js                       # after JS edits
   ```
   x86-only changes (laptop launcher, Windows cameras, local harness) end here: `dtc test ... --local` is their test.
4. **Commit + push** (message: what and why; end with the attribution line the session asks for), then
   ```
   dtc pi sync
   dtc test smoke            # ~2 min on the Pi: unit, app start, frames, SOT models, benchmark API, pause, detection
   ```
   `dtc test unit` alone for non-app changes; `dtc test integration` / `quality` when sources, grabbers or SOT loss
   rules change. Each run -> `runs/<suite>/NNNN/report.md`.
5. **UI changes**: `dtc pi start --source campus`, open the page in Chrome and look at it (and the console for errors).
6. **Leave the Pi as you found it**: stop benchmarks you started; say if the app is left running and on which branch.

Report to the user: what changed, which tests ran and their result (failures with output), what was not tested.
