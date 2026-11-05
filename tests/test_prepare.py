"""Build/serve detection for fleet checkouts."""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

from demo_director.prepare import find_entry, run_build_hooks, start_server, wait_port


class PrepareTests(unittest.TestCase):
    def test_find_entry_prefers_built_dist_over_template(self):
        root = Path(tempfile.mkdtemp())
        (root / "web").mkdir()
        (root / "web" / "index.html").write_text(
            "<!doctype html><title>t</title><style>/*__CSS__*/</style><body>x</body>"
        )
        (root / "dist").mkdir()
        (root / "dist" / "snapshot.html").write_text(
            "<!doctype html><title>Snap</title><style>body{color:red}</style>"
            + ("<p>hello demo board content here</p>" * 20)
        )
        self.assertEqual(find_entry(root), "dist/snapshot.html")

    def test_run_build_hooks_makes_dist_and_runs_export_static(self):
        root = Path(tempfile.mkdtemp())
        (root / "web").mkdir()
        (root / "web" / "index.html").write_text("<!doctype html><title>App</title><body>hi</body>")
        (root / "export_static.py").write_text(
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parent\n"
            "(root / 'dist').mkdir(exist_ok=True)\n"
            "(root / 'dist' / 'snap.html').write_text("
            "'<!doctype html><title>Snap</title><body>offline snap content</body>')\n"
        )
        ran = run_build_hooks(root)
        self.assertIn("export_static.py", ran)
        self.assertTrue((root / "dist" / "snap.html").is_file())
        self.assertEqual(find_entry(root), "dist/snap.html")

    def test_start_server_prefers_server_py_and_binds_port(self):
        root = Path(tempfile.mkdtemp())
        (root / "web").mkdir()
        (root / "web" / "index.html").write_text(
            "<!doctype html><title>API App</title><body>hello from api app page</body>"
        )
        (root / "server.py").write_text(
            "import http.server, os\n"
            "PORT=int(os.environ.get('PORT','9999'))\n"
            "class H(http.server.SimpleHTTPRequestHandler):\n"
            "    def __init__(self,*a,**k):\n"
            "        super().__init__(*a, directory=os.path.join(os.path.dirname(__file__),'web'), **k)\n"
            "    def log_message(self,*a): pass\n"
            "http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()\n"
        )
        # pick a free port
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        server = start_server(root, port)
        try:
            self.assertEqual(server.mode, "server.py")
            self.assertTrue(wait_port(port, 3))
            self.assertTrue(server.url.endswith("/"))
        finally:
            server.stop()

    def test_npm_node_start_without_node_modules(self):
        root = Path(tempfile.mkdtemp())
        (root / "public").mkdir()
        (root / "public" / "index.html").write_text(
            "<!doctype html><title>Meal</title><body>family meal planner demo</body>"
        )
        (root / "src").mkdir()
        (root / "src" / "server.js").write_text(
            "const http=require('http');const fs=require('fs');const path=require('path');\n"
            "const PORT=Number(process.env.PORT||4309);\n"
            "http.createServer((req,res)=>{\n"
            "  const body=fs.readFileSync(path.join(__dirname,'../public/index.html'));\n"
            "  res.writeHead(200,{'Content-Type':'text/html'}); res.end(body);\n"
            "}).listen(PORT,'127.0.0.1');\n"
        )
        (root / "package.json").write_text(json.dumps({
            "name": "meal",
            "scripts": {"start": "node src/server.js"},
        }))
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        server = start_server(root, port)
        try:
            self.assertEqual(server.mode, "npm-start")
            self.assertTrue(wait_port(port, 5))
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()


