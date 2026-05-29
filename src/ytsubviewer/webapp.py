from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ytsubviewer.auth import AuthMiddleware, generate_session_token
from ytsubviewer.background_jobs import (
    BackgroundGenerationManager,
    GenerationJobSnapshot,
    TaskSnapshot,
)
from ytsubviewer.config import APP_VERSION, Settings, save_user_settings, settings as app_settings
from ytsubviewer.creator_profiles import CreatorProfileStore
from ytsubviewer.i18n import load_translations
from ytsubviewer.job_state import artifacts_from_state, load_job_state
from ytsubviewer.license import LicenseManager
from ytsubviewer.models import JobArtifacts, TranslationControlConfig
from ytsubviewer.pipeline import SubtitlePipeline
from ytsubviewer.rate_limit import RateLimitMiddleware
from ytsubviewer.runtime import inspect_environment
from ytsubviewer.services.translate import DeepSeekTranslator
from ytsubviewer.update_service import UpdateService
from ytsubviewer.utils import format_duration, format_eta


YOUTUBE_URL_RE = re.compile(
    r"^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+"
)


def _validate_youtube_url(url: str) -> None:
    if not YOUTUBE_URL_RE.match(url):
        raise HTTPException(status_code=400, detail=f"无效的 YouTube 链接：{url}")


class SettingsPayload(BaseModel):
    api_key: str = ""


class AnalyzePayload(BaseModel):
    url: str
    style_preset: str = "default"
    glossary_text: str = ""
    protected_terms_text: str = ""
    performance_mode: str = "balanced"
    use_creator_defaults: bool = True


class BatchPayload(BaseModel):
    urls_text: str
    style_preset: str = "default"
    glossary_text: str = ""
    protected_terms_text: str = ""
    performance_mode: str = "balanced"
    use_creator_defaults: bool = True


class OpenPlayerPayload(BaseModel):
    work_dir: str
    bilingual: bool = False


class ExportPayload(BaseModel):
    work_dir: str
    bilingual: bool = False
    preview: bool = False
    performance_mode: str = "balanced"


class CueUpdatePayload(BaseModel):
    cue_id: int
    target_text: str = ""


class CueLockPayload(BaseModel):
    cue_id: int
    locked: bool = True


class BulkReplacePayload(BaseModel):
    source_text: str
    target_text: str


class CreatorProfilePayload(BaseModel):
    url: str
    style_preset: str = "default"
    glossary_text: str = ""
    protected_terms_text: str = ""


class LicensePayload(BaseModel):
    license_key: str = ""


