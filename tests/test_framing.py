import itertools
import json
import pathlib
import random
import unittest

from PIL import Image

from demo_director import compose
from demo_director.director import frames_for, record
from demo_director.shotlist import parse
from demo_director.types import Hold

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
        self.assertEqual(compose.ease(0), 0)
        self.assertEqual(compose.ease(1), 1)
        self.assertAlmostEqual(compose.ease(0.5), 0.5)
        self.assertGreater(compose.ease_out(0.5), 0.5)
        vals = [compose.ease(i / 10) for i in range(11)]
        self.assertEqual(vals, sorted(vals))

    def test_zoom_rect_always_inside_and_covers_label(self):
        rng = random.Random(1)
        sizes = [(1280, 800), (1280, 720), (1080, 1080), (720, 1280)]
        amounts = [0, 0.25, 0.5, 1]
        for _ in range(40):
            amounts.append(rng.random())
        for (width, height), amount in itertools.product(sizes, amounts):
            def box():
                w = rng.uniform(4, width * 0.7)
                h = rng.uniform(4, height * 0.7)
                return (rng.uniform(0, width - w), rng.uniform(0, height - h), w, h)
            target, label = box(), box()
            x, y, w, h = compose.zoom_rect(target, (width, height), amount, label)
            self.assertGreaterEqual(x, -1e-6)
            self.assertGreaterEqual(y, -1e-6)
            self.assertLessEqual(x + w, width + 1e-3, (target, label, amount, (x, y, w, h)))
            self.assertLessEqual(y + h, height + 1e-3)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
            for item in (target, label):
                self.assertGreaterEqual(item[0], x - 0.25)
                self.assertGreaterEqual(item[1], y - 0.25)
                self.assertLessEqual(item[0] + item[2], x + w + 0.25)
                self.assertLessEqual(item[1] + item[3], y + h + 0.25)

    def test_wide_target_is_not_sliced(self):
        size = (1280, 800)
        target = (36, 320, 980, 28)
        crop = compose.zoom_rect(target, size, 1)
        _inside(self, target, crop)
        self.assertGreater(crop[2], 1280 / 1.6)
        framed = compose.frame_rect(target, size, 1, target)
        _inside(self, target, framed)

    def test_right_target_keeps_left_label(self):
        target = (1100, 200, 80, 36)
        label = (40, 190, 220, 40)
        naive = compose.zoom_rect(target, (1280, 800), 1)
        self.assertLess(label[0], naive[0])
        crop = compose.frame_rect(target, (1280, 800), 1, label)
        _inside(self, target, crop)
        _inside(self, label, crop)
        self.assertGreater(crop[2], naive[2])
        self.assertLessEqual(crop[0] + crop[2], 1280.01)

    def test_unsliced_moves_off_a_cut_field(self):
        crop = (0, 164, 800, 500)
        field = (40, 137, 936, 51)
        must = ((40, 403, 490, 22),)
        settled = compose.unsliced(crop, (field,), must, (1280, 800))
        self.assertFalse(compose._hits(field, settled))
        _inside(self, must[0], settled)

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
        self.assertNotEqual(still.tobytes(), moving.tobytes())
        changed = sum(
            still.getpixel((x, y)) != moving.getpixel((x, y))
            for x in range(55, 105)
            for y in range(30, 75)
        )
        self.assertGreater(changed, 10)

    def test_title_and_end_cards(self):
        shot = Image.new("RGB", (320, 200), (15, 23, 42))
        beat = type("B", (), {})()
        beat.before = shot
        beat.after = shot
        beat.box = None
        beat.step = Hold(frames=2)
        beat.label = None
        beat.lines = ()
        frames = list(frames_for([beat], (320, 180), title="Weekend Planner", aspect="16:9",
                                 subtitle="Plan the weekend"))
        self.assertGreater(len(frames), 20)
        self.assertEqual(frames[0].size, (320, 180))
        self.assertNotEqual(frames[0].getpixel((160, 90)), frames[len(frames) // 2].getpixel((160, 90)))
        card = compose.title_card((640, 360), "Weekend Planner", 1, "Plan the weekend")
        end = compose.end_card((640, 360), "Weekend Planner", 1)
        self.assertGreater(_near_white(card), 30)
        self.assertGreater(_near_white(end), 30)
        self.assertNotEqual(list(card.getdata())[::80], list(end.getdata())[::80])

    def test_recorded_label_is_left_of_the_add_button(self):
        data = example()
        data["steps"] = [{"action": "click", "selector": "#add", "frames": 2}]
        beats = record(parse(data))
        self.assertEqual(len(beats), 1)
        box, label = beats[0].box, beats[0].label
        self.assertIsNotNone(box)
        self.assertIsNotNone(label)
        self.assertLess(label[0], box[0])
        crop = compose.frame_rect(box, beats[0].before.size, 1, label)
        _inside(self, box, crop)
        _inside(self, label, crop)
        self.assertLessEqual(crop[0] + crop[2], beats[0].before.width + 0.5)
        self.assertLessEqual(crop[1] + crop[3], beats[0].before.height + 0.5)


def _near_white(img: Image.Image) -> int:
    count = 0
    for x in range(0, img.width, 2):
        for y in range(img.height // 3, img.height // 3 + 80):
            r, g, b = img.getpixel((x, y))
            if r > 220 and g > 220 and b > 220:
                count += 1
    return count


if __name__ == "__main__":
    unittest.main()
