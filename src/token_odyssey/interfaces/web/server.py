"""Dependency-free HTTP adapter for a loopback-only playtest page."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .session import WebError, WebSession


STATIC = Path(__file__).with_name("static")
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}


def create_server(session: WebSession, port=8000):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Polls and user-authored text do not belong in the console log.
            pass

        def _send(self, status, data, content_type="application/json"):
            payload = json.dumps(data, ensure_ascii=False).encode() if content_type == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def _local(self):
            allowed = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:
                raise WebError("仅支持 localhost 访问。", 403)
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{host}" for host in allowed}:
                raise WebError("不允许跨站访问本地运行。", 403)

        def do_GET(self):
            try:
                self._local()
                url = urlsplit(self.path)
                if url.path in ASSETS:
                    filename, mime = ASSETS[url.path]
                    self._send(200, (STATIC / filename).read_bytes(), mime)
                elif url.path == "/api/catalog":
                    self._send(200, session.catalog())
                elif url.path == "/api/state":
                    actor = parse_qs(url.query).get("actor", [None])[0]
                    self._send(200, session.snapshot(actor))
                elif url.path == "/api/observer":
                    self._send(200, session.observer_snapshot())
                else:
                    raise WebError("页面不存在。", 404)
            except WebError as exc:
                self._send(exc.status, {"error": str(exc)})

        def do_POST(self):
            try:
                self._local()
                if self.headers.get("X-Playtest-Token") != session.token:
                    raise WebError("页面凭证已失效，请刷新页面。", 403)
                if self.headers.get_content_type() != "application/json":
                    raise WebError("需要 JSON 表单。", 415)
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise WebError("表单大小无效。", 413)
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise WebError("表单必须是对象。", 400)
                path = urlsplit(self.path).path
                if path == "/api/start":
                    result = session.start(payload)
                elif path in {"/api/submit", "/api/advance", "/api/pause", "/api/stop"}:
                    result = session.command(path.rsplit("/", 1)[1], payload)
                else:
                    raise WebError("接口不存在。", 404)
                self._send(202, result)
            except WebError as exc:
                self._send(exc.status, {"error": str(exc)})
            except (ValueError, ValidationError):
                self._send(400, {"error": "表单参数无效，请检查角色配置、轮数与动作字段。"})
            except Exception:
                self._send(500, {"error": "本地服务处理失败，请检查服务端配置与运行目录。"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(scenario, config=None, *, port=8000, runs_dir="runs", llm_timeout=60):
    session = WebSession(scenario, config, runs_dir=runs_dir, llm_timeout=llm_timeout)
    server = create_server(session, port)
    print(f"Token Odyssey 网页已启动：http://localhost:{server.server_port}", flush=True)
    print("按 Ctrl+C 停止服务；网页刷新会保留当前运行，服务重启后需开始新测试。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
