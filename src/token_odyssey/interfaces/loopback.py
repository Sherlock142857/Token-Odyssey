"""Shared HTTP safety boundary for Token Odyssey's loopback-only interfaces."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast

from token_odyssey.interfaces.web.session import WebError


class LoopbackHandler(BaseHTTPRequestHandler):
    """Base handler for local-only JSON APIs and fixed static assets."""

    allow_data_images = False

    def log_message(self, format: str, *args: Any) -> None:
        """Keep polling and user-authored text out of console logs."""

    def send_payload(self, status: int, data: Any, content_type: str = "application/json") -> None:
        payload = json.dumps(data, ensure_ascii=False).encode() if content_type == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        image_policy = " img-src 'self' data:;" if self.allow_data_images else ""
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self';"
            f"{image_policy} connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def require_local_request(self) -> None:
        server_port = cast(HTTPServer, self.server).server_port
        allowed = {f"localhost:{server_port}", f"127.0.0.1:{server_port}"}
        if self.headers.get("Host") not in allowed:
            raise WebError("仅支持 localhost 访问。", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed}:
            raise WebError("不允许跨站访问本地运行。", 403)

    def read_json_object(self, maximum_bytes: int) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise WebError("需要 JSON 表单。", 415)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= maximum_bytes:
            raise WebError("表单大小无效。", 413)
        payload = json.loads(self.rfile.read(size))
        if not isinstance(payload, dict):
            raise WebError("表单必须是对象。", 400)
        return payload

    def send_static(
        self,
        request_path: str,
        directory: Path,
        assets: dict[str, tuple[str, str]],
    ) -> bool:
        asset = assets.get(request_path)
        if asset is None:
            return False
        filename, content_type = asset
        self.send_payload(200, (directory / filename).read_bytes(), content_type)
        return True
