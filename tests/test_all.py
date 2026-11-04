import json, os, pathlib, tempfile, unittest
from PIL import Image
from demo_director import shotlist, compose
from demo_director.shotlist import ShotListError

ROOT = pathlib.Path(__file__).resolve().parents[1]

def example():
    d = json.loads((ROOT / "examples/weekend-planner.json").read_text())
    d["url"] = (ROOT / "fixtures/app.html").as_uri()
    return d

class ShotListTests(unittest.TestCase):
    def test_example_parses(self):
        sl = shotlist.parse(example())
        self.assertEqual(len(sl.steps), 7); self.assertEqual(sl.aspects, ("16:9", "1:1", "9:16"))
    def test_rejects(self):
        for bad, msg in [({"action": "click"}, "selector"), ({"action": "fly"}, "unknown"),
                         ({"action": "type", "selector": "#a"}, "text"), ({"action": "hold", "frames": 0}, "frames")]:
            d = example(); d["steps"] = [bad]
            with self.assertRaisesRegex(ShotListError, msg): shotlist.parse(d)
    def test_bad_aspect(self):
        d = example(); d["aspects"] = ["4:3"]
        with self.assertRaises(ShotListError): shotlist.parse(d)

class ComposeTests(unittest.TestCase):
    def test_ease_endpoints(self):
        self.assertEqual(compose.ease(0), 0); self.assertEqual(compose.ease(1), 1); self.assertEqual(compose.ease(5), 1)
    def test_zoom_stays_in_frame(self):
        for box in [(0, 0, 10, 10), (1270, 790, 10, 10), (600, 400, 50, 50)]:
            x, y, w, h = compose.zoom_rect(box, (1280, 800), 1)
            self.assertGreaterEqual(x, 0); self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, 1280.001); self.assertLessEqual(y + h, 800.001)
            self.assertAlmostEqual(w, 1280 / 1.6)
    def test_no_zoom_is_full(self):
        self.assertEqual(compose.zoom_rect(None, (100, 50), 1), (0, 0, 100, 50))
    def test_fit_aspect_sizes(self):
        img = Image.new("RGB", (1280, 800), "red")
        for out in [(720, 1280), (1080, 1080), (1280, 720)]:
            self.assertEqual(compose.fit_aspect(img, out).size, out)

class EndToEnd(unittest.TestCase):
    def test_render_real_browser(self):
        from demo_director.director import direct
        out = tempfile.mkdtemp()
        res = direct(shotlist.parse(example()), out, scale=0.4)
        for asp, (W, H) in shotlist.ASPECTS.items():
            g = Image.open(res[asp]["gif"])
            self.assertEqual(g.size, (int(W * .4) // 2 * 2, int(H * .4) // 2 * 2))
            self.assertGreater(g.n_frames, 40)
            self.assertGreater(os.path.getsize(res[asp]["mp4"]), 1000)

if __name__ == "__main__":
    unittest.main()
