"""The household denylist blocks renders and never becomes the showcase pick."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from demo_director.batch import batch
from demo_director.director import direct
from demo_director.gallery import write_gallery
from demo_director.guard import (
    FAMILY_TERMS,
    detail_has_name,
    effective_names,
    locate_denied,
    reads_private_sources,
    term_present,
)
from demo_director.propose import init_source
from demo_director.scrub import (
    blocked_repos,
    open_prs,
    prepare_branch,
    propose_tree,
    refusal_reason,
    replace_surface,
    run_set,
)
from demo_director.session import RecordingFailure
from demo_director.shotlist import parse
from demo_director.showcase import (
    choose_still_index,
    discard_showcase,
    pick_best,
    publish_showcase,
)
from scripts.fleet_check import _bucket, block_replay

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _shot(caption="Hold", title="Desk"):
    data = json.loads((ROOT / "examples/weekend-planner.json").read_text())
    data["url"] = (ROOT / "fixtures/app.html").resolve().as_uri()
    data["title"] = title
    data["steps"] = [{"action": "caption", "text": caption, "frames": 2}, {"action": "hold", "frames": 2}]
    data["aspects"] = ["16:9"]
    return data


class NameGateTests(unittest.TestCase):
    def test_builtin_terms_are_whole_words_and_not_allowable(self):
        self.assertFalse(reads_private_sources())
        os.environ["DEMO_DIRECTOR_READS_PRIVATE_SOURCES"] = "1"
        try:
            self.assertFalse(reads_private_sources())
        finally:
            os.environ.pop("DEMO_DIRECTOR_READS_PRIVATE_SOURCES", None)
        self.assertIn("Charlotte", FAMILY_TERMS)
        self.assertIn("St. Mary", FAMILY_TERMS)
        names = effective_names(allow=("Charlotte", "Vic", "Team Alpha"))
        folded = {term.casefold() for term in names}
        self.assertIn("charlotte", folded)
        self.assertIn("vic", folded)
        self.assertNotIn("team alpha", folded)
        self.assertFalse(term_present("characterization of a victory lap", "Char"))
        self.assertFalse(term_present("characterization of a victory lap", "Vic"))
        self.assertFalse(term_present("BoydVC ships a demo", "Boyd"))
        self.assertTrue(term_present("B O Y D fridge", "Boyd"))
        self.assertTrue(locate_denied(names, page="Meet Charlotte at St. Mary."))
        self.assertTrue(locate_denied(names, page="Practice at Saint Mary and St Mary."))
        self.assertFalse(locate_denied(names, page="Team Alpha plays Saturday."))
        self.assertTrue(detail_has_name("name:Charlotte:page"))
        self.assertFalse(detail_has_name("name-policy"))
        self.assertFalse(detail_has_name("email"))

    def test_caption_blocks_and_deletes_a_showcase_gif(self):
        out = pathlib.Path(tempfile.mkdtemp())
        decoy = out / "demo-16x9.gif"
        decoy.write_bytes(b"GIF89a")
        (out / "still-16x9.png").write_bytes(b"png")
        (out / "notes.txt").write_text("keep")
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(_shot("Meet Charlotte")), str(out), scale=0.25)
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("name:", ctx.exception.detail)
        self.assertIn("Charlotte", ctx.exception.detail)
        self.assertFalse(decoy.exists())
        self.assertFalse((out / "still-16x9.png").exists())
        self.assertTrue((out / "notes.txt").exists())
        self.assertEqual(_bucket("pii-blocked", ctx.exception.detail), "name-blocked")

    def test_allow_name_cannot_render_a_builtin_term(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        path = folder / "named.json"
        path.write_text(json.dumps(_shot("Hello Vic")))
        out = pathlib.Path(tempfile.mkdtemp())
        report = batch([str(path)], str(out), scale=0.25, allow_names=("Vic", "Charlotte"))
        item = report["items"][0]
        self.assertEqual(item["status"], "pii-blocked")
        self.assertIn("name:", item["error"])
        self.assertEqual(list(out.glob("demo-*.gif")), [])

    def test_visible_page_text_blocks_without_a_deny_file(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        page = folder / "board.html"
        page.write_text(
            "<style>body{margin:0;background:#102;color:#eee;font-family:system-ui}"
            ".card{min-height:160px;background:#14532d;color:#fff;padding:16px}</style>"
            "<h1>Desk</h1><p>Orange cones are ready. Miranda kept the Saturday note.</p>"
            "<div class=card>Cones</div>"
        )
        data = _shot("Hold")
        data["url"] = page.resolve().as_uri()
        out = pathlib.Path(tempfile.mkdtemp())
        (out / "demo-16x9.gif").write_bytes(b"GIF89a")
        with self.assertRaises(RecordingFailure) as ctx:
            direct(parse(data), str(out), scale=0.25)
        self.assertIn("Miranda", ctx.exception.detail)
        self.assertIn("page", ctx.exception.detail)
        self.assertEqual(list(out.glob("demo-*.gif")), [])

    def test_init_refuses_a_readme_even_when_allowed(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        path = folder / "README.md"
        path.write_text("# Spalding night\n\n## Schedule\n")
        with self.assertRaises(RecordingFailure) as ctx:
            init_source(str(path), allow_names=("Spalding",))
        self.assertEqual(ctx.exception.kind, "pii-blocked")
        self.assertIn("name:", ctx.exception.detail)

    def test_still_pick_skips_a_dirty_frame(self):
        self.assertEqual(choose_still_index(100, 12, 12), 87)
        self.assertEqual(choose_still_index(20, 12, 12), 12)
        frames = ["clean board", "Charlotte waves", "clean board"]
        self.assertEqual(choose_still_index(3, 0, 0, frames, effective_names()), 2)
        self.assertIsNone(choose_still_index(2, 0, 0, ["Vic", "Boyd"], effective_names()))

    def test_best_pick_never_features_a_name_blocked_row(self):
        rows = [
            {"repo": "leaky", "bucket": "name-blocked", "status": "pii-blocked",
             "detail": "name:Charlotte:page", "score": 100, "title": "Charlotte"},
            {"repo": "plain", "bucket": "ok", "status": "ok", "score": 1, "title": "Desk"},
        ]
        self.assertEqual(pick_best(rows)["repo"], "plain")
        src = pathlib.Path(tempfile.mkdtemp())
        leaky = src / "leaky.png"
        plain = src / "plain.png"
        leaky.write_bytes(b"leak")
        plain.write_bytes(b"ok")
        dest = pathlib.Path(tempfile.mkdtemp())
        (dest / "best-still.png").write_bytes(b"old-leak")
        winner = publish_showcase(rows, dest, {"leaky": leaky, "plain": plain})
        self.assertEqual(winner["repo"], "plain")
        self.assertEqual((dest / "best-still.png").read_bytes(), b"ok")
        only = publish_showcase(rows[:1], dest, {"leaky": leaky})
        self.assertIsNone(only)
        self.assertFalse((dest / "best-still.png").exists())

    def test_gallery_omits_a_blocked_gif_and_a_blocked_title(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        gif = folder / "demo-16x9.gif"
        gif.write_bytes(b"GIF89a")
        report = {"items": [{"status": "pii-blocked", "error": "pii-blocked at step -1 name:Vic:page", "title": "Vic", "aspects": {}}]}
        text = pathlib.Path(write_gallery(str(folder), "Desk", report)).read_text()
        self.assertNotIn("demo-16x9.gif", text)
        self.assertNotIn("Vic", text)
        shown = pathlib.Path(tempfile.mkdtemp())
        (shown / "demo-16x9.gif").write_bytes(b"GIF89a")
        page = pathlib.Path(write_gallery(str(shown), "Charlotte")).read_text()
        self.assertNotIn("demo-16x9.gif", page)
        self.assertNotIn("Charlotte", page)
        self.assertIn("Demo", page)

    def test_replay_block_deletes_showcase_files(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        (folder / "demo-16x9.gif").write_bytes(b"GIF89a")
        (folder / "demo-16x9.mp4").write_bytes(b"mp4")
        (folder / "index.html").write_text("<img src=demo-16x9.gif>")
        (folder / "report.json").write_text("{}")
        blocked = block_replay(["Charlotte"], folder)
        self.assertEqual(blocked["bucket"], "name-blocked")
        self.assertEqual(blocked["status"], "pii-blocked")
        self.assertIn("name:", blocked["detail"])
        self.assertFalse((folder / "demo-16x9.gif").exists())
        self.assertFalse((folder / "index.html").exists())
        self.assertTrue((folder / "report.json").exists())
        self.assertEqual(_bucket("pii-blocked", "pii-blocked at step -1 name:Charlotte:replay"), "name-blocked")
        self.assertEqual(_bucket("pii-blocked", "email"), "other")


class ScrubTests(unittest.TestCase):
    def test_replacements_are_synthetic_and_whole_word(self):
        updated, hits = replace_surface("Charlotte's Victor at St. Mary. BoydVC victory character.")
        self.assertIn("Avery's", updated)
        self.assertIn("Jordan", updated)
        self.assertIn("Northfield", updated)
        self.assertIn("BoydVC", updated)
        self.assertIn("victory", updated)
        self.assertIn("character", updated)
        self.assertNotIn("Charlotte", updated)
        self.assertNotIn("Victor", updated)
        self.assertTrue(any(hit["synthetic"] == "Avery" for hit in hits))
        shouted, _ = replace_surface("ST. MARY")
        self.assertEqual(shouted, "NORTHFIELD")

    def test_propose_skips_private_sources_and_code_identifiers(self):
        root = pathlib.Path(tempfile.mkdtemp()) / "sample-app"
        private = root / "hspt" / "bank.json"
        private.parent.mkdir(parents=True)
        private.write_text('{"secret":"do-not-read","name":"Charlotte"}')
        tutor = root / "notes" / "tutor-sheets.csv"
        tutor.parent.mkdir(parents=True)
        tutor.write_text("secret-sheet,Charlotte\n")
        data = root / "data" / "users.json"
        data.parent.mkdir(parents=True)
        data.write_text('{"name":"Miranda"}\n')
        fixture = root / "fixtures" / "app.html"
        fixture.parent.mkdir(parents=True)
        fixture.write_text(
            "<p>Charlotte plays Saturday.</p>"
            "<script>const boyd = \"Boyd\";</script>"
        )
        script = root / "src" / "ui.js"
        script.parent.mkdir(parents=True)
        script.write_text('const victor = "Victor";\nexport function victorScore(){return 1}\n')
        law = pathlib.Path(tempfile.mkdtemp()) / "case-law"
        law.mkdir()
        (law / "index.html").write_text("<p>Charlotte</p>")
        self.assertEqual(refusal_reason("case-law"), "law")
        self.assertEqual(refusal_reason("kpd-app"), "kpd")
        self.assertIsNone(refusal_reason("flawless-ui"))
        self.assertEqual(propose_tree(law), [])
        changes = propose_tree(root)
        blob = json.dumps(changes)
        self.assertNotIn("do-not-read", blob)
        self.assertNotIn("secret-sheet", blob)
        paths = [change["path"] for change in changes]
        self.assertIn("fixtures/app.html", paths)
        self.assertIn("src/ui.js", paths)
        self.assertFalse(any("hspt" in path or "tutor" in path or path.startswith("data/") for path in paths))
        html = next(change["updated"] for change in changes if change["path"] == "fixtures/app.html")
        self.assertIn("Avery", html)
        self.assertIn('const boyd = "Rowan"', html)
        js = next(change["updated"] for change in changes if change["path"] == "src/ui.js")
        self.assertIn('const victor = "Jordan"', js)
        self.assertIn("function victorScore", js)
        self.assertEqual(private.read_text(), '{"secret":"do-not-read","name":"Charlotte"}')
        self.assertEqual(tutor.read_text(), "secret-sheet,Charlotte\n")

    def test_dry_run_prepare_and_list_commands(self):
        work = pathlib.Path(tempfile.mkdtemp())
        repo = work / "src" / "sample-app"
        (repo / "fixtures").mkdir(parents=True)
        page = repo / "fixtures" / "app.html"
        original = "<p>Miranda at Spalding.</p>\n"
        page.write_text(original)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "scrub@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Scrub"], cwd=repo, check=True)
        subprocess.run(["git", "add", "fixtures/app.html"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        summary = work / "summary.json"
        summary.write_text(json.dumps({
            "repos": [
                {"repo": "alpha", "bucket": "ok"},
                {"repo": "beta", "bucket": "name-blocked"},
                {"repo": "gamma", "bucket": "blank"},
                {"repo": "delta", "status": "pii-blocked", "detail": "name:Vic:page"},
            ]
        }))
        self.assertEqual(blocked_repos(json.loads(summary.read_text())), ["beta", "delta"])
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/fleet_scrub.py"), "list-blocked", "--summary", str(summary)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(proc.stdout.splitlines(), ["beta", "delta"])
        set_file = work / "repos.txt"
        set_file.write_text("sample-app demo\nkpd-app law\ncase-law demo\nmissing-app demo\n")
        proposed = run_set(set_file, work, "propose")
        self.assertFalse(proposed["reads_private_sources"])
        by_repo = {row["repo"]: row for row in proposed["repos"]}
        self.assertEqual(by_repo["kpd-app"]["skipped"], "kpd")
        self.assertEqual(by_repo["case-law"]["skipped"], "law")
        self.assertEqual(by_repo["missing-app"]["skipped"], "missing")
        self.assertEqual(by_repo["sample-app"]["changes"][0]["path"], "fixtures/app.html")
        self.assertEqual(page.read_text(), original)
        dry = prepare_branch(repo, dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertFalse(dry["committed"])
        self.assertEqual(page.read_text(), original)
        planned = open_prs(repo, yes=False)
        self.assertFalse(planned["opened"])
        self.assertEqual(page.read_text(), original)
        self.assertTrue(any(cmd[0] == "gh" for cmd in planned["commands"]))
        prepared = prepare_branch(repo, dry_run=False)
        self.assertTrue(prepared["committed"])
        self.assertNotIn("Miranda", page.read_text())
        self.assertIn("Quinn", page.read_text())
        self.assertIn("Riverside", page.read_text())
        head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
        self.assertEqual(head.stdout.strip(), "scrub/family-names")
        page.write_text("<p>Vic helps Char.</p>\n")
        calls = []

        def runner(cmd, cwd):
            calls.append(cmd)

        opened = open_prs(repo, yes=True, runner=runner)
        self.assertTrue(opened["opened"])
        self.assertEqual(calls[0][0], "git")
        self.assertEqual(calls[1][:3], ["gh", "pr", "create"])
        self.assertIn("--draft", calls[1])


if __name__ == "__main__":
    unittest.main()
