import json
import os
import pathlib
import tempfile
import unittest

from demo_director.batch import render_paths
from demo_director.director import direct, read_page_text
from demo_director.pii import guard, scan
from demo_director.shotlist import parse
from demo_director.types import PiiBlocked, PiiPolicy

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "fixtures/app.html").resolve().as_uri()


def _page(tmp: pathlib.Path, body: str) -> str:
    path = tmp / "page.html"
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Synthetic</title></head>"
        f"<body><p>{body}</p></body></html>"
    )
    return path.resolve().as_uri()


def _list(url: str, names: tuple[str, ...] = ()) -> dict:
    data = {
        "title": "Probe",
        "url": url,
        "aspects": ["16:9"],
        "steps": [{"action": "hold", "frames": 2}],
    }
    if names:
        data["pii"] = {"names": list(names)}
    return data


class PiiTests(unittest.TestCase):
    def test_denylist_and_false_friends(self):
        self.assertEqual(scan((ROOT / "fixtures/app.html").read_text(), PiiPolicy()), ())
        clean = "Sat 10:00 · Scrimmage, Field 1\n1 conflict: Sat 10:00 overlaps Practice"
        self.assertEqual(scan(clean, PiiPolicy()), ())
        self.assertEqual(scan("Team Alpha", PiiPolicy(names=("Al",))), ())
        self.assertEqual(scan("", PiiPolicy()), ())
        email = scan("Reach alex.demo@gmail.com for the roster", PiiPolicy())
        self.assertEqual([hit.kind for hit in email], ["email"])
        phone = scan("Call +1 (415) 555-0199 for the field.", PiiPolicy())
        self.assertEqual([hit.kind for hit in phone], ["phone"])
        named = scan("Coach Jordan Hale is on the roster", PiiPolicy(names=("Jordan Hale",)))
        self.assertEqual([hit.kind for hit in named], ["name"])

    def test_unreadable_text_fails_closed(self):
        with self.assertRaises(PiiBlocked) as raised:
            guard(None, PiiPolicy())
        self.assertEqual(raised.exception.hits[0].kind, "unreadable")

        class Boom:
            def evaluate(self, *_args, **_kwargs):
                raise RuntimeError("detached")

        self.assertIsNone(read_page_text(Boom()))

    def test_caption_email_blocks_before_render(self):
        data = _list(FIXTURE)
        data["steps"] = [{"action": "caption", "text": "mail alex.demo@gmail.com", "frames": 2}]
        out = tempfile.mkdtemp()
        with self.assertRaises(PiiBlocked):
            direct(parse(data), out)
        self.assertEqual(list(pathlib.Path(out).glob("*.gif")), [])

    def test_page_email_blocks_the_batch(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        url = _page(tmp, "The coach is alex.demo@gmail.com today.")
        shot = tmp / "pii.json"
        shot.write_text(json.dumps(_list(url)))
        out = tmp / "out"
        report = render_paths([str(shot)], str(out), scale=0.4)
        self.assertFalse(report.ok)
        self.assertIn("email", report.items[0].error)
        self.assertEqual(list(out.rglob("*.gif")), [])

    def test_page_phone_and_configured_name_block(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        phone = _page(tmp, "Call +1 (415) 555-0199 for the field.")
        out = tempfile.mkdtemp()
        with self.assertRaises(PiiBlocked) as raised:
            direct(parse(_list(phone)), out)
        self.assertTrue(any(hit.kind == "phone" for hit in raised.exception.hits))
        self.assertEqual(list(pathlib.Path(out).glob("*.gif")), [])

        named = tmp / "named.html"
        named.write_text(
            "<!doctype html><html><body><p>Coach Jordan Hale sent the lineup.</p></body></html>"
        )
        out = tempfile.mkdtemp()
        with self.assertRaises(PiiBlocked) as raised:
            direct(parse(_list(named.resolve().as_uri(), ("Jordan Hale",))), out)
        self.assertTrue(any(hit.kind == "name" for hit in raised.exception.hits))
        self.assertFalse(any(name.endswith(".gif") for name in os.listdir(out)))


if __name__ == "__main__":
    unittest.main()
