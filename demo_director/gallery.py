"""Static gallery: every rendered aspect, side by side, no network."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

ASPECTS = ("16:9", "1:1", "9:16")
_ORDER = {"16:9": 0, "1:1": 1, "9:16": 2}


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _rel(page: str, target: str) -> str:
    return Path(os.path.relpath(target, os.path.dirname(page))).as_posix()


def render_html(title: str, sections: list[tuple[str, list[dict]]], page_path: str = "index.html") -> str:
    """sections: (heading, items). Each item has aspect, gif, and optional frames/bytes/duration."""
    blocks = []
    for heading, items in sections:
        cards = []
        for item in sorted(items, key=lambda it: _ORDER.get(it.get("aspect", ""), 9)):
            gif = _esc(_rel(page_path, item["gif"]))
            aspect = _esc(str(item.get("aspect", "")))
            bits = []
            if item.get("duration") is not None:
                bits.append(f"{float(item['duration']):.1f}s")
            if item.get("frames") is not None:
                bits.append(f"{item['frames']} frames")
            if item.get("bytes") is not None:
                bits.append(_fmt_bytes(int(item["bytes"])))
            meta = " · ".join(bits)
            cards.append(
                f"""<figure>
  <figcaption><span class="tag">{aspect}</span><span>{_esc(meta)}</span></figcaption>
  <div class="frame"><img src="{gif}" alt="{aspect} demo"></div>
</figure>"""
            )
        body = "\n".join(cards) or '<p class="sub">No renders yet.</p>'
        blocks.append(f"<section><h2>{_esc(heading)}</h2>\n<div class=\"grid\">\n{body}\n</div></section>")
    safe_title = _esc(title or "Demo")
    grids = "\n".join(blocks) or '<p class="sub">No renders yet.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} — Demo Director</title>
<style>
  :root {{
    --bg: #070b14;
    --line: #243049;
    --accent: #f97316;
    --text: #e8eefc;
    --muted: #93a0bd;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    font-family: Inter, ui-sans-serif, system-ui, sans-serif;
    color: var(--text);
    background:
      radial-gradient(1000px 520px at 8% -10%, rgba(249,115,22,.20), transparent 55%),
      radial-gradient(820px 480px at 110% 0%, rgba(56,189,248,.14), transparent 50%),
      var(--bg);
  }}
  header {{ padding: 56px 6vw 8px; max-width: 1200px; }}
  .eyebrow {{
    letter-spacing: .22em;
    font-size: 12px;
    color: var(--accent);
    font-weight: 700;
    margin: 0 0 12px;
  }}
  h1 {{
    font-size: clamp(36px, 5vw, 64px);
    line-height: .98;
    letter-spacing: -.035em;
    margin: 0 0 14px;
    font-weight: 700;
  }}
  h2 {{
    font-size: 18px;
    font-weight: 650;
    margin: 8px 6vw 0;
    color: var(--muted);
  }}
  .sub {{ color: var(--muted); font-size: 17px; max-width: 38rem; margin: 0; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 22px;
    padding: 18px 6vw 28px;
    align-items: start;
  }}
  figure {{
    margin: 0;
    background: linear-gradient(180deg, rgba(22,29,46,.92), rgba(12,16,28,.92));
    border: 1px solid var(--line);
    border-radius: 22px;
    padding: 14px;
    box-shadow: 0 24px 70px rgba(0,0,0,.35);
  }}
  figcaption {{
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: baseline;
    padding: 4px 6px 12px;
    color: var(--muted);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }}
  .tag {{ color: white; font-weight: 700; letter-spacing: .04em; }}
  .frame {{
    display: flex;
    align-items: center;
    justify-content: center;
    background:
      radial-gradient(420px 240px at 50% 42%, rgba(249,115,22,.16), transparent 70%),
      #090d16;
    border-radius: 14px;
    overflow: hidden;
    min-height: 180px;
    padding: 8px;
  }}
  img {{ max-width: 100%; max-height: 480px; width: auto; height: auto; display: block; border-radius: 8px; }}
  footer {{
    padding: 8px 6vw 48px;
    color: var(--muted);
    font-size: 13px;
    letter-spacing: .04em;
  }}
  @media (max-width: 980px) {{
    .grid {{ grid-template-columns: 1fr; }}
    img {{ max-height: 70vh; }}
  }}
</style>
</head>
<body>
<header>
  <p class="eyebrow">DEMO DIRECTOR</p>
  <h1>{safe_title}</h1>
  <p class="sub">Synthetic data only. One shot list, three aspects, recorded in a real browser.</p>
</header>
{grids}
<footer>Regenerated from the shot list. Nothing here is posted.</footer>
</body>
</html>
"""


def _items_from_folder(folder: str, report: dict | None) -> list[dict]:
    by_aspect = {}
    if report:
        for item in report.get("items") or []:
            for aspect, row in (item.get("aspects") or {}).items():
                gif = row.get("gif") or ""
                if gif and os.path.abspath(os.path.dirname(gif)) == os.path.abspath(folder):
                    by_aspect[aspect] = row
    items = []
    for aspect in ASPECTS:
        slug = aspect.replace(":", "x")
        gif = os.path.join(folder, f"demo-{slug}.gif")
        if not os.path.isfile(gif):
            continue
        row = by_aspect.get(aspect) or {}
        items.append({
            "aspect": aspect,
            "gif": gif,
            "frames": row.get("frames"),
            "bytes": row.get("bytes", os.path.getsize(gif)),
            "duration": row.get("duration"),
        })
    return items


def _read_report(folder: str) -> dict | None:
    path = os.path.join(folder, "report.json")
    if not os.path.isfile(path):
        return None
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_gallery(outdir: str, title: str = "Demo Director", report: dict | None = None) -> str:
    """Write ``index.html`` into ``outdir``. Returns the path.

    The root render is one section. Each immediate subdirectory that has demos
    is another, so a batch of several shot lists sits side by side.
    """
    os.makedirs(outdir, exist_ok=True)
    page = os.path.join(outdir, "index.html")
    root_report = report if report is not None else _read_report(outdir)
    sections: list[tuple[str, list[dict]]] = []
    root_items = _items_from_folder(outdir, root_report)
    if root_items:
        heading = title
        if root_report and len(root_report.get("items") or []) == 1:
            heading = root_report["items"][0].get("title") or title
        sections.append((heading, root_items))
    if os.path.isdir(outdir):
        for name in sorted(os.listdir(outdir)):
            folder = os.path.join(outdir, name)
            if not os.path.isdir(folder):
                continue
            sub_report = _read_report(folder) or root_report
            items = _items_from_folder(folder, sub_report)
            if not items:
                continue
            heading = name
            if sub_report:
                for item in sub_report.get("items") or []:
                    gif = ""
                    aspects = item.get("aspects") or {}
                    if aspects:
                        gif = next(iter(aspects.values())).get("gif") or ""
                    if gif and os.path.abspath(os.path.dirname(gif)) == os.path.abspath(folder):
                        heading = item.get("title") or heading
                        break
            sections.append((heading, items))
    if len(sections) == 1:
        title = sections[0][0]
    Path(page).write_text(render_html(title, sections, page))
    return page


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
