"""Lightweight request observability for OpsCenter APIs."""
from __future__ import annotations

import logging
import os
import re
import time
import uuid


logger = logging.getLogger("opscenter.performance")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _slow_request_ms() -> float:
    try:
        return max(50.0, float(os.getenv("OPS_SLOW_REQUEST_MS", "800")))
    except ValueError:
        return 800.0


class PerformanceMiddleware:
    """Attach request IDs and timings without buffering response bodies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
        status_code = 500

        async def send_with_timing(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                elapsed_ms = (time.perf_counter() - started) * 1000
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-response-time-ms", f"{elapsed_ms:.2f}".encode("ascii")),
                    (b"server-timing", f"app;dur={elapsed_ms:.2f}".encode("ascii")),
                ])
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            path = scope.get("path", "")
            if path.startswith("/api/") and elapsed_ms >= _slow_request_ms():
                logger.warning(
                    "slow_request request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                    request_id,
                    scope.get("method", ""),
                    path,
                    status_code,
                    elapsed_ms,
                )
