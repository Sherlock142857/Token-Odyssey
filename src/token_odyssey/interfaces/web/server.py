"""Dependency-free HTTP adapter for a loopback-only playtest page."""

from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from token_odyssey.constants import DEFAULT_ACT_LLM_TIMEOUT_SECONDS, DEFAULT_LOOPBACK_PORT
from token_odyssey.interfaces.loopback import LoopbackHandler

from .session import WebError, WebSession

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}
PLAYTEST_REQUEST_LIMIT = 64 * 1024


def create_server(session: WebSession, port: int = DEFAULT_LOOPBACK_PORT) -> ThreadingHTTPServer:
    class Handler(LoopbackHandler):
        allow_data_images = True

        def do_GET(self):
            try:
                self.require_local_request()
                url = urlsplit(self.path)
                if self.send_static(url.path, STATIC, ASSETS):
                    return
                elif url.path == "/api/catalog":
                    self.send_payload(200, session.catalog())
                elif url.path == "/api/state":
                    actor = parse_qs(url.query).get("actor", [None])[0]
                    self.send_payload(200, session.snapshot(actor))
                elif url.path == "/api/observer":
                    self.send_payload(200, session.observer_snapshot())
                else:
                    raise WebError("页面不存在。", 404)
            except WebError as exc:
                self.send_payload(exc.status, {"error": str(exc)})

        def do_POST(self):
            try:
                self.require_local_request()
                if self.headers.get("X-Playtest-Token") != session.token:
                    raise WebError("页面凭证已失效，请刷新页面。", 403)
                payload = self.read_json_object(PLAYTEST_REQUEST_LIMIT)
                path = urlsplit(self.path).path
                if path == "/api/start":
                    result = session.start(payload)
                elif path in {"/api/submit", "/api/advance", "/api/pause", "/api/stop"}:
                    result = session.command(path.rsplit("/", 1)[1], payload)
                else:
                    raise WebError("接口不存在。", 404)
                self.send_payload(202, result)
            except WebError as exc:
                self.send_payload(exc.status, {"error": str(exc)})
            except (ValueError, ValidationError):
                self.send_payload(400, {"error": "表单参数无效，请检查角色配置、轮数与动作字段。"})
            except Exception:
                self.send_payload(500, {"error": "本地服务处理失败，请检查服务端配置与运行目录。"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(
    scenario,
    config=None,
    *,
    port=DEFAULT_LOOPBACK_PORT,
    runs_dir="runs",
    llm_timeout=DEFAULT_ACT_LLM_TIMEOUT_SECONDS,
):
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
