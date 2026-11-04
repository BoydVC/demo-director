# Demo Director — SPEC

**One line:** a JSON shot list in, an X-ready demo GIF + MP4 out, in 16:9, 1:1 and 9:16, recorded from a real headless browser.

## Why
Every BoydVC delivery must ship an animated demo GIF (synthetic data only). Today that is hand work per repo. Demo Director makes the demo a checked-in, reproducible artifact.

## Prototype (v0.1, on main)
- `demo_director/shotlist.py` — frozen `ShotList`/`Step` model, validation (`goto|click|type|caption|hold|zoom`, selector/text rules, frames 1..120, aspects).
- `demo_director/director.py` — `record()` (Playwright: before/after screenshots + target bbox per step), `frames_for()` (pure: cursor glide with smoothstep, click ripple, zoom toward target, caption), `encode()` (ffmpeg palette GIF + H.264 MP4).
- `demo_director/compose.py` — pure frame math: `ease`, `zoom_rect` (always inside frame), `fit_aspect` (blurred fill, no black bars), cursor & caption drawing.
- CLI: `python -m demo_director examples/weekend-planner.json -o shots` (set `url` to the fixture's file URI).
- Tests: `python -m unittest discover -s tests` — 8 tests incl. a real-browser end-to-end render of all 3 aspects.

## v1 goals for the arena
1. **Auto-shotlist**: `demo-director init <url|README>` proposes a shot list from visible buttons/inputs and README headings.
2. **Smart framing**: zoom keeps the whole target *and* its label visible (prototype can crop left text, see `shots/still-16x9.png`); safe-area aware for 9:16 captions.
3. **Synthetic-data guard**: refuse to render if the page text matches a PII denylist (emails, phone numbers, configurable names). Fail closed.
4. **Batch mode**: `demo-director batch demos/*.json` with a JSON report (frames, bytes, duration) and size budget (GIF ≤ 5 MB for X).
5. **Gallery**: static `index.html` showing every output side by side.
6. **CI**: GitHub Action that renders on push and uploads artifacts. No posting anywhere, ever.

## Non-goals
Posting to X, audio/voiceover, editing real user data, anything KPD.

## Acceptance
All tests pass; `demo-director batch examples/` renders 3 aspects under budget; PII guard test proves a real-looking email blocks the render; gallery screenshot committed.
