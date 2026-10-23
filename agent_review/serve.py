# -*- coding: utf-8 -*-
"""Serve the report UI plus a board's review_out.

  python -m agent_review.serve --board boards\\0918全量
  python -m agent_review.serve --board boards\\0918全量 --port 8765

Opens the Vue app in report_ui/dist. Review JSON, figures and the schematic
PDF stay in review_out and are served on the same origin.

Also exposes shared expert feedback API (one JSON for all viewers):
  GET  /api/feedback
  PUT  /api/feedback          body: full map {rule_id: {mark, note, ...}}
  PUT  /api/feedback/<id>     body: one record {mark, note, ...}
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from kernel.util import ROOT, out_dir, resolve_board_path

FEEDBACK_NAME = "expert_feedback.json"
_feedback_lock = threading.Lock()


def _read_feedback(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".feedback_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class _ReviewHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **getattr(SimpleHTTPRequestHandler, "extensions_map", {}),
        ".mjs": "text/javascript",
    }

    # Set by serve_review via partial-like subclassing
    review_root: Path = Path(".")

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def translate_path(self, path: str) -> str:
        """评审文件优先；其余走 report_ui/dist，前端路由回 index.html。"""
        rel = unquote(urlparse(path).path or "/").lstrip("/")
        review = Path(self.review_root).resolve()
        if rel:
            candidate = (review / rel).resolve()
            if candidate.is_file() and _inside(candidate, review):
                return str(candidate)
        spa = (ROOT / "report_ui" / "dist").resolve()
        if rel:
            asset = (spa / rel).resolve()
            if asset.is_file() and _inside(asset, spa):
                return str(asset)
        index = spa / "index.html"
        name = Path(rel).name
        if index.is_file() and (not rel or "." not in name):
            return str(index)
        return str(spa / rel) if rel else str(index)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def _feedback_path(self) -> Path:
        return Path(self.review_root) / FEEDBACK_NAME

    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return None
        if n > 2_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if path == "/api/feedback" or path == "/api/feedback/":
            with _feedback_lock:
                data = _read_feedback(self._feedback_path())
            self._send_json(200, data)
            return
        super().do_GET()

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if path == "/api/feedback" or path == "/api/feedback/":
            try:
                body = self._read_body_json()
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            if not isinstance(body, dict):
                self._send_json(400, {"ok": False, "error": "body must be object"})
                return
            # Full replace of shared map (client sends merged snapshot)
            cleaned: dict[str, Any] = {}
            for k, v in body.items():
                rid = str(k).strip()
                if not rid or not isinstance(v, dict):
                    continue
                cleaned[rid] = {
                    "mark": str(v.get("mark") or ""),
                    "note": str(v.get("note") or ""),
                    "updated_at": str(v.get("updated_at") or ""),
                    "by": str(v.get("by") or ""),
                }
            path_f = self._feedback_path()
            with _feedback_lock:
                _atomic_write_json(path_f, cleaned)
            self._send_json(200, {"ok": True, "count": len(cleaned)})
            return
        if path.startswith("/api/feedback/"):
            rid = path[len("/api/feedback/") :].strip("/")
            if not rid or "/" in rid:
                self._send_json(400, {"ok": False, "error": "bad rule id"})
                return
            try:
                body = self._read_body_json()
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            if not isinstance(body, dict):
                self._send_json(400, {"ok": False, "error": "body must be object"})
                return
            rec = {
                "mark": str(body.get("mark") or ""),
                "note": str(body.get("note") or ""),
                "updated_at": str(body.get("updated_at") or ""),
                "by": str(body.get("by") or ""),
            }
            path_f = self._feedback_path()
            with _feedback_lock:
                all_fb = _read_feedback(path_f)
                all_fb[rid] = rec
                _atomic_write_json(path_f, all_fb)
            self._send_json(200, {"ok": True, "id": rid})
            return
        self.send_error(405, "Method Not Allowed")


def _lan_ips() -> list[str]:
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    if not found:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                found.append(ip)
        except OSError:
            pass
    return found


def serve_review(
    board_dir: str | Path,
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    board = resolve_board_path(board_dir)
    root = out_dir(board)
    if not root.is_dir():
        print(f"ERR\treview_out missing: {root}", file=sys.stderr)
        return 2
    spa = ROOT / "report_ui" / "dist" / "index.html"
    if not spa.is_file():
        print(
            f"ERR\treport_ui build missing: {spa}\n"
            "\t  cd report_ui && npm install && npm run build",
            file=sys.stderr,
        )
        return 2
    pdfjs = root / "pdfjs" / "web" / "viewer.html"
    sch = root / "sch" / "schematic.pdf"
    if not pdfjs.is_file() or not sch.is_file():
        print(
            "WARN\tPDF.js viewer assets incomplete — rebuild report:\n"
            f"\t  python -m agent_review.run --board {board}\n"
            f"\t  (or python vendor/expert_html/build_expert_html.py --out-dir {root})",
            file=sys.stderr,
        )

    class Handler(_ReviewHandler):
        review_root = root

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

    ThreadingHTTPServer.allow_reuse_address = True
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"ERR\tbind {host}:{port} failed: {exc}", file=sys.stderr)
        return 1

    local_url = f"http://127.0.0.1:{port}/"
    print(f"SERVE\t{root}")
    print(f"APP\t{spa.parent}")
    print(f"BIND\t{host}:{port}")
    print(f"URL\t{local_url}")
    for ip in _lan_ips():
        print(f"URL\thttp://{ip}:{port}/")
    print(f"FEEDBACK\t{root / FEEDBACK_NAME}  (GET/PUT /api/feedback)")
    print("HINT\tCtrl+C to stop · 须用上述 http URL（勿双击 HTML）· 批注全员共享落此文件")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(local_url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nSTOP")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Serve review_out for expert_review PDF.js")
    ap.add_argument("--board", required=True, help="Board dir under boards/ or absolute path")
    ap.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default 0.0.0.0 so LAN IP access works)",
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return serve_review(
        args.board,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
