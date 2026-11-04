# Demo Director

Shot list in → polished demo GIF/MP4 out (16:9, 1:1, 9:16), recorded from a real browser. Synthetic data only.

![demo](shots/demo-16x9.gif)

```bash
pip install playwright pillow && playwright install chromium   # plus ffmpeg
python -m unittest discover -s tests
python -m demo_director init . -o examples/weekend-planner.json
python -m demo_director batch examples/ -o shots
```

`init` reads a URL, an HTML file, or a README plus the local fixture and writes a shot list from the visible fields, buttons, and headings. `batch` renders every list, refuses pages that contain an email, a phone number, or a name in `deny_names`, and writes `report.json` plus `index.html`. A GIF over 5 MB fails the report.

Open `shots/index.html` for the gallery. Nothing here posts anywhere.
