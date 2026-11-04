"""v1 goals: PII guard, smart framing, init, batch, gallery, CI plan."""
import inspect
import itertools
import json
import pathlib
import random
import tempfile
import unittest

from PIL import Image

from demo_director import compose, guard, shotlist
from demo_director.batch import GIF_BUDGET, aspect_record, batch, expand_inputs, status_for
from demo_director.ci import workflow_text, workflow_violations
from demo_director.director import Beat, direct, frames_for, record
from demo_director.gallery import write_gallery
from demo_director.propose import propose
from demo_director.session import RecordingFailure
from demo_director.shotlist import Step, ShotListError

ROOT = pathlib.Path(__file__).resolve().parents[1]


def example():
    data = json.loads((ROOT / "examples/weekend-planner.json").read_text())
    data["url"] = (ROOT / "fixtures/app.html").as_uri()
    return data


class GuardTests(unittest.TestCase):
    def test_fixture_and_synthetic_copy_are_clean(self):
        page = (ROOT / "fixtures/app.html").read_text()
        self.assertEqual(guard.hits(page), ())
        for line in (
            "Sat 10:30 · Game vs Comets",
            "Sat 10:00 · Scrimmage, Field 1",
            "1 conflict: Sat 10:00 overlaps Practice → suggest carpool swap",
            "Team Alpha",
        ):
            self.assertEqual(guard.hits(line), (), line)

    def test_email_phone_and_configured_name(self):
        self.assertIn("email", guard.hits("Reach the coach at coach@example.com."))
        self.assertIn("phone", guard.hits("Call (415) 555-0134 today"))
        self.assertIn("phone", guard.hits("555-010-0199"))
        self.assertIn("phone", guard.hits("+1 202 555 0148"))
        self.assertIn("name", guard.hits("signed up as Casey Example", ("Casey Example",)))
        self.assertEqual(guard.hits("Team Alpha", ()), ())
        self.assertIn("name", guard.hits("hello Team Alpha", ("team alpha",)))

    def test_fail_closed_on_unreadable_or_weak_name(self):
        self.assertEqual(guard.hits(None), ("unreadable",))
        self.assertEqual(guard.hits(12), ("unreadable",))
        self.assertIn("name-policy", guard.hits("hello", ("Al",)))
        with self.assertRaises(RecordingFailure) as ctx:
            guard.assert_clean(None, ())
        self.assertEqual(ctx.exception.kind, "pii-blocked")

    def test_deny_names_parse(self):
        data = example()
        data["deny_names"] = ["Casey Example"]
        self.assertEqual(shotlist.parse(data).deny_names, ("Casey Example",))
        data["deny_names"] = "Casey Example"
        with self.assertRaises(ShotListError):
            shotlist.parse(data)

    def test_real_email_blocks_the_render(self):
        data = example()
        data["url"] = (ROOT / "fixtures/pii.html").as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        out = tempfile.mkdtemp()
        with self.assertRaises(RecordingFailure) as ctx:
            direct(shotlist.parse(data), out, scale=0.3)
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)
        self.assertEqual(list(pathlib.Path(out).glob("demo-*.gif")), [])

    def test_configured_name_blocks_the_render(self):
        data = example()
        data["deny_names"] = ["Team Alpha"]
        data["steps"] = [{"action": "hold", "frames": 2}]
        with self.assertRaises(RecordingFailure) as ctx:
            direct(shotlist.parse(data), tempfile.mkdtemp(), scale=0.3)
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("name", ctx.exception.detail)

    def test_missing_selector_is_typed(self):
        data = example()
        data["steps"] = [{"action": "click", "selector": "#no-such-control", "frames": 2}]
        with self.assertRaises(RecordingFailure) as ctx:
            record(shotlist.parse(data))
        self.assertEqual(ctx.exception.kind, "selector-missing")


