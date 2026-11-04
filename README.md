# Demo Director
Shot list in → polished demo GIF/MP4 out (16:9, 1:1, 9:16), recorded from a real browser. Synthetic data only.

![demo](shots/demo-16x9.gif)

```bash
pip install playwright pillow && playwright install chromium   # plus ffmpeg
python -m unittest discover -s tests
python -m demo_director init fixtures/app.html          # typed draft from buttons, inputs, README headings
python -m demo_director batch examples/ -o shots        # url "FIXTURE" is fixtures/app.html; GIF budget 5 MiB
python -m demo_director batch examples/ -o shots --screenshot
```

`examples/weekend-planner.json` may keep `"url": "FIXTURE"`. Renders refuse pages that contain an email, a phone number, or a name listed under `"pii": {"names": [...]}`. The gallery is `shots/index.html`. The GitHub Action document is `ci/render.yml` (push, upload artifacts, never posts). See SPEC.md.
