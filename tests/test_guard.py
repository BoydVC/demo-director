import json
import os
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
            "<style>body{margin:0;background:#102;color:#eee;font-family:system-ui}"
            "header{padding:16px 24px;background:#1e293b} .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:16px}"
            ".card{min-height:180px;border-radius:12px;padding:16px;color:#fff}"
            "</style><header><h1>Salon desk</h1></header>"
            "<p style=\"padding:0 24px\">Synthetic schedule for a Saturday session with orange cones ready today. "
            "Booked as desk.demo@example.com</p>"
            "<div class=grid><div class=card style=\"background:#7c2d12\">Cones</div>"
            "<div class=card style=\"background:#14532d\">Pinnies</div>"
            "<div class=card style=\"background:#1e3a8a\">Whistle</div></div>"
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
        shell = "\n".join((
            "7 DAYS",
            "Calendar unavailable",
            "The week server is unreachable (HTTP 404).",
            "Nothing is shown rather than guessed.",
            "Tonight",
            "No calendar data, so no conflicts can be checked.",
        ))
        self.assertEqual(BlankPolicy().reason(shell), "error-phrase:unavailable")
        desk = "The Sports Desk\nCouldn't load the desk: SyntaxError: Unexpected token is not valid JSON"
        self.assertIn("error-phrase", BlankPolicy().reason(desk) or "")
        self.assertEqual(BlankPolicy(min_chars=500).reason(page), "sparse-text")
        self.assertEqual(BlankPolicy(patterns=(r"orange cones",)).reason("orange cones"), "error-phrase:orange cones")
        self.assertIsNone(BlankPolicy(patterns=(r"orange cones",)).reason(page))
        data = example()
        data["url"] = (ROOT / "fixtures" / "mention-error.html").resolve().as_uri()
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        data["blank"] = {"min_chars": 10000, "patterns": []}
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(data), tempfile.mkdtemp(), scale=0.3)
        self.assertEqual(ctx.exception.detail, "sparse-text")


    def test_canvas_game_is_not_sparse(self):
        """A short title over a large drawn canvas is a demo, not sparse-text."""
        data = example()
        data["url"] = (ROOT / "fixtures" / "canvas-game.html").resolve().as_uri()
        data["title"] = "Canvas Drive"
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        out = tempfile.mkdtemp()
        rendered = direct(parse(data), out, scale=0.3)
        self.assertTrue(rendered)
        gifs = [row.get("gif") for row in rendered.values() if isinstance(row, dict)]
        self.assertTrue(any(gif and pathlib.Path(gif).is_file() for gif in gifs))


    def test_canvas_only_game_is_not_blank(self):
        """A full-viewport canvas with no DOM copy is still a demo."""
        data = example()
        data["url"] = (ROOT / "fixtures" / "canvas-only.html").resolve().as_uri()
        data["title"] = "Bridge Drill"
        data["steps"] = [{"action": "hold", "frames": 2}]
        data["aspects"] = ["16:9"]
        out = tempfile.mkdtemp()
        rendered = direct(parse(data), out, scale=0.3)
        self.assertTrue(rendered)
        gifs = [row.get("gif") for row in rendered.values() if isinstance(row, dict)]
        self.assertTrue(any(gif and pathlib.Path(gif).is_file() for gif in gifs))

    def test_phrase_dominates_or_a_full_page_renders(self):
        full_error = example()
        full_error["url"] = (ROOT / "fixtures" / "mention-error.html").resolve().as_uri()
        full_error["title"] = "Error handling guide"
        full_error["steps"] = [{"action": "hold", "frames": 2}]
        full_error["aspects"] = ["16:9"]
        full_empty = example()
        full_empty["url"] = (ROOT / "fixtures" / "mention-empty.html").resolve().as_uri()
        full_empty["title"] = "Saturday scoreboard"
        full_empty["steps"] = [{"action": "hold", "frames": 2}]
        full_empty["aspects"] = ["16:9"]
        only = example()
        only["url"] = (ROOT / "fixtures" / "only-failed.html").resolve().as_uri()
        only["title"] = "Failed"
        only["steps"] = [{"action": "hold", "frames": 2}]
        only["aspects"] = ["16:9"]
        folder = pathlib.Path(tempfile.mkdtemp())
        outcomes = {}
        for name, data in (("mention-error", full_error), ("mention-empty", full_empty), ("only-failed", only)):
            path = folder / f"{name}.json"
            path.write_text(json.dumps(data))
            report = batch([str(path)], tempfile.mkdtemp(), scale=0.25)
            outcomes[name] = report["items"][0]["status"]
        self.assertEqual(outcomes["mention-error"], "ok", outcomes)
        self.assertEqual(outcomes["mention-empty"], "ok", outcomes)
        self.assertEqual(outcomes["only-failed"], "blank-page", outcomes)

    def test_empty_canvas_and_flat_body_are_blank(self):
        for rel, detail in (
            ("fixtures/empty-canvas.html", "empty-canvas"),
            ("fixtures/empty-wall.html", "chrome-only"),
            ("fixtures/chrome-header.html", "chrome-only"),
            ("fixtures/chrome-gradient.html", "chrome-only"),
        ):
            data = example()
            data["url"] = (ROOT / rel).resolve().as_uri()
            data["title"] = "Wall"
            data["steps"] = [{"action": "hold", "frames": 2}]
            data["aspects"] = ["16:9"]
            with self.assertRaises(RecordingFailure) as ctx:
                direct(parse(data), tempfile.mkdtemp(), scale=0.3)
            self.assertEqual(ctx.exception.kind, "blank-page", rel)
            self.assertEqual(ctx.exception.detail, detail, rel)

    def test_small_or_painted_canvas_still_renders(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        small = folder / "small.html"
        small.write_text(
            "<style>body{margin:0;background:#102;color:#eee;font-family:system-ui}"
            ".grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:16px}"
            ".card{min-height:180px;color:#fff;padding:16px}</style>"
            "<h1>Desk</h1><p>Orange cones, green pinnies, and a blue whistle ready for Saturday.</p>"
            "<canvas width=160 height=80 style=\"width:160px;height:80px;background:#334\"></canvas>"
            "<div class=grid><div class=card style=\"background:#7c2d12\">Cones</div>"
            "<div class=card style=\"background:#14532d\">Pinnies</div>"
            "<div class=card style=\"background:#1e3a8a\">Whistle</div></div>"
        )
        painted = folder / "painted.html"
        painted.write_text(
            "<style>body{margin:0;background:#111;color:#eee;font-family:system-ui}"
            "canvas{display:block;width:100%;height:640px;background:#111}</style>"
            "<h1>Desk</h1><p>Orange cones, green pinnies, and a blue whistle ready for Saturday practice.</p>"
            "<button id=paint>Paint</button>"
            "<canvas id=sky width=960 height=640></canvas>"
            "<script>document.getElementById('paint').onclick=()=>{"
            "const c=document.getElementById('sky');const g=c.getContext('2d');"
            "g.fillStyle='#7c2d12';g.fillRect(0,0,480,640);"
            "g.fillStyle='#14532d';g.fillRect(480,0,480,640);}</script>"
        )
        for path, steps in (
            (small, [{"action": "hold", "frames": 2}]),
            (painted, [{"action": "click", "selector": "#paint", "frames": 2}, {"action": "hold", "frames": 2}]),
        ):
            data = example()
            data["url"] = path.resolve().as_uri()
            data["title"] = "Desk"
            data["steps"] = steps
            data["aspects"] = ["16:9"]
            rendered = direct(parse(data), tempfile.mkdtemp(), scale=0.3)
            self.assertIn("16:9", rendered, path.name)

    def test_deny_list_matches_normalized_text_once(self):
        from demo_director.guard import derive_names, load_deny_terms, locate_denied, term_present
        self.assertFalse(term_present("alexander took a victory lap", "Alex"))
        self.assertTrue(term_present("hello Alex today", "Alex"))
        self.assertTrue(term_present("Alex\u2019s chair", "Alex"))
        self.assertTrue(term_present("Q U I N N fridge board", "Quinn"))
        self.assertFalse(term_present('{"@type":"Thing"}', "@samplebot"))
        self.assertEqual(derive_names("Bot Launcher", title="Bot Launcher"), ())
        self.assertEqual(derive_names("Built for Alex Quinn.", title="Desk"), ("Alex Quinn", "Alex", "Quinn"))
        self.assertEqual(derive_names("Alex's Locker is open", title="Alex Locker"), ())
        self.assertNotIn("@type", derive_names('{"@type":"Thing"} for the Bot Launcher', title="Bot Launcher"))
        found = locate_denied(("Quinn", "quinn", "@samplebot"), page="Quinn (@samplebot) kept a note", title="Desk")
        self.assertEqual(found, ("Quinn:page", "@samplebot:page"))
        hidden = locate_denied(("Quinn",), page="Desk notes for Saturday", page_source="Desk notes\nQuinn")
        self.assertEqual(hidden, ("Quinn:page-source",))
        revealed = locate_denied(("Quinn",), page="Quinn is on the board", opened="Desk notes for Saturday")
        self.assertEqual(revealed, ("Quinn:page-click",))
        self.assertEqual(found.count("Quinn:page"), 1)
        folder = pathlib.Path(tempfile.mkdtemp())
        hidden = folder / "hidden.html"
        hidden.write_text(
            "<title>Desk for Quinn</title>"
            "<style>body{margin:0;background:#102;color:#eee;font-family:system-ui}"
            ".grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:16px}"
            ".card{min-height:160px;color:#fff;padding:16px}</style>"
            "<h1>Desk</h1><p>Orange cones and green pinnies are ready for Saturday.</p>"
            "<div class=grid><div class=card style=\"background:#7c2d12\">Cones</div>"
            "<div class=card style=\"background:#14532d\">Pinnies</div>"
            "<div class=card style=\"background:#1e3a8a\">Whistle</div></div>"
        )
        concealed = example()
        concealed["title"] = "Desk"
        concealed["url"] = hidden.resolve().as_uri()
        concealed["steps"] = [{"action": "hold", "frames": 2}]
        concealed["aspects"] = ["16:9"]
        concealed["deny_names"] = ["Quinn"]
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(concealed), tempfile.mkdtemp(), scale=0.25)
        self.assertIn("Quinn:page-source", ctx.exception.detail)
        clicked = folder / "clicked.html"
        clicked.write_text(
            "<style>body{margin:0;background:#102;color:#eee;font-family:system-ui}"
            ".grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:16px}"
            ".card{min-height:160px;color:#fff;padding:16px}</style>"
            "<h1>Desk</h1><p>Orange cones and green pinnies are ready for Saturday.</p>"
            "<button id=show>Show</button><p id=note></p>"
            "<div class=grid><div class=card style=\"background:#7c2d12\">Cones</div>"
            "<div class=card style=\"background:#14532d\">Pinnies</div>"
            "<div class=card style=\"background:#1e3a8a\">Whistle</div></div>"
            "<script>document.getElementById('show').onclick=()=>{"
            "document.getElementById('note').textContent='Quinn kept a note';}</script>"
        )
        opened = example()
        opened["title"] = "Desk"
        opened["url"] = clicked.resolve().as_uri()
        opened["steps"] = [{"action": "click", "selector": "#show", "frames": 2}]
        opened["aspects"] = ["16:9"]
        opened["deny_names"] = ["Quinn"]
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(opened), tempfile.mkdtemp(), scale=0.25)
        self.assertIn("Quinn:page-click", ctx.exception.detail)
        folder = pathlib.Path(tempfile.mkdtemp())
        deny = folder / "deny.txt"
        deny.write_text("# synthetic\nQuinn\n@samplebot\n")
        leaked = folder / "leaked.html"
        leaked.write_text(
            "<style>body{margin:0;background:#102;color:#eee;font-family:system-ui}"
            "header{background:#1e293b;padding:16px}.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:16px}"
            ".card{min-height:160px;color:#fff;padding:16px}</style>"
            "<header><h1>Desk</h1></header>"
            "<p>Everything Quinn (@samplebot) bookmarked for a Saturday session with orange cones ready today.</p>"
            "<div class=grid><div class=card style=\"background:#7c2d12\">Cones</div>"
            "<div class=card style=\"background:#14532d\">Pinnies</div>"
            "<div class=card style=\"background:#1e3a8a\">Whistle</div></div>"
        )
        seen = example()
        seen["title"] = "Desk"
        seen["url"] = leaked.resolve().as_uri()
        seen["steps"] = [{"action": "hold", "frames": 2}]
        seen["aspects"] = ["16:9"]
        seen_path = folder / "seen.json"
        seen_path.write_text(json.dumps(seen))
        old = os.environ.get("DEMO_DIRECTOR_DENY")
        os.environ["DEMO_DIRECTOR_DENY"] = str(deny)
        try:
            self.assertEqual(load_deny_terms(), ("Quinn", "@samplebot"))
            blocked = batch([str(seen_path)], tempfile.mkdtemp(), scale=0.25)
        finally:
            if old is None:
                os.environ.pop("DEMO_DIRECTOR_DENY", None)
            else:
                os.environ["DEMO_DIRECTOR_DENY"] = old
        item = blocked["items"][0]
        self.assertEqual(item["status"], "pii-blocked")
        self.assertEqual(item["error"].count("Quinn:page"), 1)
        self.assertIn("@samplebot:page", item["error"])
        # A multi-word title is not a deny term unless it is listed.
        plain = example()
        plain["title"] = "Bot Launcher"
        plain["steps"] = [{"action": "caption", "text": "Bot Launcher", "frames": 2}, {"action": "hold", "frames": 2}]
        plain["aspects"] = ["16:9"]
        plain_path = folder / "plain.json"
        plain_path.write_text(json.dumps(plain))
        opened = batch([str(plain_path)], tempfile.mkdtemp(), scale=0.25)
        self.assertEqual(opened["items"][0]["status"], "ok", opened)


if __name__ == "__main__":
    unittest.main()
