import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from demo_director.propose import Control, PageControls, draft_from, dump_draft, init_source, synthetic_value
from demo_director.session import RecordingFailure
from demo_director.shotlist import parse
from demo_director.types import Caption, Click, Draft, Hold, Type, Zoom

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "app.html"


class InitTests(unittest.TestCase):
    def test_draft_is_typed_and_synthetic(self):
        page = PageControls(
            title="Weekend Planner",
            controls=(
                Control("#ev", "input", "Add an event…"),
                Control("#email", "input", "person@example.com"),
                Control("#add", "button", "Add"),
                Control("", "button", "Skip me"),
            ),
        )
        draft = draft_from("Ignored", "file:///app.html", ("Weekend board", "See conflicts"), page)
        self.assertIsInstance(draft, Draft)
        self.assertEqual(len(draft.notes), len(draft.shotlist.steps))
        self.assertIsInstance(draft.shotlist.steps[0], Caption)
        self.assertEqual(draft.shotlist.steps[0].text, "Weekend board")
        self.assertTrue(any(isinstance(step, Caption) and step.text == "See conflicts" for step in draft.shotlist.steps))
        typed = [step for step in draft.shotlist.steps if isinstance(step, Type)]
        self.assertEqual([step.selector for step in typed], ["#ev", "#email"])
        self.assertEqual(typed[0].text, "Add an event (demo)")
        self.assertEqual(typed[1].text, "Sample")
        self.assertNotIn("@", typed[1].text)
        clicks = [step for step in draft.shotlist.steps if isinstance(step, Click)]
        self.assertEqual([step.selector for step in clicks], ["#add"])
        self.assertIsInstance(draft.shotlist.steps[-1], Hold)
        again = parse(dump_draft(draft))
        self.assertEqual(again.steps, draft.shotlist.steps)
        self.assertEqual(synthetic_value("alex.demo@gmail.com"), "Sample")

    def test_result_zoom_lands_before_the_hold(self):
        page = PageControls("Kit Check", (Control("#item", "input", "Add a kit item…"),))
        draft = draft_from("Kit Check", "FIXTURE", (), page, ("#status",))
        self.assertIsInstance(draft.shotlist.steps[-2], Zoom)
        self.assertEqual(draft.shotlist.steps[-2].selector, "#status")
        self.assertIsInstance(draft.shotlist.steps[-1], Hold)

    def test_init_readme_uses_headings_and_visible_controls(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        readme = tmp / "README.md"
        readme.write_text(
            "# Weekend board\n\n## Add a game\n\nThe app is [here](%s).\n\n## See conflicts\n" % FIXTURE
        )
        draft = init_source(str(readme))
        self.assertIsInstance(draft, Draft)
        texts = [step.text for step in draft.shotlist.steps if isinstance(step, Caption)]
        self.assertIn("Weekend board", texts)
        self.assertIn("Add a game", texts)
        self.assertIn("See conflicts", texts)
        self.assertTrue(any(isinstance(step, Click) and step.selector == "#add" for step in draft.shotlist.steps))
        self.assertTrue(any(isinstance(step, Type) and step.selector == "#ev" for step in draft.shotlist.steps))
        self.assertTrue(any(isinstance(step, Zoom) for step in draft.shotlist.steps))
        for step in draft.shotlist.steps:
            if isinstance(step, Type):
                self.assertNotIn("Practice, Field 3", step.text)
                self.assertNotIn("@", step.text)
        proc = subprocess.run(
            [sys.executable, "-m", "demo_director", "init", str(FIXTURE)],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        sl = parse(data)
        self.assertEqual(len(data["sources"]), len(sl.steps))
        self.assertTrue(any(isinstance(step, Click) and step.selector == "#pack" for step in sl.steps))
        self.assertTrue(any(isinstance(step, Type) and step.selector == "#ev" for step in sl.steps))
        self.assertNotIn("http://", proc.stdout)
        self.assertNotIn("https://", proc.stdout)

    def test_readme_email_refuses(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        path = folder / "README.md"
        path.write_text("# Notes\n\ncoach@example.com\n")
        with self.assertRaises(RecordingFailure) as ctx:
            init_source(str(path))
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)

    def test_pii_page_refuses_init(self):
        with self.assertRaises(RecordingFailure) as ctx:
            init_source(str(ROOT / "fixtures" / "pii.html"))
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
