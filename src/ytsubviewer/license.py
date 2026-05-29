from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ytsubviewer.config import APP_VERSION, Settings


class LicenseManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = self.settings.config_dir / "license_state.json"
        self.trial_days = 14
        self.offline_grace_days = 14

    def status(self) -> dict[str, Any]:
        state = self._load_state()
        installed_at = float(state.get("installed_at", time.time()) or time.time())
        now = time.time()
        activated = bool(state.get("license_token"))
        if activated:
            payload = dict(state.get("license_payload") or {})
            expires_at = float(payload["expires_at"]) if payload.get("expires_at") else None
            last_validated_at = float(state.get("last_validated_at", installed_at) or installed_at)
            offline_grace_until = last_validated_at + self.offline_grace_days * 86400
            active = (expires_at is None or now <= expires_at) and now <= offline_grace_until
            mode = "activated"
            status_text = "active" if active else "expired"
        else:
            trial_until = installed_at + self.trial_days * 86400
            active = now <= trial_until
            mode = "trial"
            status_text = "trial" if active else "expired"
            expires_at = trial_until
            offline_grace_until = None
            payload = {}

        return {
            "mode": mode,
            "status": status_text,
            "active": active,
            "version": APP_VERSION,
            "licensee": payload.get("licensee", ""),
            "plan": payload.get("plan", ""),
            "expires_at": expires_at,
            "offline_grace_until": offline_grace_until,
            "config_path": str(self.path),
            "requires_activation": not activated,
        }

    def activate(self, license_token: str) -> dict[str, Any]:
        token = (license_token or "").strip()
        if not token:
            raise RuntimeError("License key 不能为空。")
        payload = self._decode_token(token)
        state = self._load_state()
        state["license_token"] = token
        state["license_payload"] = payload
        state["last_validated_at"] = time.time()
        self._save_state(state)
        return self.status()

    def deactivate(self) -> dict[str, Any]:
        state = self._load_state()
        state.pop("license_token", None)
        state.pop("license_payload", None)
        state.pop("last_validated_at", None)
        self._save_state(state)
        return self.status()

    def _decode_token(self, token: str) -> dict[str, Any]:
        secret = os.getenv("YTSUBVIEWER_LICENSE_SECRET", "").strip()
        if token == "DEV-LICENSE" and os.getenv("YTSUBVIEWER_DEV_LICENSE", "").strip() == "1" and not getattr(sys, "frozen", False):
            return {
                "licensee": "Developer",
                "plan": "dev",
                "issued_at": time.time(),
            }

        try:
            encoded_payload, signature = token.split(".", 1)
        except ValueError as exc:
            raise RuntimeError("License key 格式无效。") from exc
        payload_bytes = self._urlsafe_b64decode(encoded_payload)
        if secret:
            expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise RuntimeError("License key 校验失败。")
        else:
            raise RuntimeError("当前版本未配置授权签名密钥，无法校验此 License key。")
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("License key 载荷无效。") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("License key 载荷无效。")
        return payload

    def _load_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "installed_at": time.time()}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("version", 1)
        payload.setdefault("installed_at", time.time())
        return payload

    def _save_state(self, payload: dict[str, Any]) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @staticmethod
    def _urlsafe_b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
