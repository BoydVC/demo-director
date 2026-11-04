import pathlib
import unittest

from PIL import Image

from demo_director import compose
from demo_director.director import record
from demo_director.shotlist import load

ROOT = pathlib.Path(__file__).resolve().parents[1]


class FramingTests(unittest.TestCase):
    def test_zoom_keeps_a_wide_target_fully_visible(self):
        # Centered 1.6x zoom on this card is 800px wide and clips the left text.
        card = (40, 200, 1100, 64)
        x, y, w, h = compose.zoom_rect(card, (1280, 800), 1)
        self.assertLessEqual(x, 40)
        self.assertGreaterEqual(x + w, 1140)
        self.assertLessEqual(y, 200)
        self.assertGreaterEqual(y + h, 264)
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + w, 1280.01)
        self.assertLessEqual(y + h, 800.01)

    def test_zoom_keeps_the_label_left_of_a_target(self):
        button = (1080, 140, 90, 44)
        label = (36, 120, 700, 48)
        x, y, w, h = compose.zoom_rect(button, (1280, 800), 1, label)
        self.assertLessEqual(x, 36)
        self.assertGreaterEqual(x + w, 1170)
        self.assertLessEqual(y, 120)
        self.assertGreaterEqual(y + h, 184)

    def test_tall_caption_stays_in_the_safe_area(self):
        shot = Image.new("RGB", (400, 800), (15, 23, 42))
        frame = compose.render(shot, (0, 0, 400, 800), None, 0, "One tap finds conflicts", (360, 640), "9:16")
        inset = compose.safe_insets((360, 640), "9:16")
        self.assertGreaterEqual(inset[3], int(640 * 0.16))
        bottom = frame.crop((0, 640 - inset[3], 360, 640))
        brightest = max(band[1] for band in bottom.getextrema())
        self.assertLess(brightest, 80)
        # The caption itself is still drawn, above that band.
        above = frame.crop((0, 0, 360, 640 - inset[3]))
        self.assertGreater(max(band[1] for band in above.getextrema()), 200)

    def test_recording_keeps_label_and_text_ink(self):
        beats = record(load(str(ROOT / "examples/weekend-planner.json")))
        add = next(beat for beat in beats if getattr(beat.step, "selector", None) == "#add")
        conflict = next(beat for beat in beats if getattr(beat.step, "selector", None) == "#conf")
        self.assertLessEqual(add.label_box[0], 40)
        self.assertGreater(add.label_box[2], add.box[2])
        # The conflict div stretches across the page; the framed target is its text.
        self.assertLess(conflict.box[2], 700)
        self.assertGreater(conflict.box[2], 200)
        crop = compose.zoom_rect(conflict.box, (1280, 800), 1, conflict.label_box)
        self.assertLessEqual(crop[0], conflict.box[0])
        self.assertGreaterEqual(crop[0] + crop[2], conflict.box[0] + conflict.box[2])


if __name__ == "__main__":
    unittest.main()
