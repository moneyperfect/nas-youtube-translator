"""简单的内存速率限制器。"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self._window
        ]
        if len(self._requests[client_ip]) >= self._max_requests:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试。")
        self._requests[client_ip].append(now)
        return await call_next(request)
