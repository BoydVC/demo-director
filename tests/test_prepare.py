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
