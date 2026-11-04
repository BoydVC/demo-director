"""demo-director: a shot list in, a launch GIF out."""
from __future__ import annotations

import argparse
import json
import sys

from .batch import GIF_BUDGET, run as run_batch
from .director import direct
from .gallery import write as write_gallery
from .guard import PiiBlocked
from .propose import build
from .shotlist import load


def _render(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo-director", description="Shot list in, X-ready demo GIF/MP4 out.")
    parser.add_argument("shotlist")
    parser.add_argument("-o", "--out", default="out")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(direct(load(args.shotlist), args.out, args.scale), indent=2))
    except PiiBlocked as exc:
        print(json.dumps({"error": "pii-blocked", "kind": exc.kind, "detail": exc.detail}, indent=2), file=sys.stderr)
        return 1
    return 0


def _init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo-director init", description="Propose a shot list from a URL or a README.")
    parser.add_argument("source", help="URL, HTML file, README, or repo directory")
    parser.add_argument("-o", "--out", default="shotlist.json")
    args = parser.parse_args(argv)
    proposal = build(args.source)
    with open(args.out, "w") as handle:
        json.dump(proposal, handle, indent=2)
        handle.write("\n")
    print(args.out)
    return 0


def _batch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo-director batch", description="Render many shot lists and write report.json.")
    parser.add_argument("shotlists", nargs="+")
    parser.add_argument("-o", "--out", default="out")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--budget", type=int, default=GIF_BUDGET)
    args = parser.parse_args(argv)
    report = run_batch(args.shotlists, args.out, args.scale, args.budget)
    printable = {key: value for key, value in report.items() if key != "outputs"}
    printable["outputs"] = report["outputs"]
    print(json.dumps(printable, indent=2))
    return 0 if report["ok"] else 1


def _gallery(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo-director gallery", description="Rebuild index.html from report.json.")
    parser.add_argument("outdir")
    args = parser.parse_args(argv)
    report_path = __import__("os").path.join(args.outdir, "report.json")
    with open(report_path) as handle:
        report = json.load(handle)
    print(write_gallery(args.outdir, report))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: demo-director init <url|README> | batch <shotlists...> | gallery <dir> | <shotlist.json>")
        return 0 if argv and argv[0] in {"-h", "--help"} else 2
    if argv[0] == "init":
        return _init(argv[1:])
    if argv[0] == "batch":
        return _batch(argv[1:])
    if argv[0] == "gallery":
        return _gallery(argv[1:])
    return _render(argv)


if __name__ == "__main__":
    sys.exit(main())
