"""v1 behaviour: framing, cards, PII guard, init, batch, gallery."""
import json
import pathlib
import shutil
import tempfile
import unittest

from PIL import Image

from demo_director import compose, guard, propose, shotlist
from demo_director.batch import GIF_BUDGET, run as run_batch
from demo_director.director import Beat, direct, frames_for, record
from demo_director.gallery import render_html
from demo_director.guard import PiiBlocked
from demo_director.shotlist import Step
from demo_director.__main__ import main

ROOT = pathlib.Path(__file__).resolve().parents[1]


def example():
    data = json.loads((ROOT / "examples/weekend-planner.json").read_text())
    data["url"] = (ROOT / "fixtures/app.html").as_uri()
    return data


def _inside(test, box, crop):
    x, y, w, h = crop
    test.assertGreaterEqual(box[0] + 0.6, x)
    test.assertGreaterEqual(box[1] + 0.6, y)
    test.assertLessEqual(box[0] + box[2], x + w + 0.6)
    test.assertLessEqual(box[1] + box[3], y + h + 0.6)


class FramingTests(unittest.TestCase):
    def test_ease_settles(self):
        self.assertAlmostEqual(compose.ease(0.5), 0.5)
        self.assertGreater(compose.ease_out(0.5), 0.5)
        vals = [compose.ease(i / 10) for i in range(11)]
        self.assertEqual(vals, sorted(vals))

    def test_zoom_keeps_target_and_label(self):
        size = (1280, 800)
        target = (1100, 200, 80, 36)
        label = (40, 190, 220, 40)
        naive = compose.zoom_rect(target, size, 1)
        self.assertLess(label[0], naive[0])
        crop = compose.frame_rect(target, size, 1, label)
        _inside(self, target, crop)
        _inside(self, label, crop)
        self.assertGreaterEqual(crop[0], 0)
        self.assertGreaterEqual(crop[1], 0)
        self.assertLessEqual(crop[0] + crop[2], size[0] + 0.01)
        self.assertLessEqual(crop[1] + crop[3], size[1] + 0.01)
        self.assertGreater(crop[2], naive[2])

    def test_unsliced_moves_off_a_cut_field(self):
        crop = (0, 164, 800, 500)
        field = (40, 137, 936, 51)
        must = ((40, 403, 490, 22),)
        settled = compose.unsliced(crop, (field,), must, (1280, 800))
        self.assertFalse(compose._hits(field, settled))
        _inside(self, must[0], settled)

    def test_wide_target_is_not_sliced(self):
        size = (1280, 800)
        target = (36, 320, 980, 28)
        crop = compose.frame_rect(target, size, 1, target)
        _inside(self, target, crop)
        self.assertGreater(crop[2], 1280 / 1.6)

    def test_caption_safe_area_on_vertical(self):
        size = (360, 640)
        text = "Plan the whole weekend in 10 seconds without crowding the chrome"
        rect = compose.caption_rect(size, text, "9:16")
        wide = compose.caption_rect((640, 360), text, "16:9")
        self.assertIsNotNone(rect)
        x, y, w, h = rect
        self.assertGreaterEqual(x, size[0] * 0.07 - 1)
        self.assertLessEqual(x + w, size[0] * 0.93 + 1)
        self.assertLessEqual(y + h, size[1] * (1 - 0.18) + 1)
        gap9 = (size[1] - (y + h)) / size[1]
        gap16 = (360 - (wide[1] + wide[3])) / 360
        self.assertGreater(gap9, 0.16)
        self.assertGreater(gap9, gap16)

    def test_cursor_has_shadow_and_motion_trail(self):
        still = Image.new("RGB", (220, 140), (15, 23, 42))
        moving = still.copy()
        compose.draw_cursor(still, (110, 40), velocity=(0, 0))
        compose.draw_cursor(moving, (110, 40), velocity=(48, 4))
        self.assertNotEqual(list(still.getdata()), list(moving.getdata()))
        self.assertGreater(_changed(still, moving, (55, 30, 105, 75)), 10)
        self.assertGreater(_off_background(still, (15, 23, 42)), 12)

    def test_title_and_end_cards(self):
        shot = Image.new("RGB", (320, 200), (15, 23, 42))
        beat = Beat(shot, shot, None, Step("hold", frames=2))
        frames = list(frames_for([beat], (320, 180), title="Weekend Planner", aspect="16:9",
                                 subtitle="Plan the weekend"))
        self.assertGreater(len(frames), 20)
        self.assertEqual(frames[0].size, (320, 180))
        self.assertNotEqual(frames[0].getpixel((4, 4)), frames[len(frames) // 2].getpixel((4, 4)))
        card = compose.title_card((640, 360), "Weekend Planner", 1, "Plan the weekend")
        end = compose.end_card((640, 360), "Weekend Planner", 1)
        self.assertGreater(_near_white(card), 30)
        self.assertGreater(_near_white(end), 30)
        self.assertNotEqual(list(card.getdata())[::80], list(end.getdata())[::80])


def _changed(a: Image.Image, b: Image.Image, box: tuple[int, int, int, int]) -> int:
    x0, y0, x1, y1 = box
    return sum(
        a.getpixel((x, y)) != b.getpixel((x, y))
        for x in range(x0, x1)
        for y in range(y0, y1)
    )


def _off_background(img: Image.Image, bg: tuple[int, int, int]) -> int:
    return sum(1 for px in img.getdata() if px != bg)


def _near_white(img: Image.Image) -> int:
    count = 0
    for x in range(0, img.width, 2):
        for y in range(img.height // 3, img.height // 3 + 80):
            r, g, b = img.getpixel((x, y))
            if r > 220 and g > 220 and b > 220:
                count += 1
    return count


class GuardTests(unittest.TestCase):
    def test_patterns_and_fail_closed(self):
        self.assertEqual(guard.scan("coach@example.com")[0][0], "email")
        self.assertEqual(guard.scan("Call (415) 555-0134 today")[0][0], "phone")
        self.assertEqual(guard.scan("+1 415 555 0134")[0][0], "phone")
        self.assertEqual(guard.scan("Notes for Casey Nguyen", ["Casey Nguyen"])[0][0], "name")
        self.assertEqual(guard.scan("Team Alpha", ["Al"]), [])
        self.assertEqual(guard.scan("Sat 10:00 · Scrimmage, Field 1"), [])
        self.assertNotIn("coach@example.com", guard.scan("coach@example.com")[0][1])
        with self.assertRaises(PiiBlocked) as caught:
            guard.assert_clean(None)
        self.assertEqual(caught.exception.kind, "unreadable")

    def test_email_on_the_page_blocks_the_render(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        page = tmp / "pii.html"
        page.write_text("<!doctype html><meta charset=utf-8><title>x</title>"
                        "<body><h1>Contact</h1><p>coach@example.com</p></body></html>")
        sl = shotlist.parse({
            "title": "Blocked",
            "url": page.as_uri(),
            "aspects": ["16:9"],
            "steps": [{"action": "hold", "frames": 2}],
        })
        out = tmp / "out"
        with self.assertRaises(PiiBlocked) as caught:
            direct(sl, str(out), scale=0.3)
        self.assertEqual(caught.exception.kind, "email")
        self.assertFalse(list(out.glob("*.gif")))

    def test_configured_name_on_the_page_blocks(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        page = tmp / "name.html"
        page.write_text("<!doctype html><body><h1>Notes for Casey Nguyen</h1></body></html>")
        sl = shotlist.parse({
            "title": "Notes",
            "url": page.as_uri(),
            "deny_names": ["Casey Nguyen"],
            "aspects": ["16:9"],
            "steps": [{"action": "hold", "frames": 2}],
        })
        with self.assertRaises(PiiBlocked) as caught:
            direct(sl, str(tmp / "out"), scale=0.3)
        self.assertEqual(caught.exception.kind, "name")

    def test_email_typed_in_the_shot_list_blocks(self):
        sl = shotlist.parse({
            "title": "Nope",
            "url": "file:///tmp/does-not-need-to-exist.html",
            "steps": [{"action": "type", "selector": "#a", "text": "coach@example.com", "frames": 2}],
        })
        with self.assertRaises(PiiBlocked) as caught:
            direct(sl, tempfile.mkdtemp(), scale=0.3)
        self.assertEqual(caught.exception.kind, "email")


class InitTests(unittest.TestCase):
    def test_init_from_readme_proposes_a_runnable_demo(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "fixtures").mkdir()
        shutil.copy(ROOT / "fixtures/app.html", tmp / "fixtures" / "app.html")
        readme = tmp / "README.md"
        readme.write_text("# Weekend Planner\n\n## One tap finds conflicts\n\nSynthetic data only.\n")
        out = tmp / "shotlist.json"
        code = main(["init", str(readme), "-o", str(out)])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text())
        sl = shotlist.parse(data)
        actions = [step.action for step in sl.steps]
        self.assertIn("caption", actions)
        self.assertIn("type", actions)
        self.assertIn("click", actions)
        self.assertIn("zoom", actions)
        self.assertEqual(sl.aspects, ("16:9", "1:1", "9:16"))
        texts = " ".join(step.text or "" for step in sl.steps)
        self.assertIn("Weekend Planner", texts)
        self.assertIn("One tap finds conflicts", texts)
        self.assertTrue(any(step.selector == "#add" for step in sl.steps))
        self.assertTrue(any(step.selector == "#ev" for step in sl.steps))
        self.assertNotIn("@", texts)
        self.assertNotIn("http://", json.dumps(data))
        self.assertNotIn("https://", json.dumps(data))


class BatchAndGalleryTests(unittest.TestCase):
    def test_batch_report_blocks_pii_and_enforces_budget(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        clean = tmp / "clean.json"
        dirty = tmp / "dirty.json"
        page = tmp / "pii.html"
        page.write_text("<!doctype html><body><p>coach@example.com</p></body></html>")
        clean.write_text(json.dumps({
            "title": "Clean",
            "url": (ROOT / "fixtures/app.html").as_uri(),
            "aspects": ["16:9"],
            "fps": 8,
            "steps": [{"action": "hold", "frames": 2}],
        }))
        dirty.write_text(json.dumps({
            "title": "Dirty",
            "url": page.as_uri(),
            "aspects": ["16:9"],
            "steps": [{"action": "hold", "frames": 2}],
        }))
        out = tmp / "out"
        report = run_batch([str(clean), str(dirty)], str(out), scale=0.3, budget=1)
        self.assertFalse(report["ok"])
        self.assertEqual(GIF_BUDGET, 5 * 1024 * 1024)
        self.assertTrue(any(item.get("kind") == "email" for item in report["outputs"]))
        rendered = [item for item in report["outputs"] if "frames" in item]
        self.assertEqual(len(rendered), 1)
        for key in ("frames", "bytes", "duration"):
            self.assertIn(key, rendered[0])
        self.assertGreater(rendered[0]["bytes"], 1)
        self.assertTrue(rendered[0]["over_budget"])
        self.assertGreater(rendered[0]["duration"], 0)
        blob = pathlib.Path(report["report"]).read_text()
        self.assertNotIn("coach@example.com", blob)
        self.assertTrue((out / "index.html").is_file())
        code = main(["batch", str(dirty), "-o", str(tmp / "cli-out")])
        self.assertEqual(code, 1)

    def test_gallery_is_static_and_lists_every_aspect(self):
        items = []
        for aspect, name in (("16:9", "demo-16x9.gif"), ("1:1", "demo-1x1.gif"), ("9:16", "demo-9x16.gif")):
            items.append({
                "title": "Weekend Planner",
                "aspect": aspect,
                "gif": "/tmp/shots/" + name,
                "frames": 80,
                "bytes": 400000,
                "duration": 6.5,
            })
        page = render_html("Weekend Planner", items, "/tmp/shots/index.html")
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            self.assertIn(name, page)
        self.assertIn("Synthetic data only", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("<script", page.lower())


class RecordedFramingTests(unittest.TestCase):
    def test_add_button_keeps_its_label_in_frame(self):
        beats = record(shotlist.parse(example()))
        add = next(beat for beat in beats if getattr(beat.step, "selector", None) == "#add")
        self.assertIsNotNone(add.label)
        self.assertLess(add.label[0], add.box[0] - 40)
        naive = compose.zoom_rect(add.box, add.before.size, 1)
        self.assertLess(add.label[0], naive[0])
        crop = compose.frame_rect(add.box, add.before.size, 1, add.label)
        _inside(self, add.box, crop)
        _inside(self, add.label, crop)
        conf = next(beat for beat in beats if getattr(beat.step, "selector", None) == "#conf")
        self.assertLess(conf.box[2], conf.before.width * 0.9)
        held = compose.frame_rect(conf.box, conf.before.size, 1, conf.label or conf.box)
        _inside(self, conf.box, held)
        if conf.label:
            _inside(self, conf.label, held)


class LoadTests(unittest.TestCase):
    def test_relative_fixture_resolves(self):
        sl = shotlist.load(str(ROOT / "examples/weekend-planner.json"))
        self.assertTrue(sl.url.startswith("file:"))
        self.assertTrue(sl.url.endswith("fixtures/app.html"))
        self.assertEqual(len(sl.steps), 7)


if __name__ == "__main__":
    unittest.main()
