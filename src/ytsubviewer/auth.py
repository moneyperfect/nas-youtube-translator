"""基于本地 token 的 API 认证。"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token
        self._public_paths = ("/", "/api/bootstrap", "/api/health", "/static/")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self._public_paths):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {self._token}":
            raise HTTPException(status_code=401, detail="未授权访问。")
        return await call_next(request)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def save_session_token(token: str, runtime_dir: Path) -> Path:
    path = runtime_dir / "session-token.txt"
    path.write_text(token, encoding="utf-8")
    return path
