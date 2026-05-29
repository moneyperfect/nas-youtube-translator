from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ytsubviewer.models import JobArtifacts, SubtitleCue


STATE_FILENAME = "job_state.json"


def load_job_state(work_dir: Path) -> dict[str, Any] | None:
    path = work_dir / STATE_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_job_state(work_dir: Path, payload: dict[str, Any]) -> Path:
    path = work_dir / STATE_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def serialize_cues(cues: list[SubtitleCue]) -> list[dict[str, Any]]:
    return [cue.to_dict() for cue in cues]


def deserialize_cues(items: list[dict[str, Any]] | None) -> list[SubtitleCue]:
    if not items:
        return []
    return [SubtitleCue.from_dict(item) for item in items]


def artifacts_from_state(state: dict[str, Any]) -> JobArtifacts | None:
    video_path = state.get("video_path")
    chinese_subtitle_path = state.get("chinese_subtitle_path")
    if not video_path or not chinese_subtitle_path:
        return None
    artifacts = JobArtifacts(
        video_id=state.get("video_id", ""),
        title=state.get("title", ""),
        source_kind=state.get("source_kind", ""),
        video_path=Path(video_path),
        english_subtitle_path=Path(state["english_subtitle_path"]) if state.get("english_subtitle_path") else None,
        chinese_subtitle_path=Path(chinese_subtitle_path),
        chinese_ass_path=Path(state["chinese_ass_path"]) if state.get("chinese_ass_path") else None,
        bilingual_ass_path=Path(state["bilingual_ass_path"]) if state.get("bilingual_ass_path") else None,
        burned_chinese_video_path=Path(state["burned_chinese_video_path"]) if state.get("burned_chinese_video_path") else None,
        burned_bilingual_video_path=Path(state["burned_bilingual_video_path"]) if state.get("burned_bilingual_video_path") else None,
        quality_report_path=Path(state["quality_report_path"]) if state.get("quality_report_path") else None,
        duration_seconds=int(state["duration_seconds"]) if state.get("duration_seconds") else None,
        work_dir=Path(state.get("work_dir", ".")),
    )
    if not artifacts.video_path.exists() or not artifacts.chinese_subtitle_path.exists():
        return None
    if artifacts.chinese_ass_path and not artifacts.chinese_ass_path.exists():
        artifacts.chinese_ass_path = None
    if artifacts.bilingual_ass_path and not artifacts.bilingual_ass_path.exists():
        artifacts.bilingual_ass_path = None
    if artifacts.burned_chinese_video_path and not artifacts.burned_chinese_video_path.exists():
        artifacts.burned_chinese_video_path = None
    if artifacts.burned_bilingual_video_path and not artifacts.burned_bilingual_video_path.exists():
        artifacts.burned_bilingual_video_path = None
    if artifacts.quality_report_path and not artifacts.quality_report_path.exists():
        artifacts.quality_report_path = None
    return artifacts
