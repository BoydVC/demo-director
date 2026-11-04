"""demo-director init | batch | render."""
from __future__ import annotations

import argparse
import json
import sys

from .batch import GIF_BUDGET_BYTES, render_paths
from .discover import dump_draft, init_source
from .types import PiiBlocked, ShotListError


def _budget(megabytes: float) -> int:
    return int(megabytes * 1024 * 1024)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"init", "batch", "render", "-h", "--help"}:
        argv = ["render", *argv]

    parser = argparse.ArgumentParser(
        prog="demo-director",
        description="Shot list in, X-ready demo GIF/MP4 out. Synthetic data only. Never posts.",
    )
    commands = parser.add_subparsers(dest="cmd", required=True)

    init = commands.add_parser("init", help="propose a typed shot list from a URL or a README")
    init.add_argument("source")
    init.add_argument("-o", "--out", help="also write the draft JSON here")

    batch = commands.add_parser("batch", help="render every shot list and write a JSON report")
    batch.add_argument("paths", nargs="+")
    batch.add_argument("-o", "--out", default="out")
    batch.add_argument("--scale", type=float, default=0.6)
    batch.add_argument("--budget-mb", type=float, default=GIF_BUDGET_BYTES / (1024 * 1024))
    batch.add_argument("--screenshot", action="store_true", help="also write gallery.png")

    render = commands.add_parser("render", help="render one shot list")
    render.add_argument("shotlist")
    render.add_argument("-o", "--out", default="out")
    render.add_argument("--scale", type=float, default=0.6)
    render.add_argument("--budget-mb", type=float, default=GIF_BUDGET_BYTES / (1024 * 1024))
    render.add_argument("--screenshot", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            payload = dump_draft(init_source(args.source))
            text = json.dumps(payload, indent=2)
            if args.out:
                with open(args.out, "w") as handle:
                    handle.write(text + "\n")
            print(text)
            return 0
        paths = args.paths if args.cmd == "batch" else [args.shotlist]
        report = render_paths(paths, args.out, args.scale, _budget(args.budget_mb), shoot=args.screenshot)
    except (PiiBlocked, ShotListError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
