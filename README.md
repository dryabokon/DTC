# DTC: Detection, Tracking, and Classification 
![alt text](/images/Image1.jpg)


## Setup
`tools/` is a git submodule (github.com/dryabokon/tools, pinned commit):
```
git clone --recursive https://github.com/dryabokon/DTC      # or, in an existing clone: git submodule update --init
```
Raspberry Pi: `python infrastructure/pi.py sync` puts the Pi's checkout and its `tools/` on this laptop's branch and commits
(from GitHub, or as git bundles over SSH when the Pi is offline).
