"""
Simple Python HTTP File Server
Zero dependencies — standard library only.

Usage:
    python file_server.py [--port PORT] [--dir DIRECTORY]

Downloading a file:
    curl http://localhost:8080/filename.txt -O

Uploading a file:
    curl -X POST http://localhost:8080/ -F "file=@/path/to/file.txt"
"""

import argparse
import http.server
import re
import sys
import urllib.parse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Simple HTTP file server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--dir", type=Path, default=Path.cwd(), help="Directory to serve (default: cwd)")
    return parser.parse_args()


def parse_multipart(body: bytes, boundary: str) -> dict[str, tuple[str, bytes]]:
    """
    Parse a multipart/form-data body. Returns a dict of:
        field_name -> (filename, data)
    filename is "" for non-file fields.
    """
    sep = ("--" + boundary).encode()
    end = ("--" + boundary + "--").encode()
    parts = {}

    for chunk in body.split(sep):
        if not chunk or chunk.strip() in (b"", end, b"--"):
            continue
        # Split headers from body on the first blank line
        if b"\r\n\r\n" in chunk:
            raw_headers, _, content = chunk.partition(b"\r\n\r\n")
        else:
            continue
        content = content.rstrip(b"\r\n")

        # Parse Content-Disposition header
        name = filename = ""
        for line in raw_headers.decode(errors="replace").splitlines():
            if line.lower().startswith("content-disposition"):
                m = re.search(r'name="([^"]*)"', line)
                if m:
                    name = m.group(1)
                m = re.search(r'filename="([^"]*)"', line)
                if m:
                    filename = m.group(1)

        if name:
            parts[name] = (filename, content)

    return parts


def make_handler(serve_dir: Path):
    class Handler(http.server.BaseHTTPRequestHandler):

        def do_GET(self):
            """Serve a file for download."""
            target = self._resolve_path()
            if target is None:
                return

            if target.is_dir():
                # List directory contents
                entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
                lines = [str(e.relative_to(serve_dir)) + ("/" if e.is_dir() else "") for e in entries]
                body = "\n".join(lines).encode()
                self._respond(200, "text/plain", body)
            elif target.is_file():
                data = target.read_bytes()
                self._respond(200, "application/octet-stream", data,
                              extra_headers={"Content-Disposition": f'attachment; filename="{target.name}"'})
            else:
                self._send_error(404, f"Not found: {self.path}")

        def do_POST(self):
            """Accept a file upload."""
            target = self._resolve_path()
            if target is None:
                return

            upload_dir = target if target.is_dir() else target.parent
            content_type = self.headers.get("Content-Type", "")

            if "multipart/form-data" in content_type:
                # curl -F "file=@path/to/file"
                m = re.search(r"boundary=(\S+)", content_type)
                if not m:
                    self._send_error(400, "Missing multipart boundary")
                    return
                boundary = m.group(1).strip('"')
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                parts = parse_multipart(body, boundary)
                if "file" not in parts:
                    self._send_error(400, "No 'file' field in upload")
                    return
                orig_filename, data = parts["file"]
                filename = Path(orig_filename).name or "upload.bin"
                dest = upload_dir / filename
                dest.write_bytes(data)
                msg = f"Uploaded: {dest.relative_to(serve_dir)}"
            else:
                # curl -X POST --data-binary @file?filename=foo.txt
                filename = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query
                ).get("filename", ["upload.bin"])[0]
                dest = upload_dir / Path(filename).name
                length = int(self.headers.get("Content-Length", 0))
                dest.write_bytes(self.rfile.read(length))
                msg = f"Uploaded: {dest.relative_to(serve_dir)}"

            print(f"[UPLOAD] {msg}")
            self._respond(200, "text/plain", msg.encode())

        # ── Helpers ──────────────────────────────────────────────────────────

        def _resolve_path(self):
            """Map a URL path to a real path, rejecting traversal attempts."""
            url_path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            target = (serve_dir / url_path.lstrip("/")).resolve()
            if not str(target).startswith(str(serve_dir)):
                self._send_error(403, "Access denied")
                return None
            return target

        def _respond(self, status, content_type, body, extra_headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status, message):
            body = message.encode()
            self._respond(status, "text/plain", body)

        def log_message(self, fmt, *args):
            print(f"[{self.client_address[0]}] {fmt % args}")

    return Handler


def main():
    args = parse_args()
    serve_dir = args.dir.resolve()

    if not serve_dir.is_dir():
        print(f"Error: '{serve_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    handler = make_handler(serve_dir)
    server = http.server.HTTPServer(("", args.port), handler)

    print(f"Serving '{serve_dir}' on port {args.port}")
    print(f"  Download:  curl http://localhost:{args.port}/<filename> -O")
    print(f"  Upload:    curl -X POST http://localhost:{args.port}/ -F 'file=@<path>'")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()