class FramingTests(unittest.TestCase):
    def test_zoom_rect_always_inside_and_covers_label(self):
        rng = random.Random(1)
        sizes = [(1280, 800), (1280, 720), (1080, 1080), (720, 1280)]
        amounts = [0, 0.25, 0.5, 1]
        for _ in range(80):
            amounts.append(rng.random())
        for (W, H), amount in itertools.product(sizes, amounts):
            def box():
                w = rng.uniform(4, W * 0.7)
                h = rng.uniform(4, H * 0.7)
                return (rng.uniform(0, W - w), rng.uniform(0, H - h), w, h)
            target, label = box(), box()
            x, y, w, h = compose.zoom_rect(target, (W, H), amount, label)
            self.assertGreaterEqual(x, -1e-6)
            self.assertGreaterEqual(y, -1e-6)
            self.assertLessEqual(x + w, W + 1e-3, (target, label, amount, (x, y, w, h)))
            self.assertLessEqual(y + h, H + 1e-3)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
            for b in (target, label):
                self.assertGreaterEqual(b[0], x - 0.25)
                self.assertGreaterEqual(b[1], y - 0.25)
                self.assertLessEqual(b[0] + b[2], x + w + 0.25)
                self.assertLessEqual(b[1] + b[3], y + h + 0.25)

    def test_right_target_keeps_left_label(self):
        # The v0.1 still cropped the input sitting to the left of Add.
        target = (992, 110, 80, 48)
        label = (40, 110, 900, 48)
        x, y, w, h = compose.zoom_rect(target, (1280, 800), 1, label)
        self.assertGreaterEqual(label[0], x - 0.2)
        self.assertLessEqual(target[0] + target[2], x + w + 0.2)
        self.assertLessEqual(x + w, 1280.01)
        self.assertLessEqual(y + h, 800.01)
        self.assertGreater(w, 1280 / 1.6)

    def test_portrait_caption_stays_out_of_the_bottom_band(self):
        size = (360, 640)
        left, top, right, bottom = compose.safe_insets(size)
        x0, y0, x1, y1 = compose.caption_placement(size, 180, 22)
        self.assertGreaterEqual(x0, left - 0.01)
        self.assertLessEqual(x1, size[0] - right + 0.01)
        self.assertGreaterEqual(y0, top - 0.01)
        self.assertLessEqual(y1, size[1] - bottom + 0.01)
        img = Image.new("RGB", size, (10, 10, 20))
        compose.draw_caption(img, "One tap finds conflicts")
        band = img.crop((0, size[1] - bottom + 1, size[0], size[1]))
        pixels = band.size[0] * band.size[1]
        self.assertEqual(band.getcolors(pixels), [(pixels, (10, 10, 20))])
        above = img.crop((left, 0, size[0] - right, size[1] - bottom))
        self.assertGreater(len(above.getcolors(pixels) or []), 1)

    def test_recorded_label_is_left_of_the_add_button(self):
        data = example()
        data["steps"] = [{"action": "click", "selector": "#add", "frames": 2}]
        beats = record(shotlist.parse(data))
        self.assertEqual(len(beats), 1)
        box, label = beats[0].box, beats[0].label
        self.assertIsNotNone(box)
        self.assertIsNotNone(label)
        self.assertLess(label[0], box[0])
        x, y, w, h = compose.zoom_rect(box, beats[0].before.size, 1, label)
        self.assertGreaterEqual(label[0], x - 0.5)
        self.assertLessEqual(box[0] + box[2], x + w + 0.5)
        self.assertLessEqual(x + w, beats[0].before.width + 0.5)
        self.assertLessEqual(y + h, beats[0].before.height + 0.5)


