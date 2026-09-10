"""Dependency-free loopback server for the full campaign flow."""

import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from token_odyssey.constants import DEFAULT_CAMPAIGN_LLM_TIMEOUT_SECONDS, DEFAULT_LOOPBACK_PORT
from token_odyssey.interfaces.loopback import LoopbackHandler
from token_odyssey.interfaces.web.session import WebError
from token_odyssey.orchestration.agents import concise_validation_error
from token_odyssey.orchestration.session import CampaignSession

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}
CAMPAIGN_REQUEST_LIMIT = 128 * 1024


def create_server(session: CampaignSession, port: int = DEFAULT_LOOPBACK_PORT) -> ThreadingHTTPServer:
    class Handler(LoopbackHandler):
        def do_GET(self):
            try:
                self.require_local_request()
                target = urlsplit(self.path)
                path = target.path
                if self.send_static(path, STATIC, ASSETS):
                    return
                elif path == "/api/catalog":
                    self.send_payload(200, session.catalog())
                elif path == "/api/state":
                    self.send_payload(200, session.snapshot())
                elif path == "/api/debug":
                    raw_cursor = parse_qs(target.query).get("cursor", ["0"])[0]
                    if not raw_cursor.isdigit():
                        raise WebError("调试游标无效。", 400)
                    self.send_payload(200, session.debug_snapshot(int(raw_cursor)))
                else:
                    raise WebError("页面不存在。", 404)
            except WebError as exc:
                self.send_payload(exc.status, {"error": str(exc)})

        def do_POST(self):
            try:
                self.require_local_request()
                if self.headers.get("X-Playtest-Token") != session.token:
                    raise WebError("页面凭证已失效，请刷新页面。", 403)
                payload = self.read_json_object(CAMPAIGN_REQUEST_LIMIT)
                operation = urlsplit(self.path).path.rsplit("/", 1)[-1]
                if operation == "start":
                    result = session.start(payload)
                elif operation == "resume":
                    campaign_id = payload.get("campaign_id", "")
                    if not isinstance(campaign_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", campaign_id):
                        raise WebError("存档 ID 无效。", 400)
                    result = session.resume(campaign_id)
                elif operation in {
                    "enter-act",
                    "submit",
                    "advance",
                    "pause",
                    "end-act",
                    "developer-instruction",
                    "retry",
                    "abort",
                }:
                    result = session.command(operation, payload)
                else:
                    raise WebError("接口不存在。", 404)
                self.send_payload(202, result)
            except WebError as exc:
                self.send_payload(exc.status, {"error": str(exc)})
            except (ValueError, ValidationError) as exc:
                self.send_payload(400, {"error": f"表单或存档无效：{concise_validation_error(exc)}"})
            except Exception:
                self.send_payload(500, {"error": "本地 Campaign 服务处理失败，请查看开发者信息。"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(
    config,
    *,
    port=DEFAULT_LOOPBACK_PORT,
    runs_dir="runs",
    llm_timeout=DEFAULT_CAMPAIGN_LLM_TIMEOUT_SECONDS,
    resume=None,
):
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
