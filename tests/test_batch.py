import inspect
import json
import pathlib
import tempfile
import unittest

from PIL import Image

from demo_director.batch import GIF_BUDGET, aspect_record, batch, expand_inputs, status_for
from demo_director.ci import workflow_text, workflow_violations
from demo_director.director import direct, frames_for, record
from demo_director.gallery import write_gallery
from demo_director.types import Click

ROOT = pathlib.Path(__file__).resolve().parents[1]


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
        self.assertEqual(row["duration"], 1.0)
        self.assertEqual(status_for([row]), "over-budget")
        self.assertEqual(status_for([aspect_record(10, 100, 10, GIF_BUDGET, "g", "m")]), "ok")

    def test_frames_are_deterministic(self):
        img = Image.new("RGB", (320, 200), (15, 23, 42))
        step = Click("#add", frames=4)
        beat = type("B", (), {})()
        beat.before = img
        beat.after = img.copy()
        beat.box = (220, 40, 40, 30)
        beat.step = step
        beat.label = (10, 40, 200, 30)
        beat.lines = ()
        first = list(frames_for([beat], (160, 90)))
        second = list(frames_for([beat], (160, 90)))
        self.assertEqual(len(first), len(second))
        self.assertGreater(len(first), 1)
        self.assertEqual(first[0].tobytes(), second[0].tobytes())
        self.assertEqual(first[-1].tobytes(), second[-1].tobytes())

    def test_examples_batch_is_under_budget(self):
        out = tempfile.mkdtemp()
        report = batch([str(ROOT / "examples")], out, scale=0.25)
        self.assertTrue(report["ok"], report)
        titles = sorted(item["title"] for item in report["items"])
        self.assertEqual(titles, ["Kit Check", "Weekend Planner demo"])
        for item in report["items"]:
            self.assertEqual(item["status"], "ok")
            self.assertEqual(list(item["aspects"]), ["16:9", "1:1", "9:16"])
            for row in item["aspects"].values():
                self.assertTrue(row["within_budget"])
                self.assertLessEqual(row["bytes"], GIF_BUDGET)
                self.assertGreater(row["frames"], 40)
                self.assertGreater(row["bytes"], 1000)
                self.assertEqual(row["duration"], round(row["frames"] / 10, 3))
        html = (pathlib.Path(out) / "index.html").read_text()
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            self.assertGreaterEqual(html.count(name), 2)

    def test_gallery_escapes_and_lists_every_aspect(self):
        folder = tempfile.mkdtemp()
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            pathlib.Path(folder, name).write_bytes(b"GIF89a")
        text = pathlib.Path(write_gallery(folder, "Weekend & Planner")).read_text()
        self.assertIn("Weekend &amp; Planner", text)
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            self.assertIn(name, text)
        self.assertIn("Synthetic data only", text)
        self.assertNotIn("<script", text.lower())

    def test_workflow_renders_on_push_and_does_not_post(self):
        text = workflow_text()
        self.assertEqual(workflow_violations(text), [])
        self.assertIn("upload-artifact", text)
        self.assertIn("push", text)
        self.assertTrue((ROOT / "ci" / "render.yml").is_file())
        self.assertFalse((ROOT / ".github" / "workflows").exists())


if __name__ == "__main__":
    unittest.main()