def create_web_app(
    *,
    app_runtime_settings: Settings | None = None,
    pipeline: SubtitlePipeline | None = None,
    generation_manager: BackgroundGenerationManager | None = None,
    mount_legacy: bool = True,
) -> FastAPI:
    runtime_settings = app_runtime_settings or app_settings
    runtime_pipeline = pipeline or SubtitlePipeline(runtime_settings)
    runtime_generation_manager = generation_manager or BackgroundGenerationManager(runtime_settings, runtime_pipeline)
    runtime_generation_manager.bind_pipeline(runtime_pipeline)

    session_token = generate_session_token()

    runtime: dict[str, Any] = {
        "settings": runtime_settings,
        "pipeline": runtime_pipeline,
        "generation_manager": runtime_generation_manager,
        "creator_profiles": CreatorProfileStore(runtime_settings),
        "license_manager": LicenseManager(runtime_settings),
        "update_service": UpdateService(runtime_settings),
        "session_token": session_token,
    }

    web_root = _resolve_web_root(runtime_settings)
    app = FastAPI(title="YTSubViewer", docs_url=None, redoc_url=None)
    app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)
    app.add_middleware(AuthMiddleware, token=session_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=web_root), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": APP_VERSION}

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        current_settings = runtime["settings"]
        return {
            "version": APP_VERSION,
            "settings": _serialize_settings(current_settings),
            "environment": inspect_environment(current_settings),
            "style_presets": _serialize_style_presets(),
            "performance_modes": _serialize_performance_modes(),
            "job": _serialize_current_job(runtime),
            "history": _serialize_history(runtime),
            "license": runtime["license_manager"].status(),
            "update": runtime["update_service"].status(),
            "creator_profiles": _serialize_creator_profiles(runtime),
            "session_token": runtime["session_token"],
            "available_languages": [
                {"code": "zh", "label": "中文"},
                {"code": "en", "label": "English"},
            ],
        }

    @app.post("/api/settings")
    def save_settings(payload: SettingsPayload) -> dict[str, Any]:
        save_user_settings(deepseek_api_key=payload.api_key)
        _reload_runtime(runtime)
        current_settings = runtime["settings"]
        return {
            "settings": _serialize_settings(current_settings),
            "environment": inspect_environment(current_settings),
            "style_presets": _serialize_style_presets(),
            "performance_modes": _serialize_performance_modes(),
            "job": _serialize_current_job(runtime),
            "history": _serialize_history(runtime),
            "license": runtime["license_manager"].status(),
            "update": runtime["update_service"].status(),
            "creator_profiles": _serialize_creator_profiles(runtime),
        }

    @app.post("/api/analyze")
    def analyze(payload: AnalyzePayload) -> dict[str, Any]:
        url = payload.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="请输入 YouTube 链接。")
        _validate_youtube_url(url)

        current_pipeline: SubtitlePipeline = runtime["pipeline"]
        metadata = current_pipeline.analyze(url)
        profile = runtime["creator_profiles"].get_for_metadata(metadata)
        resolved = _resolve_control_texts(payload, profile)
        controls = _build_controls(
            resolved["style_preset"],
            resolved["glossary_text"],
            resolved["protected_terms_text"],
        )
        existing_artifacts = current_pipeline.find_existing_artifacts(metadata)
        state = _state_from_artifacts(current_pipeline, existing_artifacts, controls) if existing_artifacts else None
        controls_match = bool(state and current_pipeline._controls_match_state(state, controls))

        return {
            "metadata": _serialize_metadata(metadata),
            "strategy_text": _strategy_text(
                metadata.manual_english_subtitle_lang,
                metadata.automatic_english_subtitle_lang,
            ),
            "profile": _serialize_profile(profile),
            "resolved_controls": resolved,
            "has_existing_result": state is not None,
            "controls_match": controls_match,
            "state": _serialize_state(current_pipeline, state) if state else None,
        }

    @app.post("/api/generate")
    def generate(payload: AnalyzePayload) -> dict[str, Any]:
        url = payload.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="请输入 YouTube 链接。")
        _validate_youtube_url(url)
        if not runtime["settings"].deepseek_api_key:
            raise HTTPException(status_code=400, detail="请先保存 DeepSeek API Key。")

        current_pipeline: SubtitlePipeline = runtime["pipeline"]
        metadata = current_pipeline.analyze(url)
        profile = runtime["creator_profiles"].get_for_metadata(metadata)
        resolved = _resolve_control_texts(payload, profile)
        controls = _build_controls(
            resolved["style_preset"],
            resolved["glossary_text"],
            resolved["protected_terms_text"],
        )
        snapshot = runtime["generation_manager"].start_generation(
            url=url,
            metadata=metadata,
            strategy_text=_strategy_text(
                metadata.manual_english_subtitle_lang,
                metadata.automatic_english_subtitle_lang,
            ),
            controls=controls,
            glossary_text=resolved["glossary_text"],
            protected_terms_text=resolved["protected_terms_text"],
            performance_mode=payload.performance_mode,
        )
        return {"job": _serialize_job(runtime, snapshot)}

    @app.post("/api/batch")
    def batch_generate(payload: BatchPayload) -> dict[str, Any]:
        urls = [line.strip() for line in payload.urls_text.replace("\r", "\n").split("\n") if line.strip()]
        if not urls:
            raise HTTPException(status_code=400, detail="请至少输入一个 YouTube 链接。")
        for url in urls:
            _validate_youtube_url(url)
        queued: list[dict[str, Any]] = []
        batch_id = Path(os.urandom(8).hex()).name
        for url in urls:
            metadata = runtime["pipeline"].analyze(url)
            profile = runtime["creator_profiles"].get_for_metadata(metadata)
            resolved = _resolve_control_texts(payload, profile)
            controls = _build_controls(
                resolved["style_preset"],
                resolved["glossary_text"],
                resolved["protected_terms_text"],
            )
            snapshot = runtime["generation_manager"].start_generation(
                url=url,
                metadata=metadata,
                strategy_text=_strategy_text(
                    metadata.manual_english_subtitle_lang,
                    metadata.automatic_english_subtitle_lang,
                ),
                controls=controls,
                glossary_text=resolved["glossary_text"],
                protected_terms_text=resolved["protected_terms_text"],
                performance_mode=payload.performance_mode,
                batch_id=batch_id,
            )
            queued.append(_serialize_job(runtime, snapshot))
        return {"jobs": queued, "history": _serialize_history(runtime)}

    @app.get("/api/job/current")
    def current_job() -> dict[str, Any]:
        return {"job": _serialize_current_job(runtime)}

    @app.get("/api/job/history")
    def job_history() -> dict[str, Any]:
        return {"jobs": _serialize_history(runtime)}

    @app.get("/api/job/{task_id}")
    def job_detail(task_id: str) -> dict[str, Any]:
        snapshot = runtime["generation_manager"].get_task(task_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="任务不存在。")
        return {"job": _serialize_job(runtime, snapshot)}

    @app.post("/api/job/{task_id}/cancel")
    def cancel_job(task_id: str) -> dict[str, Any]:
        snapshot = runtime["generation_manager"].cancel_task(task_id)
        return {"job": _serialize_job(runtime, snapshot), "history": _serialize_history(runtime)}

    @app.post("/api/job/{task_id}/retry")
    def retry_job(task_id: str) -> dict[str, Any]:
        snapshot = runtime["generation_manager"].retry_task(task_id)
        return {"job": _serialize_job(runtime, snapshot), "history": _serialize_history(runtime)}

    @app.post("/api/export")
    def export_video(payload: ExportPayload) -> dict[str, Any]:
        state = _load_state_for_work_dir(Path(payload.work_dir))
        snapshot = runtime["generation_manager"].start_export(
            state=state,
            bilingual=payload.bilingual,
            preview=payload.preview,
            performance_mode=payload.performance_mode,
        )
        return {"job": _serialize_job(runtime, snapshot)}

    @app.get("/api/export/{task_id}")
    def export_detail(task_id: str) -> dict[str, Any]:
        snapshot = runtime["generation_manager"].get_task(task_id)
        if snapshot is None or snapshot.kind != "export":
            raise HTTPException(status_code=404, detail="导出任务不存在。")
        return {"job": _serialize_job(runtime, snapshot)}

    @app.get("/api/job/{task_id}/quality")
    def quality_report(task_id: str) -> dict[str, Any]:
        state = _load_state_for_task(runtime, task_id)
        quality = dict(state.get("quality_report") or {})
        return {
            "quality_report": quality,
            "quality_report_path": str(state.get("quality_report_path", "") or ""),
        }

    @app.get("/api/job/{task_id}/editor")
    def editor_document(task_id: str, issues_only: bool = False, query: str = "") -> dict[str, Any]:
        state = _load_state_for_task(runtime, task_id)
        payload = runtime["pipeline"].get_editor_payload(state, issues_only=issues_only, query=query)
        return payload

    @app.post("/api/job/{task_id}/cue/update")
    def update_cue(task_id: str, payload: CueUpdatePayload) -> dict[str, Any]:
        state = _load_state_for_task(runtime, task_id)
        artifacts = runtime["pipeline"].update_cue_translation(state, payload.cue_id, payload.target_text)
        return {
            "state": _serialize_state(runtime["pipeline"], load_job_state(artifacts.work_dir)),
            "editor": runtime["pipeline"].get_editor_payload(load_job_state(artifacts.work_dir) or {}),
        }

    @app.post("/api/job/{task_id}/cue/retranslate")
    def retranslate_cue(task_id: str, payload: CueUpdatePayload) -> dict[str, Any]:
        state = _load_state_for_task(runtime, task_id)
        artifacts = runtime["pipeline"].retranslate_cue(state, payload.cue_id)
        return {
            "state": _serialize_state(runtime["pipeline"], load_job_state(artifacts.work_dir)),
            "editor": runtime["pipeline"].get_editor_payload(load_job_state(artifacts.work_dir) or {}),
        }

    @app.post("/api/job/{task_id}/cue/lock")
    def lock_cue(task_id: str, payload: CueLockPayload) -> dict[str, Any]:
        state = _load_state_for_task(runtime, task_id)
        artifacts = runtime["pipeline"].set_cue_lock(state, payload.cue_id, payload.locked)
        return {
            "state": _serialize_state(runtime["pipeline"], load_job_state(artifacts.work_dir)),
            "editor": runtime["pipeline"].get_editor_payload(load_job_state(artifacts.work_dir) or {}),
        }

    @app.post("/api/job/{task_id}/cue/bulk-replace")
    def bulk_replace(task_id: str, payload: BulkReplacePayload) -> dict[str, Any]:
        state = _load_state_for_task(runtime, task_id)
        artifacts = runtime["pipeline"].bulk_replace_term(state, payload.source_text, payload.target_text)
        refreshed = load_job_state(artifacts.work_dir) or state
        return {
            "state": _serialize_state(runtime["pipeline"], refreshed),
            "editor": runtime["pipeline"].get_editor_payload(refreshed),
        }

    @app.post("/api/open-player")
    def open_player(payload: OpenPlayerPayload) -> dict[str, Any]:
        work_dir = Path(payload.work_dir.strip())
        state = load_job_state(work_dir)
        if not state:
            raise HTTPException(status_code=404, detail="当前任务结果不存在，请先生成字幕。")
        artifacts = artifacts_from_state(state)
        if artifacts is None:
            raise HTTPException(status_code=400, detail="当前任务还没有可播放的结果。")

        current_pipeline: SubtitlePipeline = runtime["pipeline"]
        video_path, subtitle_path = current_pipeline.prepare_player_paths(artifacts, bilingual=payload.bilingual)
        message = current_pipeline.player.open_with_subtitle(video_path, subtitle_path)
        return {"message": message}

    @app.post("/api/creator-profile/save")
    def save_creator_profile(payload: CreatorProfilePayload) -> dict[str, Any]:
        url = payload.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="请输入 YouTube 链接。")
        metadata = runtime["pipeline"].analyze(url)
        profile = runtime["creator_profiles"].save_for_metadata(
            metadata,
            style_preset=payload.style_preset,
            glossary_text=payload.glossary_text,
            protected_terms_text=payload.protected_terms_text,
        )
        return {"profile": _serialize_profile(profile), "profiles": _serialize_creator_profiles(runtime)}

    @app.get("/api/license/status")
    def license_status() -> dict[str, Any]:
        return runtime["license_manager"].status()

    @app.post("/api/license/activate")
    def activate_license(payload: LicensePayload) -> dict[str, Any]:
        return runtime["license_manager"].activate(payload.license_key)

    @app.post("/api/license/deactivate")
    def deactivate_license() -> dict[str, Any]:
        return runtime["license_manager"].deactivate()

    @app.get("/api/file")
    def download_file(path: str) -> FileResponse:
        resolved = _resolve_download_path(runtime["settings"], path)
        return FileResponse(resolved, filename=resolved.name)

    if mount_legacy:
        try:
            import gradio as gr

            from ytsubviewer.ui import create_app as create_legacy_app

            app = gr.mount_gradio_app(app, create_legacy_app(), path="/legacy")
        except Exception:
            pass

    return app


