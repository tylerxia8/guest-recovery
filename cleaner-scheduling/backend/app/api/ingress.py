from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.clock import Clock

logger = logging.getLogger("app.security")


class IngressGuard:
    def __init__(self, app: ASGIApp, proxy_secret: str, clock: Clock) -> None:
        self.app = app
        self.proxy_secret = proxy_secret
        self.clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)
        proxy_names = (b"x-turnover-client-ip", b"x-turnover-proxy-time", b"x-turnover-proxy-signature")
        state = scope.setdefault("state", {})
        if any(name in headers for name in proxy_names):
            try:
                if not self.proxy_secret or any(len(headers.get(name, [])) != 1 for name in proxy_names):
                    raise ValueError("Invalid proxy metadata")
                address, timestamp, signature = (headers[name][0].decode("ascii") for name in proxy_names)
                if not timestamp.isdecimal() or len(timestamp) > 16:
                    raise ValueError("Invalid proxy timestamp")
                if abs(self.clock.now().timestamp() - int(timestamp)) > 60:
                    raise ValueError("Expired proxy timestamp")
                parsed_address = ipaddress.ip_address(address)
                payload = f"{timestamp}\n{scope['method']}\n{scope['path']}\n{address}"
                expected = hmac.new(self.proxy_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, signature):
                    raise ValueError("Invalid proxy signature")
            except (ValueError, UnicodeError):
                await self.reject(scope, receive, send, 403, "proxy_identity_invalid", "Access unavailable.")
                return
            state["client_address"] = str(parsed_address)
        else:
            peer = scope.get("client")
            state["client_address"] = peer[0] if peer else "unknown"
        lengths = headers.get(b"content-length", [])
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 16:
                    raise ValueError("Invalid content length")
                declared_length = int(lengths[0])
            except ValueError:
                await self.reject(scope, receive, send, 400, "invalid_content_length", "Invalid request length.")
                return
            if declared_length > 6 * 1024 * 1024:
                await self.reject(scope, receive, send, 413, "payload_too_large", "Request body exceeds 6 MB.")
                return
        chunks: list[bytes] = []
        total = 0
        exceeded = False
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    total += len(chunk)
                    if total > 6 * 1024 * 1024:
                        exceeded = True
                        break
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self.reject(scope, receive, send, 408, "request_timeout", "Request body took too long.")
            return
        if exceeded:
            await self.reject(scope, receive, send, 413, "payload_too_large", "Request body exceeds 6 MB.")
            return
        body = b"".join(chunks)
        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, buffered_receive, send)

    async def reject(self, scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str) -> None:
        request_id = scope.get("state", {}).get("request_id", "")
        logger.warning("ingress_rejected code=%s status=%s request_id=%s", code, status, request_id)
        response = JSONResponse(
            {"error": {"code": code, "message": message, "details": {}, "request_id": request_id}},
            status_code=status,
        )
        await response(scope, receive, send)
