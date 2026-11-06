"""CLI. `demo-director <shotlist>` still renders one list. init / batch / gallery are v1."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .batch import GIF_BUDGET, batch, failed
from .director import direct
from .gallery import screenshot_gallery, write_gallery
from .propose import dump_draft, init_source
from .session import RecordingFailure
from .shotlist import ShotListError, load
from .types import PiiPolicy

HELP = """usage: demo-director <shotlist.json> [-o DIR]
       demo-director init <url|README> [-o FILE]
       demo-director batch <dir-or-glob> [-o DIR]
       demo-director gallery <dir> [--screenshot FILE]
       demo-director scrub list-blocked --summary FILE
       demo-director scrub propose --set FILE --workdir DIR

Synthetic data only. Refuses a real-looking email, a phone number, or a
configured name. example.com, example.org, example.net, .test, and .example
are allowed. A blank, unstyled, or HTTP error page fails too."""


def _failure_payload(exc: RecordingFailure) -> dict:
    payload = {"status": exc.kind, "step": exc.step_index, "detail": exc.detail}
    if exc.selector:
        payload["selector"] = exc.selector
    if exc.warning:
        payload["warning"] = exc.warning
    return payload


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(HELP)
        return 0 if args and args[0] in {"-h", "--help"} else 2
    cmd = args[0]
    if cmd == "init":
        return _init(args[1:])
    if cmd == "batch":
        return _batch(args[1:])
    if cmd == "gallery":
        return _gallery(args[1:])
    if cmd == "scrub":
        from .scrub import main as scrub_main
        return scrub_main(args[1:])
    return _render(args)


def _init(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="demo-director init")
    ap.add_argument("target")
    ap.add_argument("-o", "--out")
    ap.add_argument("--allow-name", action="append", default=[])
    ap.add_argument("--deny-name", action="append", default=[])
    ap.add_argument("--derive-names", action="store_true")
    ap.add_argument("--slug", default="")
    a = ap.parse_args(argv)
    try:
        data = dump_draft(init_source(
            a.target, tuple(a.allow_name), tuple(a.deny_name), a.derive_names, slug=a.slug,
        ))
    except RecordingFailure as exc:
        print(json.dumps(_failure_payload(exc)))
        return 1
    except (ShotListError, OSError) as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}))
        return 1
    text = json.dumps(data, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


def _batch(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="demo-director batch")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--budget", type=int, default=GIF_BUDGET)
    ap.add_argument("--deny-name", action="append", default=[])
    ap.add_argument("--allow-name", action="append", default=[])
    ap.add_argument("--derive-names", action="store_true")
    ap.add_argument("--screenshot", action="store_true")
    a = ap.parse_args(argv)
    report = batch(
        a.paths, a.out, a.scale, a.budget, tuple(a.deny_name),
        screenshot=a.screenshot, allow_names=tuple(a.allow_name),
        derive_names=a.derive_names,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failed(report) else 0


def _gallery(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="demo-director gallery")
    ap.add_argument("outdir")
    ap.add_argument("--title", default="Demo Director")
    ap.add_argument("--screenshot")
    a = ap.parse_args(argv)
    path = write_gallery(a.outdir, a.title)
    if a.screenshot:
        screenshot_gallery(path, a.screenshot)
    print(path)
    return 0


def _render(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="demo-director")
    ap.add_argument("shotlist")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--deny-name", action="append", default=[])
    ap.add_argument("--allow-name", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        sl = load(a.shotlist)
    except (OSError, ShotListError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}))
        return 1
    if a.deny_name or a.allow_name:
        sl = replace(sl, pii=PiiPolicy(
            names=tuple(sl.pii.names) + tuple(a.deny_name),
            allow=tuple(sl.pii.allow) + tuple(a.allow_name),
        ))
    try:
        result = direct(sl, a.out, a.scale)
    except RecordingFailure as exc:
        print(json.dumps(_failure_payload(exc)))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
