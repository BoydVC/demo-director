import json
import pathlib
import tempfile
import unittest

from demo_director import guard
from demo_director.batch import batch
from demo_director.director import direct
from demo_director.session import RecordingFailure
from demo_director.shotlist import parse

ROOT = pathlib.Path(__file__).resolve().parents[1]


def example():
    data = json.loads((ROOT / "examples/weekend-planner.json").read_text())
    data["url"] = (ROOT / "fixtures/app.html").as_uri()
    return data


class GuardTests(unittest.TestCase):
    def test_fixture_and_synthetic_copy_are_clean(self):
        for path in ("fixtures/app.html", "fixtures/kit-check.html"):
            self.assertEqual(guard.hits((ROOT / path).read_text()), (), path)
        for line in (
            "Sat 10:30 · Game vs Comets",
            "Sat 10:00 · Scrimmage, Field 1",
            "1 conflict: Sat 10:00 overlaps Practice → suggest carpool swap",
            "4 items marked ready for Saturday",
            "Team Alpha",
            "Mesh ball bag",
        ):
            self.assertEqual(guard.hits(line), (), line)

    def test_email_phone_and_configured_name(self):
        self.assertIn("email", guard.hits("Reach the coach at coach.demo@gmail.com."))
        self.assertEqual(guard.hits("Booked as desk.demo@example.com"), ())
        self.assertIn("phone", guard.hits("Call (415) 555-0134 today"))
        self.assertIn("phone", guard.hits("555-010-0199"))
        self.assertIn("phone", guard.hits("+1 202 555 0148"))
        self.assertIn("name", guard.hits("signed up as Casey Example", ("Casey Example",)))
        self.assertEqual(guard.hits("Team Alpha", ()), ())
        self.assertIn("name", guard.hits("hello Team Alpha", ("team alpha",)))

    def test_fail_closed_on_unreadable_or_weak_name(self):
        self.assertEqual(guard.hits(None), ("unreadable",))
        self.assertEqual(guard.hits(12), ("unreadable",))
        self.assertIn("name-policy", guard.hits("hello", ("Al",)))
        with self.assertRaises(RecordingFailure) as ctx:
            guard.assert_clean(None, ())
        self.assertEqual(ctx.exception.kind, "pii-blocked")

    def test_real_email_blocks_the_render(self):
        data = example()
        data["url"] = (ROOT / "fixtures/pii.html").as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        out = tempfile.mkdtemp()
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(data), out, scale=0.3)
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)
        self.assertNotIn("coach.demo@gmail.com", ctx.exception.detail)
        self.assertEqual(list(pathlib.Path(out).glob("demo-*.gif")), [])

    def test_caption_email_blocks_before_any_gif(self):
        data = example()
        data["steps"] = [{"action": "caption", "text": "mail alex.demo@gmail.com", "frames": 2}]
        out = tempfile.mkdtemp()
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(data), out, scale=0.3)
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("email", ctx.exception.detail)
        self.assertEqual(list(pathlib.Path(out).glob("*.gif")), [])

    def test_configured_name_blocks_the_render(self):
        data = example()
        data["deny_names"] = ["Team Alpha"]
        data["steps"] = [{"action": "hold", "frames": 2}]
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(data), tempfile.mkdtemp(), scale=0.3)
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("name", ctx.exception.detail)

    def test_batch_pii_writes_no_gif_and_hides_the_address(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        data = example()
        data["url"] = (ROOT / "fixtures/pii.html").as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        path = folder / "blocked.json"
        path.write_text(json.dumps(data))
        out = pathlib.Path(tempfile.mkdtemp())
        report = batch([str(path)], str(out), scale=0.3)
        self.assertFalse(report["ok"])
        self.assertEqual(report["items"][0]["status"], "pii-blocked")
        self.assertIn("email", report["items"][0]["error"])
        self.assertNotIn("@", report["items"][0]["error"])
        blob = (out / "report.json").read_text()
        self.assertNotIn("coach.demo@gmail.com", blob)
        self.assertEqual(list(out.glob("demo-*.gif")), [])

    def test_missing_selector_is_typed(self):
        from demo_director.director import record
        data = example()
        data["steps"] = [{"action": "click", "selector": "#no-such-control", "frames": 2}]
        with self.assertRaises(RecordingFailure) as ctx:
            record(parse(data))
        self.assertEqual(ctx.exception.kind, "selector-missing")
        self.assertEqual(ctx.exception.step_index, 0)
        self.assertEqual(ctx.exception.selector, "#no-such-control")

    def test_reserved_domains_are_synthetic(self):
        allowed = (
            "desk.demo@example.com",
            "Ada@Example.ORG",
            "crew@example.net",
            "a@foo.example.com",
            "a@foo.test",
            "a@a.b.test",
            "a@shop.example",
            "a@foo.bar.example",
        )
        for address in allowed:
            self.assertEqual(guard.hits(f"reach {address} today"), (), address)
        blocked = (
            "ada@gmail.com",
            "ada@notexample.com",
            "ada@example.co",
            "ada@example.com.evil.org",
            "coach.demo@gmail.com",
        )
        for address in blocked:
            self.assertIn("email", guard.hits(address), address)
        self.assertIn("email", guard.hits("desk.demo@example.com and also ada@gmail.com"))

    def test_reserved_email_on_the_page_can_render(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        page = folder / "salon.html"
        page.write_text(
            "<style>body{font-family:system-ui;background:#102;color:#eee;padding:24px}</style>"
            "<h1>Salon desk</h1>"
            "<p>Synthetic schedule for a Saturday session with orange cones ready today.</p>"
            "<p>Booked as desk.demo@example.com</p>"
        )
        data = example()
        data["url"] = page.resolve().as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        out = tempfile.mkdtemp()
        direct(parse(data), out, scale=0.3)
        self.assertTrue(list(pathlib.Path(out).glob("demo-*.gif")))

    def test_action_errors_keep_their_kind(self):
        from demo_director.director import classify_failure, record

        class TimeoutError(Exception):
            pass

        class Boom(Exception):
            pass

        covered = "Timeout 5000ms exceeded.\n<div id=c> intercepts pointer events"
        self.assertEqual(classify_failure(TimeoutError(covered)), "not-visible")
        self.assertEqual(
            classify_failure(TimeoutError("Timeout 5000ms exceeded. waiting for element to be visible")),
            "timeout",
        )
        self.assertEqual(classify_failure(Boom("Page.fill: Error: Malformed value")), "invalid-value")
        self.assertEqual(
            classify_failure(Boom("Page.goto: net::ERR_CONNECTION_REFUSED"), navigating=True),
            "nav-error",
        )
        self.assertEqual(classify_failure(TimeoutError("Timeout 15000ms exceeded"), navigating=True), "timeout")

        folder = pathlib.Path(tempfile.mkdtemp())
        filler = "<p>Synthetic demo board for a Saturday session with orange cones ready today.</p>"
        hidden = folder / "hidden.html"
        hidden.write_text(
            "<style>body{background:#111;color:#fff;font-family:sans-serif}</style>"
            + filler + "<p>Visible copy</p><button id=go hidden>Go</button>"
        )
        bad = folder / "date.html"
        bad.write_text(
            "<style>body{background:#111;color:#fff;font-family:sans-serif}</style>"
            + filler + "<p>When</p><input id=d type=date>"
        )
        covered_page = folder / "covered.html"
        covered_page.write_text(
            "<style>body{background:#111;color:#fff;font-family:sans-serif}"
            "#c{position:fixed;inset:0}</style>" + filler +
            "<p>Visible copy</p><button id=go>Go</button><div id=c></div>"
        )
        cases = (
            (hidden, [{"action": "click", "selector": "#go", "frames": 2}], "not-visible", "#go"),
            (bad, [{"action": "type", "selector": "#d", "text": "Sample", "frames": 2}], "invalid-value", "#d"),
            (covered_page, [{"action": "click", "selector": "#go", "frames": 2}], "not-visible", "#go"),
        )
        for page, steps, kind, selector in cases:
            data = example()
            data["url"] = page.resolve().as_uri()
            data["steps"] = steps
            with self.assertRaises(RecordingFailure) as ctx:
                record(parse(data))
            self.assertEqual(ctx.exception.kind, kind, page.name)
            self.assertNotEqual(ctx.exception.kind, "timeout", page.name)
            self.assertEqual(ctx.exception.selector, selector)
            self.assertEqual(ctx.exception.step_index, 0)

        data = example()
        data["steps"] = [{"action": "goto", "url": "http://127.0.0.1:1/", "frames": 2}]
        with self.assertRaises(RecordingFailure) as ctx:
            record(parse(data))
        self.assertEqual(ctx.exception.kind, "nav-error")
        self.assertEqual(ctx.exception.step_index, 0)
        self.assertIn("navigation", ctx.exception.detail)

    def test_blank_unstyled_and_http_error_fail(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        folder = pathlib.Path(tempfile.mkdtemp())
        blank = folder / "blank.html"
        blank.write_text("<style>body{background:#111;color:#eee}</style><body></body>")
        plain = folder / "plain.html"
        plain.write_text("<p>Hello plain page</p>")
        for page, detail in ((blank, "no-visible-text"), (plain, "unstyled")):
            data = example()
            data["url"] = page.resolve().as_uri()
            data["steps"] = [{"action": "hold", "frames": 2}]
            out = tempfile.mkdtemp()
            with self.assertRaises(RecordingFailure) as ctx:
                direct(parse(data), out, scale=0.3)
            self.assertEqual(ctx.exception.kind, "blank-page", page.name)
            self.assertEqual(ctx.exception.detail, detail)
            self.assertTrue(ctx.exception.warning.startswith("page is not demoable:"))
            self.assertEqual(list(pathlib.Path(out).glob("demo-*.gif")), [])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<style>body{color:red}</style><p>missing</p>"
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = example()
            data["url"] = f"http://127.0.0.1:{server.server_address[1]}/missing"
            data["steps"] = [{"action": "hold", "frames": 2}]
            with self.assertRaises(RecordingFailure) as ctx:
                direct(parse(data), tempfile.mkdtemp(), scale=0.3)
            self.assertEqual(ctx.exception.kind, "blank-page")
            self.assertIn("http-404", ctx.exception.detail)
            self.assertIn("http-404", ctx.exception.warning)
        finally:
            server.shutdown()
            server.server_close()

    def test_batch_report_names_the_failing_step(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        data = example()
        data["steps"] = [{"action": "click", "selector": "#no-such-control", "frames": 2}]
        data["aspects"] = ["16:9"]
        path = folder / "miss.json"
        path.write_text(json.dumps(data))
        report = batch([str(path)], tempfile.mkdtemp(), scale=0.3)
        item = report["items"][0]
        self.assertEqual(item["status"], "selector-missing")
        self.assertEqual(item["kind"], "selector-missing")
        self.assertEqual(item["step"], 0)
        self.assertEqual(item["selector"], "#no-such-control")
        self.assertIn("step 0", item["error"])
        self.assertIn("#no-such-control", item["error"])
        self.assertIn("selector-missing", item["error"])

    def test_styled_empty_and_error_pages_fail_closed(self):
        import subprocess
        import sys
        cases = (
            ("fixtures/blank-sparse.html", "sparse-text"),
            ("fixtures/blank-failed.html", "error-phrase:failed to load"),
            ("fixtures/blank-unavailable.html", "error-phrase:unavailable"),
            ("fixtures/blank-on-air.html", "error-phrase:no episodes on air"),
            ("fixtures/blank-error.html", "error-phrase:error"),
        )
        for rel, detail in cases:
            data = example()
            data["url"] = (ROOT / rel).resolve().as_uri()
            data["steps"] = [{"action": "hold", "frames": 2}]
            data["aspects"] = ["16:9"]
            out = tempfile.mkdtemp()
            with self.assertRaises(RecordingFailure) as ctx:
                direct(parse(data), out, scale=0.3)
            self.assertEqual(ctx.exception.kind, "blank-page", rel)
            self.assertEqual(ctx.exception.detail, detail, rel)
            self.assertIn(detail, ctx.exception.warning)
            self.assertEqual(list(pathlib.Path(out).glob("demo-*.gif")), [])
        proc = subprocess.run(
            [sys.executable, "-m", "demo_director", "init", str(ROOT / "fixtures" / "blank-failed.html")],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "blank-page")
        self.assertEqual(payload["detail"], "error-phrase:failed to load")
        self.assertIn("warning", payload)
        self.assertNotIn("RuntimeWarning", proc.stdout)

    def test_blank_policy_is_configurable(self):
        from demo_director.types import BlankPolicy
        page = "Synthetic schedule for a Saturday session with orange cones ready today."
        self.assertIsNone(BlankPolicy().reason(page))
        self.assertEqual(BlankPolicy(min_chars=500).reason(page), "sparse-text")
        self.assertEqual(
            BlankPolicy(patterns=(r"orange cones",)).reason(page),
            "error-phrase:orange cones",
        )
        data = example()
        data["url"] = (ROOT / "fixtures" / "blank-sparse.html").resolve().as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        data["blank"] = {"min_chars": 1, "patterns": []}
        out = tempfile.mkdtemp()
        direct(parse(data), out, scale=0.3)
        self.assertTrue(list(pathlib.Path(out).glob("demo-*.gif")))

    def test_derived_names_block_unless_allowlisted(self):
        from demo_director.guard import derived_names, names_to_block
        self.assertEqual(derived_names("Maryland Men's Basketball"), ())
        self.assertEqual(derived_names("for a Saturday session"), ())
        self.assertEqual(derived_names("Alex's Chair"), ("Alex",))
        self.assertEqual(derived_names("Built for Alex Example."), ("Alex Example", "Alex"))
        self.assertEqual(names_to_block("Alex's Chair", ("Alex",)), ())
        folder = pathlib.Path(tempfile.mkdtemp())
        data = example()
        data["title"] = "Alex's Chair"
        data["url"] = (ROOT / "fixtures" / "named-chair.html").resolve().as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        path = folder / "chair.json"
        path.write_text(json.dumps(data))
        (folder / "README.md").write_text("# Desk\n\nSynthetic board for Alex Example.\n")
        out = pathlib.Path(tempfile.mkdtemp())
        blocked = batch([str(path)], str(out), scale=0.3)
        item = blocked["items"][0]
        self.assertEqual(item["status"], "pii-blocked")
        self.assertTrue(item["error"].endswith("name"))
        self.assertIn("Alex", item["warning"])
        self.assertNotIn("Miranda", item["warning"])
        self.assertEqual(list(out.glob("demo-*.gif")), [])
        allowed = batch([str(path)], tempfile.mkdtemp(), scale=0.3, allow_names=("Alex", "Alex Example"))
        self.assertEqual(allowed["items"][0]["status"], "ok", allowed)


if __name__ == "__main__":
    unittest.main()
