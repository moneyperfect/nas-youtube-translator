from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ytsubviewer.models import TranslationControlConfig, TranslationGlossaryEntry


load_dotenv(override=False)


APP_NAME = "YTSubViewer"
APP_VERSION = "1.0.0"
USER_SETTINGS_FILENAME = "settings.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_local_appdata_dir() -> Path:
    if os.name == "nt":
        raw = os.getenv("LOCALAPPDATA")
        if raw:
            return Path(raw)
        return Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    else:
        return Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def _resolve_project_root() -> Path:
    return PROJECT_ROOT


def _resolve_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def _resolve_resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return PROJECT_ROOT


def _resolve_config_dir() -> Path:
    return _resolve_local_appdata_dir() / APP_NAME


def _resolve_default_data_root() -> Path:
    if os.name == "nt":
        preferred = Path("D:/") / f"{APP_NAME}Data"
        if preferred.drive and preferred.drive.upper() == "D:" and Path("D:/").exists():
            return preferred
    return _resolve_config_dir()


def _ensure_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_data_root(value: str | os.PathLike[str] | None, *, fallback: Path) -> Path:
    candidates: list[Path] = []
    if value:
        candidates.append(Path(value))
    candidates.append(_resolve_default_data_root())
    candidates.append(fallback)

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if _ensure_writable_directory(resolved):
            return resolved
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _derive_encryption_key() -> bytes:
    machine_id = f"{platform.node()}-{platform.machine()}-{platform.processor()}"
    digest = hashlib.sha256(machine_id.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_value(plaintext: str) -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return plaintext
    key = _derive_encryption_key()
    f = Fernet(key)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return ciphertext
    key = _derive_encryption_key()
    f = Fernet(key)
    return f.decrypt(ciphertext.encode()).decode()


def _load_user_settings(path: Path | None = None) -> dict[str, Any]:
    config_path = path or (_resolve_config_dir() / USER_SETTINGS_FILENAME)
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_user_settings(
    *,
    deepseek_api_key: str | None = None,
    data_root: str | None = None,
    path: Path | None = None,
) -> Path:
    config_path = path or (_resolve_config_dir() / USER_SETTINGS_FILENAME)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _load_user_settings(config_path)
    payload["version"] = 1
    if deepseek_api_key is not None:
        payload["deepseek_api_key_encrypted"] = encrypt_value(deepseek_api_key.strip())
        payload.pop("deepseek_api_key", None)
    if data_root is not None:
        payload["data_root"] = data_root.strip()
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def _first_existing_path(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _resolve_tool_command(explicit: str | None, *, executable_name: str, search_roots: list[Path]) -> str:
    if explicit:
        return explicit
    import shutil as _shutil
    base_name = Path(executable_name).stem
    candidates: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for tool_dir in [".tools", "tools"]:
            for pattern in [executable_name, base_name, f"{base_name}*"]:
                candidates.extend(sorted(root.glob(f"{tool_dir}/**/{pattern}")))
    found = _shutil.which(base_name)
    if found:
        return found
    found_path = _first_existing_path(candidates)
    return str(found_path) if found_path else executable_name


@dataclass(frozen=True)
class Settings:
    project_root: Path = field(default_factory=_resolve_project_root)
    app_root: Path = field(default_factory=_resolve_project_root)
    resource_root: Path = field(default_factory=_resolve_project_root)
    config_dir: Path = field(default_factory=_resolve_project_root)
    config_path: Path = field(default_factory=lambda: _resolve_project_root() / ".app-settings.json")
    data_root: Path = field(default_factory=_resolve_project_root)
    workspace_dir: Path = field(default_factory=lambda: _resolve_project_root() / "workspace")
    jobs_dir: Path = field(default_factory=lambda: _resolve_project_root() / "workspace" / "jobs")
    legacy_jobs_dirs: tuple[Path, ...] = ()
    cache_dir: Path = field(default_factory=lambda: _resolve_project_root() / ".cache")
    temp_dir: Path = field(default_factory=lambda: _resolve_project_root() / ".tmp")
    logs_dir: Path = field(default_factory=lambda: _resolve_project_root() / "logs")
    hf_home: Path = field(default_factory=lambda: _resolve_project_root() / ".cache" / "huggingface")
    xdg_cache_home: Path = field(default_factory=lambda: _resolve_project_root() / ".cache")
    deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    whisper_model: str = os.getenv("WHISPER_MODEL", "distil-large-v3")
    whisper_fallback_model: str = os.getenv("WHISPER_FALLBACK_MODEL", "medium")
    ffmpeg_command: str = os.getenv("FFMPEG_COMMAND", "ffmpeg")
    mpv_command: str = os.getenv("MPV_COMMAND", "mpv")
    yt_format: str = os.getenv(
        "YT_FORMAT",
        "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best[height<=1080]/best",
    )
    translation_style_preset: str = os.getenv("TRANSLATION_STYLE_PRESET", "default")
    translation_glossary_json: str = os.getenv("TRANSLATION_GLOSSARY_JSON", "")
    translation_protected_terms_json: str = os.getenv("TRANSLATION_PROTECTED_TERMS_JSON", "")
    translation_batch_size: int = 20
    translation_max_chars: int = 2800
    translation_parallel_workers: int = 3
    update_feed_url: str = os.getenv("YTSUBVIEWER_UPDATE_FEED_URL", "")
    prefer_automatic_subtitles: bool = os.getenv("PREFER_AUTOMATIC_SUBTITLES", "1").strip().lower() not in {"0", "false", "no"}
    target_line_width: int = 20
    max_subtitle_lines: int = 2
    max_concurrent_tasks: int = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
    target_language: str = os.getenv("TARGET_LANGUAGE", "zh-CN")

    @classmethod
    def load(cls, overrides: dict[str, Any] | None = None) -> "Settings":
        overrides = dict(overrides or {})
        project_root = Path(overrides.pop("project_root", PROJECT_ROOT))
        app_root = Path(overrides.pop("app_root", _resolve_app_root()))
        resource_root = Path(overrides.pop("resource_root", _resolve_resource_root()))
        config_dir = Path(overrides.pop("config_dir", _resolve_config_dir()))
        config_path = Path(overrides.pop("config_path", config_dir / USER_SETTINGS_FILENAME))
        user_settings = _load_user_settings(config_path)
        data_root_value = (
            overrides.pop("data_root", None)
            or os.getenv("YTSUBVIEWER_DATA_ROOT")
            or user_settings.get("data_root")
        )
        data_root = _resolve_data_root(data_root_value, fallback=config_dir)
        workspace_dir = Path(overrides.pop("workspace_dir", data_root / "workspace"))
        jobs_dir = Path(overrides.pop("jobs_dir", workspace_dir / "jobs"))
        legacy_jobs_dirs = tuple(
            path
            for path in [
                project_root / "workspace" / "jobs",
                app_root.parent / "workspace" / "jobs",
                app_root.parent.parent / "workspace" / "jobs",
            ]
            if path != jobs_dir and path.exists()
        )
        cache_dir = Path(overrides.pop("cache_dir", data_root / ".cache"))
        temp_dir = Path(overrides.pop("temp_dir", data_root / ".tmp"))
        logs_dir = Path(overrides.pop("logs_dir", data_root / "logs"))
        hf_home = Path(overrides.pop("hf_home", cache_dir / "huggingface"))
        xdg_cache_home = Path(overrides.pop("xdg_cache_home", cache_dir))

        explicit_ffmpeg = overrides.pop("ffmpeg_command", None) or os.getenv("FFMPEG_COMMAND")
        explicit_mpv = overrides.pop("mpv_command", None) or os.getenv("MPV_COMMAND")
        search_roots = [resource_root, app_root, project_root]
        ffmpeg_command = _resolve_tool_command(explicit_ffmpeg, executable_name="ffmpeg", search_roots=search_roots)
        mpv_command = _resolve_tool_command(explicit_mpv, executable_name="mpv", search_roots=search_roots)

        return cls(
            project_root=project_root,
            app_root=app_root,
            resource_root=resource_root,
            config_dir=config_dir,
            config_path=config_path,
            data_root=data_root,
            workspace_dir=workspace_dir,
            jobs_dir=jobs_dir,
            legacy_jobs_dirs=legacy_jobs_dirs,
            cache_dir=cache_dir,
            temp_dir=temp_dir,
            logs_dir=logs_dir,
            hf_home=hf_home,
            xdg_cache_home=xdg_cache_home,
            deepseek_api_key=(
                overrides.pop("deepseek_api_key", None)
                or os.getenv("DEEPSEEK_API_KEY")
                or decrypt_value(user_settings.get("deepseek_api_key_encrypted", ""))
                or user_settings.get("deepseek_api_key")
                or None
            ),
            deepseek_base_url=overrides.pop("deepseek_base_url", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
            deepseek_model=overrides.pop("deepseek_model", os.getenv("DEEPSEEK_MODEL", "deepseek-chat")),
            whisper_model=overrides.pop("whisper_model", os.getenv("WHISPER_MODEL", "distil-large-v3")),
            whisper_fallback_model=overrides.pop("whisper_fallback_model", os.getenv("WHISPER_FALLBACK_MODEL", "medium")),
            ffmpeg_command=ffmpeg_command,
            mpv_command=mpv_command,
            yt_format=overrides.pop(
                "yt_format",
                os.getenv(
                    "YT_FORMAT",
                    "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best[height<=1080]/best",
                ),
            ),
            translation_style_preset=overrides.pop("translation_style_preset", os.getenv("TRANSLATION_STYLE_PRESET", "default")),
            translation_glossary_json=overrides.pop("translation_glossary_json", os.getenv("TRANSLATION_GLOSSARY_JSON", "")),
            translation_protected_terms_json=overrides.pop(
                "translation_protected_terms_json",
                os.getenv("TRANSLATION_PROTECTED_TERMS_JSON", ""),
            ),
            translation_batch_size=int(overrides.pop("translation_batch_size", 20)),
            translation_max_chars=int(overrides.pop("translation_max_chars", 2800)),
            translation_parallel_workers=int(overrides.pop("translation_parallel_workers", 3)),
            update_feed_url=overrides.pop("update_feed_url", os.getenv("YTSUBVIEWER_UPDATE_FEED_URL", "")),
            prefer_automatic_subtitles=(
                str(overrides.pop("prefer_automatic_subtitles", os.getenv("PREFER_AUTOMATIC_SUBTITLES", "1"))).strip().lower()
                not in {"0", "false", "no"}
            ),
            target_line_width=int(overrides.pop("target_line_width", 20)),
            max_subtitle_lines=int(overrides.pop("max_subtitle_lines", 2)),
            max_concurrent_tasks=int(overrides.pop("max_concurrent_tasks", os.getenv("MAX_CONCURRENT_TASKS", "3"))),
            target_language=str(overrides.pop("target_language", os.getenv("TARGET_LANGUAGE", "zh-CN"))),
        )

    def ensure_directories(self) -> None:
        for path in [
            self.config_dir,
            self.data_root,
            self.workspace_dir,
            self.jobs_dir,
            self.cache_dir,
            self.temp_dir,
            self.logs_dir,
            self.hf_home,
            self.xdg_cache_home,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def translation_controls(self) -> TranslationControlConfig:
        return TranslationControlConfig(
            style_preset=self.translation_style_preset.strip() or "default",
            glossary=self._parse_glossary_entries(self.translation_glossary_json),
            protected_terms=self._parse_protected_terms(self.translation_protected_terms_json),
        )

    @staticmethod
    def _parse_glossary_entries(raw: str) -> tuple[TranslationGlossaryEntry, ...]:
        items = Settings._parse_json_or_list(raw)
        entries: list[TranslationGlossaryEntry] = []
        for item in items:
            source = ""
            target = ""
            note = ""
            if isinstance(item, dict):
                source = str(item.get("source") or item.get("term") or item.get("from") or "").strip()
                target = str(item.get("target") or item.get("translation") or item.get("to") or "").strip()
                note = str(item.get("note") or item.get("description") or "").strip()
            elif isinstance(item, (list, tuple)):
                values = [str(part).strip() for part in item]
                if len(values) >= 2:
                    source, target = values[0], values[1]
                    note = values[2] if len(values) >= 3 else ""
            else:
                text = str(item).strip()
                if "->" in text:
                    source, target = [part.strip() for part in text.split("->", 1)]
                elif ":" in text:
                    source, target = [part.strip() for part in text.split(":", 1)]
            if source and target:
                entries.append(TranslationGlossaryEntry(source=source, target=target, note=note))
        return tuple(entries)

    @staticmethod
    def _parse_protected_terms(raw: str) -> tuple[str, ...]:
        items = Settings._parse_json_or_list(raw)
        terms: list[str] = []
        for item in items:
            text = ""
            if isinstance(item, dict):
                text = str(item.get("term") or item.get("value") or item.get("text") or "").strip()
            else:
                text = str(item).strip()
            if text:
                terms.append(text)
        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return tuple(deduped)

    @staticmethod
    def _parse_json_or_list(raw: str) -> list[object]:
        text = raw.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = None
            else:
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    return list(value.values())
        parts = [part.strip() for part in text.replace("\r", "\n").replace(";", "\n").split("\n")]
        if len(parts) == 1 and "," in text:
            parts = [part.strip() for part in text.split(",")]
        return [part for part in parts if part]


settings = Settings.load()