def _resolve_web_root(current_settings: Settings) -> Path:
    candidates = [
        current_settings.resource_root / "src" / "ytsubviewer" / "web",
        Path(__file__).resolve().parent / "web",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("Web frontend assets are missing.")


def _serialize_style_presets() -> list[dict[str, str]]:
    presets = DeepSeekTranslator.available_style_presets()
    return [
        {
            "name": preset.name,
            "label": preset.label,
            "description": preset.description,
        }
        for preset in presets.values()
    ]


def _serialize_performance_modes() -> list[dict[str, str]]:
    return [
        {
            "name": "fast",
            "label": "Fast",
            "description": "优先速度，适合快速预览和短视频批量处理。",
        },
        {
            "name": "balanced",
            "label": "Balanced",
            "description": "默认模式，平衡速度、稳定性和导出质量。",
        },
        {
            "name": "quality",
            "label": "Quality",
            "description": "优先质量，适合正式交付前的最终成品。",
        },
    ]


def _serialize_settings(current_settings: Settings) -> dict[str, Any]:
    return {
        "api_key_ready": bool(current_settings.deepseek_api_key),
        "data_root": str(current_settings.data_root),
        "config_path": str(current_settings.config_path),
        "prefer_automatic_subtitles": current_settings.prefer_automatic_subtitles,
        "update_feed_url": current_settings.update_feed_url,
        "max_concurrent_tasks": current_settings.max_concurrent_tasks,
        "target_language": current_settings.target_language,
    }


def _serialize_current_job(runtime: dict[str, Any]) -> dict[str, Any] | None:
    active = runtime["generation_manager"].get_active_task()
    if active is not None:
        return _serialize_job(runtime, active)
    snapshot = runtime["generation_manager"].get_current_snapshot()
    if snapshot is None:
        return None
    return _serialize_job(runtime, snapshot)


def _serialize_history(runtime: dict[str, Any], *, limit: int = 30) -> list[dict[str, Any]]:
    tasks = runtime["generation_manager"].list_tasks(limit=limit)
    return [_serialize_job(runtime, task) for task in tasks]


def _serialize_job(runtime: dict[str, Any], snapshot: GenerationJobSnapshot | TaskSnapshot) -> dict[str, Any]:
    current_pipeline: SubtitlePipeline = runtime["pipeline"]
    state = _state_from_snapshot(snapshot)
    return {
        "job_id": snapshot.task_id,
        "kind": snapshot.kind,
        "status": snapshot.status,
        "stage": snapshot.stage,
        "progress": snapshot.progress,
        "progress_percent": max(1, round(snapshot.progress * 100)) if snapshot.status in {"running", "pending"} else 100,
        "title": snapshot.title,
        "duration_seconds": snapshot.duration_seconds,
        "duration_text": format_duration(snapshot.duration_seconds),
        "strategy_text": snapshot.strategy_text,
        "thumbnail_url": snapshot.thumbnail_url,
        "work_dir": snapshot.work_dir,
        "logs": list(snapshot.log_lines),
        "error": snapshot.error,
        "performance_mode": snapshot.performance_mode,
        "bilingual": snapshot.bilingual,
        "preview": snapshot.preview,
        "current_step": snapshot.current_step,
        "total_steps": snapshot.total_steps,
        "completed_items": snapshot.completed_items,
        "total_items": snapshot.total_items,
        "eta_text": format_eta(snapshot.eta_seconds),
        "can_retry": snapshot.status in {"failed", "completed", "cancelled"},
        "can_cancel": snapshot.status in {"pending", "running"},
        "state": _serialize_state(current_pipeline, state) if state else None,
    }


def _state_from_snapshot(snapshot: GenerationJobSnapshot | TaskSnapshot) -> dict[str, Any] | None:
    if not snapshot.work_dir:
        return None
    return load_job_state(Path(snapshot.work_dir))


def _state_from_artifacts(
    current_pipeline: SubtitlePipeline,
    artifacts: JobArtifacts | None,
    controls: TranslationControlConfig,
) -> dict[str, Any] | None:
    if artifacts is None:
        return None
    artifacts = current_pipeline.ensure_subtitle_artifacts(artifacts)
    artifacts = current_pipeline.ensure_quality_report(artifacts)
    state = load_job_state(artifacts.work_dir) or artifacts.to_state()
    state["translation_controls"] = state.get("translation_controls") or controls.to_dict()
    return state


def _serialize_state(current_pipeline: SubtitlePipeline, state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    artifacts = artifacts_from_state(state)
    if artifacts is not None:
        artifacts = current_pipeline.ensure_subtitle_artifacts(artifacts)
        artifacts = current_pipeline.ensure_quality_report(artifacts)
        state = load_job_state(artifacts.work_dir) or state

    return {
        "status": str(state.get("status", "")).strip() or "idle",
        "video_id": state.get("video_id", ""),
        "title": state.get("title", ""),
        "duration_seconds": int(state["duration_seconds"]) if state.get("duration_seconds") else None,
        "duration_text": format_duration(int(state["duration_seconds"])) if state.get("duration_seconds") else "",
        "source_kind": state.get("source_kind", ""),
        "work_dir": state.get("work_dir", ""),
        "downloads": _serialize_downloads(state),
        "quality_report_path": state.get("quality_report_path", ""),
        "quality_report": dict(state.get("quality_report") or {}),
    }


def _serialize_downloads(state: dict[str, Any]) -> dict[str, dict[str, str]]:
    files = {
        "video": state.get("video_path", ""),
        "subtitle": state.get("chinese_subtitle_path", ""),
        "chinese_ass": state.get("chinese_ass_path", ""),
        "bilingual_ass": state.get("bilingual_ass_path", ""),
        "quality_report": state.get("quality_report_path", ""),
        "burned_chinese_video": state.get("burned_chinese_video_path", ""),
        "burned_bilingual_video": state.get("burned_bilingual_video_path", ""),
    }
    payload: dict[str, dict[str, str]] = {}
    for key, raw_path in files.items():
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        path = Path(path_text)
        if not path.exists():
            continue
        payload[key] = {
            "path": str(path),
            "name": path.name,
            "url": f"/api/file?path={quote(str(path))}",
        }
    return payload


def _reload_runtime(runtime: dict[str, Any]) -> None:
    current_settings = Settings.load()
    runtime["settings"] = current_settings
    runtime["pipeline"] = SubtitlePipeline(current_settings)
    generation_manager: BackgroundGenerationManager = runtime["generation_manager"]
    generation_manager.settings = current_settings
    generation_manager.runtime_dir = current_settings.data_root / ".runtime"
    generation_manager.runtime_dir.mkdir(parents=True, exist_ok=True)
    generation_manager.tasks_dir = generation_manager.runtime_dir / "tasks"
    generation_manager.tasks_dir.mkdir(parents=True, exist_ok=True)
    generation_manager.current_snapshot_path = generation_manager.runtime_dir / "current_generation_job.json"
    generation_manager.bind_pipeline(runtime["pipeline"])
    runtime["creator_profiles"] = CreatorProfileStore(current_settings)
    runtime["license_manager"] = LicenseManager(current_settings)
    runtime["update_service"] = UpdateService(current_settings)


def _build_controls(style_preset: str, glossary_text: str, protected_terms_text: str) -> TranslationControlConfig:
    temp_settings = Settings(
        translation_style_preset=(style_preset or "default").strip() or "default",
        translation_glossary_json=glossary_text.strip(),
        translation_protected_terms_json=protected_terms_text.strip(),
    )
    return temp_settings.translation_controls()


def _strategy_text(manual_lang: str | None, automatic_lang: str | None) -> str:
    if manual_lang:
        return f"优先使用人工英文字幕：{manual_lang}"
    if automatic_lang:
        return f"未检测到人工英文字幕，将优先使用 YouTube 自动英文字幕：{automatic_lang}"
    return "未检测到英文字幕，将回退到本地转写。"


def _resolve_download_path(current_settings: Settings, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在。")

    resolved = path.resolve()
    allowed_roots = [
        current_settings.data_root.resolve(),
        current_settings.project_root.resolve(),
        current_settings.config_dir.resolve(),
    ]
    if not any(_is_within_root(resolved, root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="不允许访问这个文件。")
    return resolved


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath([str(path), str(root)])
    except ValueError:
        return False
    return common.lower() == str(root).lower()


def _serialize_metadata(metadata) -> dict[str, Any]:
    return {
        "video_id": metadata.video_id,
        "title": metadata.title,
        "duration_seconds": metadata.duration_seconds,
        "duration_text": format_duration(metadata.duration_seconds),
        "thumbnail_url": metadata.thumbnail_url,
        "channel_id": metadata.channel_id,
        "channel_name": metadata.channel_name,
        "uploader": metadata.uploader,
    }


def _serialize_profile(profile) -> dict[str, Any] | None:
    if profile is None:
        return None
    return profile.to_dict()


def _serialize_creator_profiles(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in runtime["creator_profiles"].list_profiles()]


def _resolve_control_texts(payload: AnalyzePayload | BatchPayload, profile) -> dict[str, str]:
    style_preset = (payload.style_preset or "default").strip() or "default"
    glossary_text = payload.glossary_text
    protected_terms_text = payload.protected_terms_text
    if getattr(payload, "use_creator_defaults", True) and profile is not None:
        if style_preset == "default" and profile.style_preset:
            style_preset = profile.style_preset
        if not glossary_text.strip() and profile.glossary_text:
            glossary_text = profile.glossary_text
        if not protected_terms_text.strip() and profile.protected_terms_text:
            protected_terms_text = profile.protected_terms_text
    return {
        "style_preset": style_preset,
        "glossary_text": glossary_text,
        "protected_terms_text": protected_terms_text,
    }


def _load_state_for_work_dir(work_dir: Path) -> dict[str, Any]:
    state = load_job_state(work_dir)
    if not state:
        raise HTTPException(status_code=404, detail="当前任务缺少可用状态，请先生成字幕。")
    return state


def _load_state_for_task(runtime: dict[str, Any], task_id: str) -> dict[str, Any]:
    snapshot = runtime["generation_manager"].get_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if not snapshot.work_dir:
        raise HTTPException(status_code=400, detail="当前任务还没有可编辑结果。")
    state = load_job_state(Path(snapshot.work_dir))
    if not state:
        raise HTTPException(status_code=404, detail="当前任务缺少可编辑状态。")
    return state
