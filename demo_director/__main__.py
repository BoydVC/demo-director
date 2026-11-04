import argparse, json
from .shotlist import load
from .director import direct

ap = argparse.ArgumentParser(prog="demo-director", description="Shot list in, X-ready demo GIF/MP4 out.")
ap.add_argument("shotlist"); ap.add_argument("-o", "--out", default="out")
ap.add_argument("--scale", type=float, default=0.6)
a = ap.parse_args()
print(json.dumps(direct(load(a.shotlist), a.out, a.scale), indent=2))
