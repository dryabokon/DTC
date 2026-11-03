import os, sys
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SRC = os.path.join(REPO, 'src')
for p in (SRC, os.path.join(SRC, 'configs'), os.path.join(REPO, 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(SRC)                     # the app resolves ../images, ../models relative to src/
