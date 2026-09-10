"""Dependency-free loopback server for the full campaign flow."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from token_odyssey.interfaces.web.session import WebError
from token_odyssey.orchestration.session import CampaignSession


STATIC = Path(__file__).with_name("static")
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}


def create_server(session: CampaignSession, port=8000):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, status, data, content_type="application/json"):
            payload = json.dumps(data, ensure_ascii=False).encode() if content_type == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
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
                target = urlsplit(self.path)
                path = target.path
                if path in ASSETS:
                    filename, mime = ASSETS[path]
                    self._send(200, (STATIC / filename).read_bytes(), mime)
                elif path == "/api/catalog":
                    self._send(200, session.catalog())
                elif path == "/api/state":
                    self._send(200, session.snapshot())
                elif path == "/api/debug":
                    raw_cursor = parse_qs(target.query).get("cursor", ["0"])[0]
                    if not raw_cursor.isdigit():
                        raise WebError("调试游标无效。", 400)
                    self._send(200, session.debug_snapshot(int(raw_cursor)))
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
                if not 0 < size <= 131072:
                    raise WebError("表单大小无效。", 413)
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise WebError("表单必须是对象。", 400)
                operation = urlsplit(self.path).path.rsplit("/", 1)[-1]
                if operation == "start":
                    result = session.start(payload)
                elif operation == "resume":
                    campaign_id = payload.get("campaign_id", "")
                    if not isinstance(campaign_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", campaign_id):
                        raise WebError("存档 ID 无效。", 400)
                    result = session.resume(campaign_id)
                elif operation in {"enter-act", "submit", "advance", "pause", "end-act",
                                   "developer-instruction", "retry", "abort"}:
                    result = session.command(operation, payload)
                else:
                    raise WebError("接口不存在。", 404)
                self._send(202, result)
            except WebError as exc:
                self._send(exc.status, {"error": str(exc)})
            except (ValueError, ValidationError) as exc:
                self._send(400, {"error": f"表单或存档无效：{exc}"})
            except Exception:
                self._send(500, {"error": "本地 Campaign 服务处理失败，请查看开发者信息。"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(config, *, port=8000, runs_dir="runs", llm_timeout=120, resume=None):
    session = CampaignSession(config, runs_dir=runs_dir, llm_timeout=llm_timeout)
    if resume is not None:
        session.resume(resume)
    server = create_server(session, port)
    print(f"Token Odyssey 完整游玩已启动：http://localhost:{server.server_port}", flush=True)
    print("浏览器刷新可继续；服务重启可从幕边界存档恢复。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
