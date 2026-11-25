#!/usr/bin/env python3
#
# Download (or delete) the separated tracks for a song.
#
#   python3 get-tracks.py <songhash>            # saves out/<songhash>-<track>.mp3
#   python3 get-tracks.py <songhash> --remove   # deletes them from the server
#
import os
import sys

import requests

REST = os.getenv("REST") or "localhost:5000"
TRACKS = ("bass", "drums", "vocals", "other")

if len(sys.argv) < 2:
    sys.exit(f"usage: {sys.argv[0]} <songhash> [--remove]")
songhash = sys.argv[1]
remove = "--remove" in sys.argv[2:]

os.makedirs("out", exist_ok=True)
for track in TRACKS:
    if remove:
        r = requests.delete(f"http://{REST}/apiv1/remove/{songhash}/{track}.mp3")
        print(f"remove {track}: {r.status_code} {r.text.strip()}")
        continue
    r = requests.get(f"http://{REST}/apiv1/track/{songhash}/{track}.mp3")
    if r.status_code == 200:
        path = f"out/{songhash}-{track}.mp3"
        with open(path, "wb") as f:
            f.write(r.content)
        print(f"saved {path} ({len(r.content)} bytes)")
    else:
        print(f"{track}: {r.status_code} {r.text.strip()}")