class BatchTests(unittest.TestCase):
    def test_no_bypass_parameter(self):
        for fn in (batch, direct, record):
            for name in inspect.signature(fn).parameters:
                lowered = name.lower()
                for banned in ("skip", "bypass", "force", "disable"):
                    self.assertNotIn(banned, lowered, fn.__name__)

    def test_paths_sort_and_budget_math(self):
        self.assertEqual(expand_inputs(["b.json", "a.json", "a.json"]), ["a.json", "b.json"])
        self.assertEqual(GIF_BUDGET, 5 * 1024 * 1024)
        row = aspect_record(10, GIF_BUDGET + 1, 10, GIF_BUDGET, "g.gif", "g.mp4")
        self.assertFalse(row["within_budget"])
        self.assertEqual(row["duration_s"], 1.0)
        self.assertEqual(status_for([row]), "over-budget")
        self.assertEqual(status_for([aspect_record(10, 100, 10, GIF_BUDGET, "g", "m")]), "ok")

    def test_frames_are_deterministic(self):
        img = Image.new("RGB", (320, 200), (15, 23, 42))
        step = Step("click", "#add", None, None, 4)
        beat = Beat(img, img.copy(), (220, 40, 40, 30), step, (10, 40, 200, 30))
        first = list(frames_for([beat], (160, 90)))
        second = list(frames_for([beat], (160, 90)))
        self.assertEqual(len(first), len(second))
        self.assertGreater(len(first), 1)
        self.assertEqual(first[0].tobytes(), second[0].tobytes())
        self.assertEqual(first[-1].tobytes(), second[-1].tobytes())

    def test_batch_pii_writes_no_gif(self):
        folder = tempfile.mkdtemp()
        data = example()
        data["url"] = (ROOT / "fixtures/pii.html").as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        path = pathlib.Path(folder) / "blocked.json"
        path.write_text(json.dumps(data))
        out = tempfile.mkdtemp()
        report = batch([str(path)], out, scale=0.3)
        self.assertEqual(report["items"][0]["status"], "pii-blocked")
        self.assertIn("email", report["items"][0]["error"])
        self.assertEqual(report["items"][0]["aspects"], {})
        self.assertEqual(list(pathlib.Path(out).glob("demo-*.gif")), [])

    def test_batch_example_renders_three_aspects_under_budget(self):
        out = tempfile.mkdtemp()
        report = batch([str(ROOT / "examples")], out, scale=0.25)
        self.assertEqual(len(report["items"]), 1)
        item = report["items"][0]
        self.assertEqual(item["status"], "ok")
        self.assertEqual(list(item["aspects"]), ["16:9", "1:1", "9:16"])
        for row in item["aspects"].values():
            self.assertTrue(row["within_budget"])
            self.assertLessEqual(row["bytes"], GIF_BUDGET)
            self.assertGreater(row["frames"], 40)
            self.assertEqual(row["duration_s"], round(row["frames"] / 10, 3))
            self.assertGreater(row["bytes"], 1000)
        html = (pathlib.Path(out) / "index.html").read_text()
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            self.assertIn(name, html)
        again = json.loads((pathlib.Path(out) / "report.json").read_text())
        self.assertEqual(again["items"][0]["shotlist"], item["shotlist"])
        self.assertEqual(
            [row["frames"] for row in again["items"][0]["aspects"].values()],
            [row["frames"] for row in item["aspects"].values()],
        )


class InitTests(unittest.TestCase):
    def test_readme_headings_become_captions(self):
        folder = tempfile.mkdtemp()
        path = pathlib.Path(folder) / "README.md"
        path.write_text("# Weekend Planner\n\n## One tap finds conflicts\n\nNot a heading.\n")
        data = propose(str(path))
        sl = shotlist.parse(data)
        self.assertEqual([s.text for s in sl.steps if s.action == "caption"],
                         ["Weekend Planner", "One tap finds conflicts"])
        self.assertEqual(sl.steps[-1].action, "hold")
        self.assertNotIn("Not a heading", json.dumps(data))

    def test_readme_email_refuses(self):
        folder = tempfile.mkdtemp()
        path = pathlib.Path(folder) / "README.md"
        path.write_text("# Notes\n\ncoach@example.com\n")
        with self.assertRaises(RecordingFailure) as ctx:
            propose(str(path))
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)

    def test_page_proposes_visible_controls(self):
        data = propose(str(ROOT / "fixtures" / "app.html"))
        sl = shotlist.parse(data)
        selectors = [s.selector for s in sl.steps if s.selector]
        self.assertEqual(selectors[:3], ["#ev", "#add", "#pack"])
        typed = [s for s in sl.steps if s.action == "type"]
        self.assertEqual(typed[0].text, "Sample value")
        self.assertIn("Weekend Planner", sl.steps[0].text)

    def test_pii_page_refuses_init(self):
        with self.assertRaises(RecordingFailure) as ctx:
            propose(str(ROOT / "fixtures" / "pii.html"))
        self.assertEqual(ctx.exception.kind, "pii-blocked")


class GalleryAndCITests(unittest.TestCase):
    def test_gallery_lists_every_aspect(self):
        folder = tempfile.mkdtemp()
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif", "still-16x9.png"):
            pathlib.Path(folder, name).write_bytes(b"x")
        text = pathlib.Path(write_gallery(folder, "Weekend & Planner")).read_text()
        self.assertIn("Weekend &amp; Planner", text)
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif", "still-16x9.png"):
            self.assertIn(name, text)
        self.assertIn("9:16", text)

    def test_workflow_renders_on_push_and_does_not_post(self):
        text = workflow_text()
        self.assertEqual(workflow_violations(text), [])
        self.assertIn("upload-artifact", text)
        self.assertIn("push", text)
        self.assertTrue((ROOT / "ci" / "render-on-push.yml").is_file())
