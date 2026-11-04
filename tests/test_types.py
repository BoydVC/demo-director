import json
import pathlib
import unittest

from demo_director import shotlist
from demo_director.shotlist import ShotListError, dump, parse
from demo_director.types import Aspect, Beat, Caption, Click, Goto, Hold, Type, Zoom

ROOT = pathlib.Path(__file__).resolve().parents[1]


def example():
    data = json.loads((ROOT / "examples/weekend-planner.json").read_text())
    data["url"] = (ROOT / "fixtures/app.html").as_uri()
    return data


class TypeTests(unittest.TestCase):
    def test_steps_are_a_union(self):
        sl = parse(example())
        self.assertEqual(
            [type(step) for step in sl.steps],
            [Caption, Type, Click, Caption, Click, Zoom, Hold],
        )
        self.assertIsInstance(sl.aspects[0], Aspect)
        self.assertEqual(sl.aspects, ("16:9", "1:1", "9:16"))
        self.assertEqual(Aspect.TALL.size, (720, 1280))

    def test_round_trip(self):
        sl = parse(example())
        self.assertEqual(parse(dump(sl)), sl)

    def test_illegal_variants_cannot_be_built(self):
        with self.assertRaises(TypeError):
            Click()
        with self.assertRaises(ShotListError):
            Click(selector="  ")
        with self.assertRaises(ShotListError):
            Type(selector="#a", text="")
        with self.assertRaises(ShotListError):
            Goto(url="")
        with self.assertRaises(ShotListError):
            Hold(frames=0)
        with self.assertRaises(ShotListError):
            Hold(frames=121)
        with self.assertRaises(ShotListError):
            Click(selector="#a", action="goto")

    def test_beat_carries_the_label_box(self):
        from PIL import Image
        image = Image.new("RGB", (8, 8))
        beat = Beat(image, image, (0, 0, 4, 4), Hold(frames=2), (1, 1, 6, 2))
        self.assertEqual(beat.step.action, "hold")
        self.assertEqual(beat.label_box, (1, 1, 6, 2))

    def test_fixture_token_resolves_on_load(self):
        sl = shotlist.load(str(ROOT / "examples/weekend-planner.json"))
        self.assertTrue(sl.url.startswith("file:"))
        self.assertTrue(sl.url.endswith("fixtures/app.html"))


if __name__ == "__main__":
    unittest.main()
