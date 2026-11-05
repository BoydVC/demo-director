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
        self.assertTrue(env.get("SCHOOL_PULSE_DATA", "").endswith(".demo-director-data"))

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
        self.assertEqual(cmd[:3], ["npm", "run", "demo"])
        self.assertIn("--port", cmd)
        self.assertIn("4611", cmd)

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
