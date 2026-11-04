"""Static gallery: every rendered aspect side by side."""
from __future__ import annotations

import html
import os
from pathlib import Path

ASPECTS = ("16:9", "1:1", "9:16")


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _section(title: str, folder: str, prefix: str) -> str | None:
    figures = []
    for aspect in ASPECTS:
        slug = aspect.replace(":", "x")
        gif = f"demo-{slug}.gif"
        still = f"still-{slug}.png"
        if not os.path.exists(os.path.join(folder, gif)):
            continue
        src = f"{prefix}{gif}" if prefix else gif
        still_src = f"{prefix}{still}" if prefix else still
        still_html = ""
        if os.path.exists(os.path.join(folder, still)):
            still_html = f'<img src="{_esc(still_src)}" alt="{_esc(aspect)} still">'
        figures.append(
            "<figure>"
            f"{still_html}"
            f'<img src="{_esc(src)}" alt="{_esc(aspect)} demo">'
            f"<figcaption>{_esc(aspect)}</figcaption>"
            "</figure>"
        )
    if not figures:
        return None
    return f"<section><h2>{_esc(title)}</h2><div class=\"row\">{''.join(figures)}</div></section>"


def write_gallery(outdir: str, title: str = "Demo Director") -> str:
    """Write ``index.html`` into ``outdir``. Returns the path."""
    os.makedirs(outdir, exist_ok=True)
    sections = []
    top = _section(title, outdir, "")
    if top:
        sections.append(top)
    else:
        for name in sorted(os.listdir(outdir)):
            folder = os.path.join(outdir, name)
            if not os.path.isdir(folder):
                continue
            block = _section(name, folder, f"{name}/")
            if block:
                sections.append(block)
    body = "\n".join(sections) or "<p>No renders yet.</p>"
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
  body {{ margin: 0; background: #0b1020; color: #e2e8f0; font-family: system-ui, sans-serif; }}
  header {{ padding: 28px 32px 0; }}
  h1 {{ margin: 0 0 6px; font-size: 28px; }}
  h2 {{ margin: 0 0 12px; font-size: 18px; }}
  p.note {{ color: #94a3b8; margin: 0; }}
  section {{ padding: 20px 32px 36px; }}
  .row {{ display: flex; gap: 16px; align-items: flex-start; }}
  figure {{ margin: 0; background: #111827; border-radius: 12px; padding: 12px; flex: 1; min-width: 0; }}
  img {{ width: 100%; height: auto; border-radius: 8px; background: #020617; display: block; margin-bottom: 8px; }}
  figcaption {{ font-weight: 700; }}
</style>
</head>
<body>
<header>
  <h1>{_esc(title)}</h1>
  <p class="note">Synthetic data only. 16:9, 1:1, and 9:16 side by side.</p>
</header>
{body}
</body>
</html>
"""
    path = os.path.join(outdir, "index.html")
    Path(path).write_text(doc)
    return path


def screenshot_gallery(html_path: str, dest: str) -> str:
    """Open the gallery in a real browser and save a full-page PNG."""
    from playwright.sync_api import sync_playwright

    uri = Path(html_path).resolve().as_uri()
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(uri)
            page.screenshot(path=dest, full_page=True)
        finally:
            browser.close()
    return dest
