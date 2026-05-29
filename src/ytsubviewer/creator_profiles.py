from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ytsubviewer.config import Settings
from ytsubviewer.models import VideoMetadata


@dataclass
class CreatorProfile:
    profile_id: str
    channel_id: str = ""
    channel_name: str = ""
    uploader: str = ""
    style_preset: str = "default"
    glossary_text: str = ""
    protected_terms_text: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CreatorProfile":
        return cls(
            profile_id=str(payload.get("profile_id", "")).strip(),
            channel_id=str(payload.get("channel_id", "")).strip(),
            channel_name=str(payload.get("channel_name", "")).strip(),
            uploader=str(payload.get("uploader", "")).strip(),
            style_preset=str(payload.get("style_preset", "default")).strip() or "default",
            glossary_text=str(payload.get("glossary_text", "") or ""),
            protected_terms_text=str(payload.get("protected_terms_text", "") or ""),
            updated_at=float(payload.get("updated_at", time.time()) or time.time()),
        )


class CreatorProfileStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = self.settings.data_root / "creator_profiles.json"

    def list_profiles(self) -> list[CreatorProfile]:
        payload = self._load_payload()
        profiles = [CreatorProfile.from_dict(item) for item in (payload.get("profiles") or []) if isinstance(item, dict)]
        profiles.sort(key=lambda item: item.updated_at, reverse=True)
        return profiles

    def get_for_metadata(self, metadata: VideoMetadata) -> CreatorProfile | None:
        profile_id = self._profile_id(metadata)
        if not profile_id:
            return None
        for profile in self.list_profiles():
            if profile.profile_id == profile_id:
                return profile
        return None

    def save_for_metadata(
        self,
        metadata: VideoMetadata,
        *,
        style_preset: str,
        glossary_text: str,
        protected_terms_text: str,
    ) -> CreatorProfile:
        profile_id = self._profile_id(metadata)
        if not profile_id:
            raise RuntimeError("当前视频缺少可识别的频道信息，暂时无法保存创作者配置。")

        profiles = self.list_profiles()
        profile = next((item for item in profiles if item.profile_id == profile_id), None)
        if profile is None:
            profile = CreatorProfile(profile_id=profile_id)
            profiles.append(profile)

        profile.channel_id = metadata.channel_id or ""
        profile.channel_name = metadata.channel_name or ""
        profile.uploader = metadata.uploader or ""
        profile.style_preset = (style_preset or "default").strip() or "default"
        profile.glossary_text = glossary_text
        profile.protected_terms_text = protected_terms_text
        profile.updated_at = time.time()
        self._save_profiles(profiles)
        return profile

    def _load_payload(self) -> dict[str, object]:
        if not self.path.exists():
            return {"version": 1, "profiles": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "profiles": []}
        if not isinstance(payload, dict):
            return {"version": 1, "profiles": []}
        payload.setdefault("version", 1)
        payload.setdefault("profiles", [])
        return payload

    def _save_profiles(self, profiles: list[CreatorProfile]) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "profiles": [profile.to_dict() for profile in profiles],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    @staticmethod
    def _profile_id(metadata: VideoMetadata) -> str:
        return (
            (metadata.channel_id or "").strip()
            or (metadata.channel_name or "").strip()
            or (metadata.uploader or "").strip()
        )
