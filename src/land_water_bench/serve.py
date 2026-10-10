"""Serve only the exported dashboard, never model configuration or raw responses."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Handler(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, "Directory listing is disabled")

    def permitted_path(self):
        relative = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
        allowed = {"index.html", "app.js", "style.css", "data.json", "comparison.png", "RESULTS.md",
                   "display.ttf", "FONT-LICENSE.txt", "LICENSE", "THIRD_PARTY_NOTICES.md",
                   "GSHHG-LICENSE.txt", "GSHHG-GPL.txt"}
        if "/" in relative or "\\" in relative or relative.startswith(".") or not (
            relative in allowed or relative.endswith(".details.json")
        ):
            self.send_error(404)
            return False
        resolved = (Path(self.directory) / relative).resolve()
        if resolved.parent != Path(self.directory).resolve():
            self.send_error(404)
            return False
        return True

    def do_GET(self):
        if not self.permitted_path():
            return
        super().do_GET()

    def do_HEAD(self):
        if not self.permitted_path():
            return
        super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'")
        super().end_headers()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8732)
    args = parser.parse_args(argv)
    if not (args.directory / "index.html").is_file():
        parser.error("directory must contain the exported index.html")
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(args.directory.resolve())))
    print(f"Dashboard: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
