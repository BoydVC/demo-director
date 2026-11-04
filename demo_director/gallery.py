"""Static gallery: every rendered aspect side by side."""
from __future__ import annotations

import html
import os
import pathlib

from .types import BatchReport

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Demo Director</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding: 40px; font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }
  h1 { font-size: 28px; margin: 0 0 8px; }
  p.lead { color: #94a3b8; margin: 0 0 28px; }
  section { margin-bottom: 36px; }
  h2 { font-size: 18px; margin: 0 0 12px; }
  .row { display: flex; gap: 18px; align-items: flex-end; flex-wrap: nowrap; }
  figure { margin: 0; }
  img { max-height: 360px; width: auto; border-radius: 12px; background: #020617; display: block; }
  figcaption { color: #94a3b8; margin-top: 8px; font-size: 13px; }
  .err { color: #fda4af; }
</style>
</head>
<body>
<header>
  <h1>Demo Director</h1>
  <p class="lead">Synthetic shots only. These files are not posted.</p>
</header>
<!--SECTIONS-->
</body>
</html>
"""


def _section(report_source: str, title: str, figures: str, error: str | None) -> str:
    if error:
        return (
            f"<section><h2>{html.escape(report_source)}</h2>"
            f"<p class=\"err\">{html.escape(error)}</p></section>"
        )
    return f"<section><h2>{html.escape(title)}</h2><div class=\"row\">{figures}</div></section>"


def markup(report: BatchReport, outdir: str) -> str:
    sections = []
    for item in report.items:
        if item.error or not item.renders:
            sections.append(_section(item.source, item.source, "", item.error or "no output"))
            continue
        figures = []
        for render in item.renders:
            rel = pathlib.Path(os.path.relpath(render.gif, outdir)).as_posix()
            caption = f"{render.aspect.value} · {render.frames} frames · {render.gif_bytes} bytes"
            figures.append(
                "<figure>"
                f"<img src=\"{html.escape(rel, quote=True)}\" alt=\"{html.escape(render.aspect.value)}\">"
                f"<figcaption>{html.escape(caption)}</figcaption>"
                "</figure>"
            )
        sections.append(_section(item.source, item.renders[0].title, "".join(figures), None))
    return _PAGE.replace("<!--SECTIONS-->", "\n".join(sections))


def write(report: BatchReport, outdir: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "index.html")
    with open(path, "w") as handle:
        handle.write(markup(report, outdir))
    return path


def screenshot(html_path: str, png_path: str, viewport: tuple[int, int] = (1500, 900)) -> str:
    """Photograph the gallery with the same headless browser that records demos."""
    from playwright.sync_api import sync_playwright

    os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.goto(pathlib.Path(html_path).resolve().as_uri())
        height = page.evaluate("() => Math.ceil(document.body.scrollHeight)")
        page.set_viewport_size({"width": viewport[0], "height": max(320, min(int(height), viewport[1]))})
        page.screenshot(path=png_path, full_page=True)
        browser.close()
    return png_path
