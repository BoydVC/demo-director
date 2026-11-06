# Demo Director
Shot list in → polished demo GIF/MP4 out (16:9, 1:1, 9:16), recorded from a real browser. Synthetic data only.

![demo](shots/demo-16x9.gif)

```bash
pip install playwright pillow && playwright install chromium   # plus ffmpeg
python -m unittest discover -s tests
python -m demo_director examples/weekend-planner.json -o shots   # set "url" to fixtures/app.html file URI
```
See SPEC.md.

## Family-name gate

A render that would show a built-in household or school name is blocked before any GIF, still, or gallery pick is written. An allow list cannot turn those terms back on. `reads_private_sources` stays off: the scrub tools do not open tutor sheets or an hspt bank, and they refuse KPD and law repos.

List repos a fleet summary marked name-blocked (one repo per line, no render):

```bash
python scripts/fleet_scrub.py list-blocked --summary /tmp/fleet-check/summary.json
```

Dry-run synthetic replacements for demo, fixture, and UI strings:

```bash
python scripts/fleet_scrub.py propose --set repos.txt --workdir /tmp/fleet-check
```

Commit a local `scrub/family-names` branch in each checkout, or open draft PRs after that diff looks right. `--only` repeats, so a parent can shard the list. `--clone` with `--org` fetches a missing public checkout. `--yes` is required before `open-prs` pushes anything.

```bash
python scripts/fleet_scrub.py prepare --set repos.txt --workdir /tmp/fleet-check --only some-repo
python scripts/fleet_scrub.py open-prs --set repos.txt --workdir /tmp/fleet-check --org BoydVC --yes
```
