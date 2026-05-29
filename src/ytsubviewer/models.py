from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SubtitleCue:
    id: int
    start: float
    end: float
    source_text: str
    target_text: str = ""

    def clone(self, **changes: object) -> "SubtitleCue":
        payload = asdict(self)
        payload.update(changes)
        return SubtitleCue(**payload)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "SubtitleCue":
        return cls(**payload)


@dataclass
class VideoMetadata:
    video_id: str
    title: str
    duration_seconds: int | None
    thumbnail_url: str | None
    manual_english_subtitle_lang: str | None
    automatic_english_subtitle_lang: str | None
    channel_id: str | None = None
    channel_name: str | None = None
    uploader: str | None = None


@dataclass(frozen=True)
class TranslationGlossaryEntry:
    source: str
    target: str
    note: str = ""


@dataclass(frozen=True)
class TranslationStylePreset:
    name: str
    label: str
    description: str
    instructions: tuple[str, ...]
    temperature: float = 0.3


@dataclass(frozen=True)
class TranslationControlConfig:
    style_preset: str = "default"
    glossary: tuple[TranslationGlossaryEntry, ...] = ()
    protected_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "style_preset": self.style_preset,
            "glossary": [asdict(entry) for entry in self.glossary],
            "protected_terms": list(self.protected_terms),
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> "TranslationControlConfig":
        if not payload:
            return cls()
        glossary_payload = payload.get("glossary") or []
        glossary = tuple(
            TranslationGlossaryEntry(
                source=str(item.get("source", "")).strip(),
                target=str(item.get("target", "")).strip(),
                note=str(item.get("note", "")).strip(),
            )
            for item in glossary_payload
            if str(item.get("source", "")).strip() and str(item.get("target", "")).strip()
        )
        protected_terms = tuple(
            str(term).strip()
            for term in (payload.get("protected_terms") or [])
            if str(term).strip()
        )
        return cls(
            style_preset=str(payload.get("style_preset", "default")).strip() or "default",
            glossary=glossary,
            protected_terms=protected_terms,
        )


@dataclass
class JobArtifacts:
    video_id: str
    title: str
    source_kind: str
    video_path: Path | None
    english_subtitle_path: Path | None
    chinese_subtitle_path: Path | None
    work_dir: Path
    chinese_ass_path: Path | None = None
    bilingual_ass_path: Path | None = None
    burned_chinese_video_path: Path | None = None
    burned_bilingual_video_path: Path | None = None
    quality_report_path: Path | None = None
    duration_seconds: int | None = None

    def to_state(self) -> dict[str, str]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "source_kind": self.source_kind,
            "video_path": str(self.video_path) if self.video_path else "",
            "english_subtitle_path": str(self.english_subtitle_path) if self.english_subtitle_path else "",
            "chinese_subtitle_path": str(self.chinese_subtitle_path) if self.chinese_subtitle_path else "",
            "chinese_ass_path": str(self.chinese_ass_path) if self.chinese_ass_path else "",
            "bilingual_ass_path": str(self.bilingual_ass_path) if self.bilingual_ass_path else "",
            "burned_chinese_video_path": str(self.burned_chinese_video_path) if self.burned_chinese_video_path else "",
            "burned_bilingual_video_path": str(self.burned_bilingual_video_path) if self.burned_bilingual_video_path else "",
            "quality_report_path": str(self.quality_report_path) if self.quality_report_path else "",
            "duration_seconds": str(self.duration_seconds) if self.duration_seconds is not None else "",
            "work_dir": str(self.work_dir),
        }

    @classmethod
    def from_state(cls, state: dict[str, str]) -> "JobArtifacts":
        return cls(
            video_id=state.get("video_id", ""),
            title=state.get("title", ""),
            source_kind=state.get("source_kind", ""),
            video_path=Path(state["video_path"]) if state.get("video_path") else None,
            english_subtitle_path=Path(state["english_subtitle_path"]) if state.get("english_subtitle_path") else None,
            chinese_subtitle_path=Path(state["chinese_subtitle_path"]) if state.get("chinese_subtitle_path") else None,
            chinese_ass_path=Path(state["chinese_ass_path"]) if state.get("chinese_ass_path") else None,
            bilingual_ass_path=Path(state["bilingual_ass_path"]) if state.get("bilingual_ass_path") else None,
            burned_chinese_video_path=Path(state["burned_chinese_video_path"]) if state.get("burned_chinese_video_path") else None,
            burned_bilingual_video_path=Path(state["burned_bilingual_video_path"]) if state.get("burned_bilingual_video_path") else None,
            quality_report_path=Path(state["quality_report_path"]) if state.get("quality_report_path") else None,
            duration_seconds=int(state["duration_seconds"]) if state.get("duration_seconds") else None,
            work_dir=Path(state["work_dir"]) if state.get("work_dir") else Path("."),
        )
