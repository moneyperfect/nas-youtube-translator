from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from ytsubviewer.config import APP_VERSION, Settings


class UpdateService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        feed_url = (self.settings.update_feed_url or "").strip()
        base = {
            "current_version": APP_VERSION,
            "feed_url": feed_url,
            "configured": bool(feed_url),
            "update_available": False,
            "latest_version": APP_VERSION,
            "download_url": "",
            "message": "未配置更新源。",
        }
        if not feed_url:
            return base
        try:
            with urlopen(feed_url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError):
            base["message"] = "无法连接更新源。"
            return base
        latest_version = str(payload.get("version", APP_VERSION)).strip() or APP_VERSION
        base.update(
            {
                "latest_version": latest_version,
                "download_url": str(payload.get("download_url", "")).strip(),
                "update_available": latest_version != APP_VERSION,
                "message": "发现新版本。" if latest_version != APP_VERSION else "当前已是最新版本。",
            }
        )
        return base
