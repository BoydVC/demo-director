import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from demo_director.batch import GIF_BUDGET_BYTES, expand_paths, render_paths, report_for_outputs, within_budget
from demo_director.gallery import markup
from demo_director.types import Aspect, BatchItem, BatchReport, RenderReport

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def test_budget_fails_closed(self):
        self.assertTrue(within_budget(1000))
        self.assertTrue(within_budget(GIF_BUDGET_BYTES))
        self.assertFalse(within_budget(GIF_BUDGET_BYTES + 1))
        self.assertFalse(within_budget(0))
        self.assertFalse(within_budget(-1))
        tmp = pathlib.Path(tempfile.mkdtemp())
        gif = tmp / "big.gif"
        mp4 = tmp / "big.mp4"
        gif.write_bytes(b"0" * (GIF_BUDGET_BYTES + 1))
        mp4.write_bytes(b"1" * 2000)
        report = report_for_outputs("Big", Aspect.WIDE, 50, 10, str(gif), str(mp4), GIF_BUDGET_BYTES)
        self.assertFalse(report.within_budget)
        self.assertEqual(report.gif_bytes, GIF_BUDGET_BYTES + 1)
        self.assertAlmostEqual(report.duration_s, 5.0)
        self.assertFalse(BatchReport((BatchItem("big.json", (report,), None),), GIF_BUDGET_BYTES).ok)
        with self.assertRaises(FileNotFoundError):
            report_for_outputs("Big", Aspect.WIDE, 50, 10, str(tmp / "missing.gif"), str(mp4), GIF_BUDGET_BYTES)
        found, missing = expand_paths([str(tmp / "nope")])
        self.assertEqual(found, [])
        self.assertEqual(missing, [str(tmp / "nope")])
        empty = render_paths([str(tmp / "nope")], str(tmp / "out"))
        self.assertFalse(empty.ok)
        self.assertIsNotNone(empty.items[0].error)

    def test_gallery_row_lists_every_aspect(self):
        renders = []
        for aspect, name in ((Aspect.WIDE, "demo-16x9.gif"), (Aspect.SQUARE, "demo-1x1.gif"), (Aspect.TALL, "demo-9x16.gif")):
            renders.append(RenderReport("Weekend", aspect, 70, 12000, 8000, 7.0, name, name.replace(".gif", ".mp4"), True))
        report = BatchReport((BatchItem("examples/weekend-planner.json", tuple(renders), None),), GIF_BUDGET_BYTES)
        html = markup(report, ".")
        row = html.split('class="row"', 1)[1].split("</section>", 1)[0]
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            self.assertIn(name, row)
        self.assertEqual(row.count("<figure>"), 3)

    def test_examples_batch_is_under_budget(self):
        out = tempfile.mkdtemp()
        proc = subprocess.run(
            [sys.executable, "-m", "demo_director", "batch", "examples", "-o", out, "--scale", "0.25"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertLessEqual(report["budget_bytes"], 5 * 1024 * 1024)
        renders = report["items"][0]["renders"]
        self.assertEqual([item["aspect"] for item in renders], ["16:9", "1:1", "9:16"])
        for item in renders:
            self.assertGreater(item["frames"], 40)
            self.assertGreater(item["bytes"], 1000)
            self.assertLessEqual(item["bytes"], report["budget_bytes"])
            self.assertTrue(item["within_budget"])
            self.assertGreater(item["duration"], 0)
            self.assertIn("bytes", item)
            self.assertIn("duration", item)
        page = (pathlib.Path(out) / "index.html").read_text()
        row = page.split('class="row"', 1)[1]
        for name in ("demo-16x9.gif", "demo-1x1.gif", "demo-9x16.gif"):
            self.assertIn(name, row)


if __name__ == "__main__":
    unittest.main()