class ViteAndSeedTests(unittest.TestCase):
    def test_is_vite_app_detects_config_and_scripts(self):
        from demo_director.prepare import is_vite_app, vite_config

        root = Path(tempfile.mkdtemp())
        (root / "web").mkdir()
        (root / "web" / "vite.config.ts").write_text(
            "import { defineConfig } from 'vite'; export default defineConfig({ root: '.' });\n"
        )
        (root / "package.json").write_text(json.dumps({
            "name": "lab",
            "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        }))
        self.assertTrue(is_vite_app(root))
        self.assertEqual(vite_config(root), "web/vite.config.ts")

    def test_page_looks_loading_and_ready(self):
        from demo_director.prepare import page_looks_loading

        self.assertTrue(page_looks_loading("Loading the household…"))
        self.assertTrue(page_looks_loading("Loading..."))
        self.assertFalse(page_looks_loading(
            "School Pulse\nTonight\nStudent A · Pinecrest Middle\nMath quiz due Friday"
        ))

    def test_seed_env_sets_foundry_and_school_pulse_data(self):
        from demo_director.prepare import seed_env

        root = Path(tempfile.mkdtemp())
        (root / "fixtures").mkdir()
        (root / "fixtures" / "planner-records.json").write_text("{}")
        (root / "public").mkdir()
        (root / "public" / "app.js").write_text("/* board */")
        (root / "package.json").write_text(json.dumps({
            "scripts": {"dashboard": "tsx src/server.ts", "demo": "node dist/cli.js demo"},
        }))
        env = seed_env(root, {})
        self.assertEqual(env.get("FOUNDRY_SEED"), "1")
        self.assertIn(".demo-director-data", env.get("SCHOOL_PULSE_DATA", ""))

    def test_npm_serve_prefers_demo_with_port(self):
        from demo_director.prepare import _npm_serve_cmd

        root = Path(tempfile.mkdtemp())
        (root / "package.json").write_text(json.dumps({
            "scripts": {
                "demo": "node dist/src/cli.js demo",
                "start": "node dist/src/cli.js serve",
                "dev": "vite --port 4173",
            },
        }))
        cmd = _npm_serve_cmd(root, 4611)
        self.assertIsNotNone(cmd)
        self.assertIn("--port", cmd)
        self.assertIn("4611", cmd)
        self.assertTrue(cmd[0] in {"npm", "node"})
        if cmd[0] == "npm":
            self.assertEqual(cmd[:3], ["npm", "run", "demo"])

    def test_vite_preview_cmd_includes_config_and_port(self):
        from demo_director.prepare import _vite_preview_cmd

        root = Path(tempfile.mkdtemp())
        (root / "vite.config.ts").write_text("export default {}\n")
        (root / "node_modules").mkdir()
        (root / "package.json").write_text(json.dumps({
            "scripts": {"build": "vite build", "preview": "vite preview"},
        }))
        cmd = _vite_preview_cmd(root, 4622)
        self.assertIsNotNone(cmd)
        self.assertIn("preview", cmd)
        self.assertIn("4622", cmd)
        self.assertIn("vite.config.ts", cmd)

    def test_pick_free_port_in_band(self):
        from demo_director.prepare import pick_free_port, release_port

        port = pick_free_port(4600, 4699)
        self.assertTrue(4600 <= port <= 4699)
        release_port(port)


class PrivateRebuildTests(unittest.TestCase):
    def test_skips_private_rebuild_scripts(self):
        from demo_director.prepare import reads_private_sources, run_build_hooks

        root = Path(tempfile.mkdtemp())
        (root / "build_bank.py").write_text(
            "import os\nSRC = os.environ.get(\"HQ_HSPT_ROOT\", \"/workspace/hspt\")\n"
        )
        (root / "export_static.py").write_text(
            "from pathlib import Path\n"
            "Path(\"dist\").mkdir(exist_ok=True)\n"
            "Path(\"dist/ok.html\").write_text(\"<!doctype html><title>Ok</title><body>offline demo content here</body>\")\n"
        )
        self.assertTrue(reads_private_sources(root / "build_bank.py"))
        ran = run_build_hooks(root)
        self.assertNotIn("build_bank.py", ran)
        self.assertIn("export_static.py", ran)


class PlaceholderAndPortTests(unittest.TestCase):
    def test_next_app_prefers_next_start(self):
        from demo_director.prepare import _npm_serve_cmd

        root = Path(tempfile.mkdtemp())
        (root / "next.config.ts").write_text("export default {}\n")
        (root / ".next").mkdir()
        (root / "package.json").write_text(json.dumps({
            "scripts": {
                "demo": "tsx src/bench/cli.ts --mode mock",
                "start": "next start -p 4317",
                "dev": "next dev",
            },
        }))
        cmd = _npm_serve_cmd(root, 4633)
        self.assertEqual(cmd[:3], ["npx", "--no-install", "next"])
        self.assertIn("4633", cmd)

    def test_fill_html_placeholders_inlines_css_comment(self):
        from demo_director.prepare import fill_html_placeholders, run_build_hooks

        root = Path(tempfile.mkdtemp())
        web = root / "web"
        web.mkdir()
        (web / "museum.css").write_text("body{color:#efe;background:#123}")
        (web / "museum.js").write_text("window.READY=1;")
        (web / "index.html").write_text(
            "<!doctype html><title>Museum</title><!--CSS--><body>"
            "<canvas id=c></canvas><div id=hud>Lobby</div></body>"
        )
        (root / "site").mkdir()
        ran = run_build_hooks(root)
        self.assertIn("fill:placeholders", ran)
        html = (web / "index.html").read_text()
        self.assertIn("background:#123", html)
        self.assertIn("<style>", html)
        self.assertTrue((root / "site" / "index.html").is_file())

    def test_render_py_is_a_build_hook(self):
        from demo_director.prepare import run_build_hooks, find_entry

        root = Path(tempfile.mkdtemp())
        (root / "render.py").write_text(
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parent\n"
            "(root / 'dist').mkdir(exist_ok=True)\n"
            "(root / 'dist' / 'season-book-snapshot.html').write_text("
            "'<!doctype html><title>Season</title><style>body{color:red}</style>"
            "<body>season book snapshot content here</body>')\n"
        )
        ran = run_build_hooks(root)
        self.assertIn("render.py", ran)
        self.assertEqual(find_entry(root), "dist/season-book-snapshot.html")

    def test_discover_port_env_keys_reads_server_py(self):
        from demo_director.prepare import discover_port_env_keys, _port_env

        root = Path(tempfile.mkdtemp())
        (root / "server.py").write_text(
            "import os\nPORT=int(os.environ.get('HUB_PORT', '4646'))\n"
        )
        keys = discover_port_env_keys(root)
        self.assertIn("HUB_PORT", keys)
        env = _port_env(4615, root)
        self.assertEqual(env.get("HUB_PORT"), "4615")
        self.assertEqual(env.get("PORT"), "4615")

    def test_hub_style_server_binds_claimed_port(self):
        from demo_director.prepare import start_server, wait_port

        root = Path(tempfile.mkdtemp())
        web = root / "web"
        web.mkdir()
        (web / "index.html").write_text(
            "<!doctype html><title>Hub</title><body>hub projects board content</body>"
        )
        (root / "server.py").write_text(
            "import http.server, os\n"
            "PORT=int(os.environ.get('HUB_PORT','4646'))\n"
            "class H(http.server.SimpleHTTPRequestHandler):\n"
            "    def __init__(self,*a,**k):\n"
            "        super().__init__(*a, directory=os.path.join(os.path.dirname(__file__),'web'), **k)\n"
            "    def log_message(self,*a): pass\n"
            "http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()\n"
        )
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        server = start_server(root, port)
        try:
            self.assertEqual(server.mode, "server.py")
            self.assertTrue(wait_port(port, 3))
        finally:
            server.stop()
