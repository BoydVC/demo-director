import pathlib
import unittest

from demo_director.ci import FORBIDDEN, WORKFLOW, posts_anywhere, workflow_text


class CiTests(unittest.TestCase):
    def test_action_renders_on_push_and_does_not_post(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        self.assertFalse((root / ".github" / "workflows").exists())
        self.assertEqual(WORKFLOW, root / "ci" / "render.yml")
        text = workflow_text()
        self.assertIn("push:", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("python -m demo_director batch examples/ -o shots", text)
        self.assertIn("python -m unittest discover -s tests", text)
        self.assertEqual(posts_anywhere(text), [])
        for token in FORBIDDEN:
            self.assertNotIn(token, text.lower())


if __name__ == "__main__":
    unittest.main()
