"""Static gallery: every rendered aspect, side by side, no network."""
from __future__ import annotations

import html
import os
from pathlib import Path

ORDER = {"16:9": 0, "1:1": 1, "9:16": 2}


def _rel(page: str, target: str) -> str:
    return os.path.relpath(target, os.path.dirname(page))


def _fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def render_html(title: str, items: list[dict], page_path: str = "index.html") -> str:
    """items: aspect, gif, frames, bytes, duration, still (optional). Paths are absolute or relative to page_path."""
    cards = []
    for item in sorted(items, key=lambda it: ORDER.get(it.get("aspect", ""), 9)):
        gif = html.escape(_rel(page_path, item["gif"]))
        aspect = html.escape(str(item.get("aspect", "")))
        frames = item.get("frames")
        duration = item.get("duration")
        size = item.get("bytes")
        bits = []
        if duration is not None:
            bits.append(f"{duration:.1f}s")
        if frames is not None:
            bits.append(f"{frames} frames")
        if size is not None:
            bits.append(_fmt_bytes(int(size)))
        meta = " · ".join(bits)
        cards.append(
            f"""<figure>
  <figcaption><span class="tag">{aspect}</span><span>{html.escape(meta)}</span></figcaption>
  <div class="frame"><img src="{gif}" alt="{aspect} demo"></div>
</figure>"""
        )
    body = "\n".join(cards) or "<p class=\"sub\">No renders yet.</p>"
    safe_title = html.escape(title or "Demo")
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
  .sub {{ color: var(--muted); font-size: 17px; max-width: 38rem; margin: 0; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 22px;
    padding: 36px 6vw 72px;
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
    padding: 0 6vw 48px;
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
<main class="grid">
{body}
</main>
<footer>Regenerated from the shot list.</footer>
</body>
</html>
"""


def write(outdir: str, report: dict) -> str:
    page = os.path.join(outdir, "index.html")
    items = [item for item in report.get("outputs", []) if item.get("gif")]
    title = next((item.get("title") for item in items if item.get("title")), "Demo")
    Path(page).write_text(render_html(title, items, page))
    return page
