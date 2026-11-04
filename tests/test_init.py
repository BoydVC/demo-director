import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from demo_director.propose import (
    Control, PageControls, display_title, draft_from, dump_draft, human_title, init_source, synthetic_value,
)
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
        path.write_text("# Notes\n\ncoach.demo@gmail.com\n")
        with self.assertRaises(RecordingFailure) as ctx:
            init_source(str(path))
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)

    def test_typed_inputs_get_browser_values(self):
        self.assertEqual(synthetic_value("When", "date"), "2026-11-07")
        self.assertEqual(synthetic_value("At", "time"), "10:00")
        self.assertEqual(synthetic_value("Count", "number"), "3")
        self.assertEqual(synthetic_value("", "color"), "#3366ff")
        self.assertEqual(synthetic_value("ignored", "url"), "https://example.test")
        self.assertEqual(synthetic_value("person@example.com", "text"), "Sample")
        page = PageControls("Times", (
            Control("#d", "input", "When", "date"),
            Control("#n", "input", "Count", "number"),
        ))
        draft = draft_from("Times", "file:///x.html", (), page)
        typed = [step for step in draft.shotlist.steps if isinstance(step, Type)]
        self.assertEqual([step.text for step in typed], ["2026-11-07", "3"])

    def test_init_fills_date_and_number_then_replays(self):
        from dataclasses import replace
        from demo_director.director import record
        draft = init_source(str(ROOT / "fixtures" / "typed-fields.html"))
        typed = {step.selector: step.text for step in draft.shotlist.steps if isinstance(step, Type)}
        self.assertEqual(typed["#d"], "2026-11-07")
        self.assertEqual(typed["#n"], "3")
        self.assertNotIn("Sample", typed.values())
        url = (ROOT / "fixtures" / "typed-fields.html").resolve().as_uri()
        record(replace(draft.shotlist, url=url))

    def test_init_skips_hidden_controls_and_prefers_stable_selectors(self):
        from dataclasses import replace
        from demo_director.director import record
        draft = init_source(str(ROOT / "fixtures" / "tabs.html"))
        selectors = [step.selector for step in draft.shotlist.steps if isinstance(step, Click)]
        self.assertIn("#close", selectors)
        self.assertNotIn("#later", selectors)
        self.assertTrue(any(selector.startswith("role=button") and "Box score" in selector for selector in selectors))
        self.assertFalse(any("nth-of-type" in selector for selector in selectors))
        url = (ROOT / "fixtures" / "tabs.html").resolve().as_uri()
        record(replace(draft.shotlist, url=url))

    def test_slug_becomes_a_spaced_title(self):
        self.assertEqual(human_title("four-game-jam"), "Four Game Jam")
        self.assertEqual(display_title("FOUR-GAMEJAM", "four-game-jam"), "Four Game Jam")
        self.assertEqual(display_title("FOUR-GAMEJAM", ""), "Four Game Jam")
        self.assertEqual(display_title("Four Gamejam", ""), "Four Game Jam")
        self.assertEqual(display_title("FOUR-GAME-JAM", ""), "Four Game Jam")
        self.assertEqual(display_title("Weekend Planner", "demo-director"), "Weekend Planner")
        folder = pathlib.Path(tempfile.mkdtemp()) / "four-game-jam"
        folder.mkdir()
        (folder / "index.html").write_text(
            "<style>body{margin:0;padding:28px;background:#111;color:#eee;font-family:system-ui}</style>"
            "<h1>FOUR-GAMEJAM</h1>"
            "<p>Synthetic jam for a Saturday session with orange cones ready today.</p>"
            "<button id=play>Play</button>"
        )
        draft = init_source(str(folder))
        self.assertEqual(draft.shotlist.title, "Four Game Jam")
        self.assertEqual(draft.shotlist.steps[0].text, "Four Game Jam")

    def test_reserved_email_in_a_readme_is_allowed(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        path = folder / "README.md"
        path.write_text("# Notes\n\ndesk.demo@example.com\n")
        draft = init_source(str(path))
        self.assertEqual(draft.shotlist.steps[0].text, "Notes")

    def test_pii_page_refuses_init(self):
        with self.assertRaises(RecordingFailure) as ctx:
            init_source(str(ROOT / "fixtures" / "pii.html"))
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
