from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Generator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ytsubviewer.config import Settings, settings
from ytsubviewer.job_state import (
    artifacts_from_state,
    deserialize_cues,
    load_job_state,
    save_job_state,
    serialize_cues,
)
from ytsubviewer.models import JobArtifacts, SubtitleCue, TranslationControlConfig, VideoMetadata
from ytsubviewer.quality import QualityReport, generate_quality_report, write_quality_report_markdown
from ytsubviewer.services.export import VideoExportService
from ytsubviewer.services.player import PlayerService
from ytsubviewer.services.transcribe import TranscriptionService
from ytsubviewer.services.translate import DeepSeekTranslator
from ytsubviewer.services.youtube import YouTubeService
from ytsubviewer.subtitle_processing import (
    build_bilingual_cues,
    build_bilingual_cues_from_tracks,
    parse_srt_file,
    parse_vtt_file,
    polish_translated_cues,
    split_source_cues,
    write_ass,
    write_srt,
)
from ytsubviewer.utils import format_eta, normalize_chinese_text, slugify_filename


logger = logging.getLogger(__name__)


ProgressCallback = Callable[[float, str], None]


@dataclass
class PipelineEvent:
    progress: float
    message: str
    artifacts: JobArtifacts | None = None
    stage: str = ""
    current_step: int | None = None
    total_steps: int | None = None
    completed_items: int | None = None
    total_items: int | None = None
    eta_seconds: float | None = None


@dataclass
class EditorSession:
    state: dict
    metadata: VideoMetadata
    controls: TranslationControlConfig
    source_cues: list[SubtitleCue]
    translated_cues: list[SubtitleCue]
    artifacts: JobArtifacts
    edited_cue_ids: set[int]
    locked_cue_ids: set[int]


class SubtitlePipeline:
    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self.settings.ensure_directories()
        self.youtube = YouTubeService(app_settings)
        self.transcriber = TranscriptionService(app_settings)
        self.translator = DeepSeekTranslator(app_settings, app_settings.translation_controls())
        self.exporter = VideoExportService(app_settings)
        self.player = PlayerService(app_settings)

    def analyze(self, url: str) -> VideoMetadata:
        return self.youtube.extract_metadata(url)

    def find_existing_artifacts(self, metadata: VideoMetadata) -> JobArtifacts | None:
        for work_dir in self.youtube.candidate_work_dirs(metadata):
            state = load_job_state(work_dir) or {}

            artifacts = artifacts_from_state(state)
            if artifacts is not None:
                artifacts = self._hydrate_existing_artifacts(artifacts, metadata.duration_seconds)
                if artifacts.chinese_subtitle_path:
                    return artifacts
                continue

            video_path = self.youtube.find_existing_video(work_dir)
            chinese_subtitle_path = self.youtube.find_existing_chinese_subtitle(work_dir)
            if not video_path or not chinese_subtitle_path:
                continue

            artifacts = self._hydrate_existing_artifacts(
                JobArtifacts(
                    video_id=metadata.video_id,
                    title=metadata.title,
                    source_kind=state.get("source_kind")
                    or (
                        "manual_subtitle"
                        if metadata.manual_english_subtitle_lang and self.youtube.find_existing_manual_subtitle(work_dir)
                        else (
                            "automatic_subtitle"
                            if metadata.automatic_english_subtitle_lang and self.youtube.find_existing_automatic_subtitle(work_dir)
                            else "faster_whisper"
                        )
                    ),
                    video_path=video_path,
                    english_subtitle_path=self.youtube.find_existing_subtitle(work_dir),
                    chinese_subtitle_path=chinese_subtitle_path,
                    quality_report_path=self.youtube.find_existing_quality_report(work_dir),
                    work_dir=work_dir,
                    duration_seconds=metadata.duration_seconds,
                ),
                metadata.duration_seconds,
            )
            if artifacts.chinese_subtitle_path:
                return artifacts
        return None

    def generate(
        self,
        url: str,
        *,
        controls: TranslationControlConfig | None = None,
        progress: ProgressCallback | None = None,
        performance_mode: str = "balanced",
    ) -> JobArtifacts:
        emit = progress or (lambda _value, _message: None)
        final_artifacts: JobArtifacts | None = None
        for event in self.generate_events(url, controls=controls, performance_mode=performance_mode):
            emit(event.progress, event.message)
            if event.artifacts is not None:
                final_artifacts = event.artifacts
        if final_artifacts is None:
            raise RuntimeError("任务意外结束，未生成输出文件。")
        return final_artifacts

    def generate_events(
        self,
        url: str,
        *,
        controls: TranslationControlConfig | None = None,
        performance_mode: str = "balanced",
    ) -> Generator[PipelineEvent, None, None]:
        yield PipelineEvent(0.05, "分析视频信息", stage="analyze")
        metadata = self.youtube.extract_metadata(url)
        work_dir = self.youtube.prepare_work_dir(metadata)
        state = load_job_state(work_dir) or {}
        controls = self._resolve_translation_controls(state, controls)
        translator = self._build_translator(controls, performance_mode=performance_mode)
        profile = self._performance_profile(performance_mode)

        existing_artifacts = self.find_existing_artifacts(metadata)
        if (
            state.get("status") == "completed"
            and existing_artifacts is not None
            and self._controls_match_state(state, controls)
        ):
            yield PipelineEvent(0.12, "检测到已有完成结果，检查附加字幕产物", stage="qa")
            existing_artifacts = self.ensure_subtitle_artifacts(existing_artifacts)
            existing_artifacts = self.ensure_quality_report(existing_artifacts)
            yield PipelineEvent(1.0, "处理完成，可直接打开播放器观看", artifacts=existing_artifacts, stage="completed")
            return
        if state.get("status") == "completed" and existing_artifacts is not None:
            yield PipelineEvent(0.12, "检测到旧结果，但翻译配置已变化，将基于现有素材重新生成", stage="qa")

        video_path = self.youtube.find_existing_video(work_dir)
        if video_path is not None:
            yield PipelineEvent(0.12, "复用已下载视频", stage="download")
        else:
            yield PipelineEvent(0.12, "下载本地视频", stage="download")
            video_path = self.youtube.download_video(url, work_dir)

        english_subtitle_path: Path | None = None
        source_kind = "faster_whisper"
        if metadata.manual_english_subtitle_lang:
            english_subtitle_path = self.youtube.find_existing_manual_subtitle(work_dir)
            if english_subtitle_path is not None:
                yield PipelineEvent(0.25, "复用已有英文字幕", stage="subtitle_source")
            else:
                yield PipelineEvent(0.25, "下载人工英文字幕", stage="subtitle_source")
                english_subtitle_path = self.youtube.download_manual_subtitle(
                    url,
                    metadata.manual_english_subtitle_lang,
                    work_dir,
                )
            if english_subtitle_path and english_subtitle_path.exists():
                source_kind = "manual_subtitle"
        elif self.settings.prefer_automatic_subtitles and metadata.automatic_english_subtitle_lang:
            english_subtitle_path = self.youtube.find_existing_automatic_subtitle(work_dir)
            if english_subtitle_path is not None:
                yield PipelineEvent(0.25, "复用已有自动英文字幕", stage="subtitle_source")
            else:
                yield PipelineEvent(0.25, "下载 YouTube 自动英文字幕", stage="subtitle_source")
                english_subtitle_path = self.youtube.download_automatic_subtitle(
                    url,
                    metadata.automatic_english_subtitle_lang,
                    work_dir,
                )
            if english_subtitle_path and english_subtitle_path.exists():
                source_kind = "automatic_subtitle"

        source_cues = self._restore_source_cues(state, source_kind)
        source_cues_ready = bool(source_cues)

        if source_cues:
            if source_kind == "manual_subtitle":
                yield PipelineEvent(0.35, f"复用已解析人工字幕，共 {len(source_cues)} 条", stage="subtitle_source")
            elif source_kind == "automatic_subtitle":
                yield PipelineEvent(0.35, f"复用已解析 YouTube 自动英文字幕，共 {len(source_cues)} 条", stage="subtitle_source")
            else:
                yield PipelineEvent(0.52, f"复用已转写英文字幕，共 {len(source_cues)} 条", stage="transcribe")
        elif source_kind == "manual_subtitle":
            yield PipelineEvent(0.35, "解析人工字幕", stage="subtitle_source")
            source_cues = parse_vtt_file(english_subtitle_path)
            source_cues_ready = True
        elif source_kind == "automatic_subtitle":
            yield PipelineEvent(0.35, "解析 YouTube 自动英文字幕", stage="subtitle_source")
            source_cues = parse_vtt_file(english_subtitle_path)
            source_cues_ready = True
        else:
            try:
                yield PipelineEvent(0.35, "抽取音频", stage="transcribe")
                audio_path = self.transcriber.extract_audio(video_path, work_dir)
                yield PipelineEvent(0.52, "本地转写英文字幕（首次使用可能下载模型）", stage="transcribe")
                source_cues = self.transcriber.transcribe(
                    audio_path,
                    primary_model=profile["whisper_model"],
                    fallback_model=profile["whisper_fallback_model"],
                )
            except Exception as exc:
                logger.exception(
                    "Transcription stage failed for video_id=%s title=%s work_dir=%s",
                    metadata.video_id,
                    metadata.title,
                    work_dir,
                )
                self._save_state(
                    work_dir=work_dir,
                    metadata=metadata,
                    status="failed",
                    source_kind=source_kind,
                    video_path=video_path,
                    english_subtitle_path=english_subtitle_path,
                    chinese_subtitle_path=None,
                    chinese_ass_path=None,
                    bilingual_ass_path=None,
                    burned_chinese_video_path=None,
                    burned_bilingual_video_path=None,
                    source_cues=[],
                    translated_cues=[],
                    translation_controls=controls,
                    quality_report_path=None,
                    quality_report=None,
                    last_error=str(exc),
                )
                raise

        if source_cues_ready:
            yield PipelineEvent(0.62, f"英文字幕分段已就绪，共 {len(source_cues)} 条", stage="subtitle_source")
        else:
            yield PipelineEvent(0.62, f"整理英文字幕分段，共 {len(source_cues)} 条", stage="subtitle_source")
            source_cues = split_source_cues(source_cues)

        translated_cues = self._restore_translated_cues(state, source_cues, source_kind, controls)
        done_count = len(translated_cues)

        self._save_state(
            work_dir=work_dir,
            metadata=metadata,
            status="translating",
            source_kind=source_kind,
            video_path=video_path,
            english_subtitle_path=english_subtitle_path,
            chinese_subtitle_path=None,
            chinese_ass_path=None,
            bilingual_ass_path=None,
            burned_chinese_video_path=self.youtube.find_existing_burned_chinese_video(work_dir),
            burned_bilingual_video_path=self.youtube.find_existing_burned_bilingual_video(work_dir),
            source_cues=source_cues,
            translated_cues=translated_cues,
            translation_controls=controls,
            quality_report_path=None,
            quality_report=None,
        )

        translated_ids = {item.id for item in translated_cues}
        remaining_cues = [cue for cue in source_cues if cue.id not in translated_ids]
        if done_count:
            yield PipelineEvent(0.70, f"继续翻译，已复用 {done_count}/{len(source_cues)} 条字幕", stage="translate")
        else:
            yield PipelineEvent(0.70, f"准备翻译，共 {len(source_cues)} 条字幕", stage="translate")

        if remaining_cues:
            remaining_batches = translator.build_batches(remaining_cues)
            batch_total = len(remaining_batches)
            start_time = time.monotonic()
            for batch_index, _, batch_result in translator.translate_cues_stream(remaining_cues):
                translated_cues.extend(batch_result)
                done_count = len(translated_cues)
                elapsed = time.monotonic() - start_time
                average_per_batch = elapsed / batch_index
                batches_left = batch_total - batch_index
                eta = format_eta(average_per_batch * batches_left)
                progress_value = 0.74 + 0.14 * (done_count / len(source_cues))

                self._save_state(
                    work_dir=work_dir,
                    metadata=metadata,
                    status="translating",
                    source_kind=source_kind,
                    video_path=video_path,
                    english_subtitle_path=english_subtitle_path,
                    chinese_subtitle_path=None,
                    chinese_ass_path=None,
                    bilingual_ass_path=None,
                    burned_chinese_video_path=self.youtube.find_existing_burned_chinese_video(work_dir),
                    burned_bilingual_video_path=self.youtube.find_existing_burned_bilingual_video(work_dir),
                    source_cues=source_cues,
                    translated_cues=translated_cues,
                    translation_controls=controls,
                    quality_report_path=None,
                    quality_report=None,
                )

                yield PipelineEvent(
                    progress_value,
                    (
                        f"调用 DeepSeek 翻译：本次第 {batch_index}/{batch_total} 批，"
                        f"累计 {done_count}/{len(source_cues)} 条，预计剩余 {eta}"
                    ),
                    stage="translate",
                    current_step=batch_index,
                    total_steps=batch_total,
                    completed_items=done_count,
                    total_items=len(source_cues),
                    eta_seconds=average_per_batch * batches_left,
                )
        else:
            yield PipelineEvent(0.88, f"已复用全部 {len(source_cues)} 条翻译结果", stage="translate")

        suspicious_ids = translator.suspicious_translation_ids(translated_cues)
        if suspicious_ids:
            yield PipelineEvent(0.89, f"检测到 {len(suspicious_ids)} 条低质量字幕，自动重译中", stage="qa")
            translated_cues = translator.repair_low_quality_translations(translated_cues)

        self._assert_translation_quality(source_cues, translated_cues, translator)

        yield PipelineEvent(0.90, "优化中文字幕断句", stage="qa")
        chinese_cues = polish_translated_cues(
            translated_cues,
            width=self.settings.target_line_width,
            max_lines=self.settings.max_subtitle_lines,
        )

        yield PipelineEvent(0.94, "生成双语播放字幕", stage="qa")
        bilingual_cues = build_bilingual_cues(translated_cues)
        quality_report = self._generate_quality_report(chinese_cues, metadata.duration_seconds, controls)

        safe_title = slugify_filename(metadata.title)
        chinese_subtitle_path = work_dir / f"{safe_title}.zh-CN.srt"
        chinese_ass_path = work_dir / f"{safe_title}.zh-CN.ass"
        bilingual_ass_path = work_dir / f"{safe_title}.bilingual.ass"
        quality_report_path = work_dir / f"{safe_title}.quality-report.md"

        yield PipelineEvent(0.96, "写入中文字幕文件", stage="qa")
        write_srt(chinese_cues, chinese_subtitle_path)
        yield PipelineEvent(0.98, "写入 ASS 播放字幕", stage="qa")
        write_ass(chinese_cues, chinese_ass_path, bilingual=False)
        write_ass(bilingual_cues, bilingual_ass_path, bilingual=True)
        write_quality_report_markdown(quality_report, quality_report_path)

        artifacts = self._hydrate_existing_artifacts(
            JobArtifacts(
                video_id=metadata.video_id,
                title=metadata.title,
                source_kind=source_kind,
                video_path=video_path,
                english_subtitle_path=english_subtitle_path,
                chinese_subtitle_path=chinese_subtitle_path,
                chinese_ass_path=chinese_ass_path,
                bilingual_ass_path=bilingual_ass_path,
                burned_chinese_video_path=self.youtube.find_existing_burned_chinese_video(work_dir),
                burned_bilingual_video_path=self.youtube.find_existing_burned_bilingual_video(work_dir),
                quality_report_path=quality_report_path,
                duration_seconds=metadata.duration_seconds,
                work_dir=work_dir,
            ),
            metadata.duration_seconds,
        )
        self._save_state(
            work_dir=work_dir,
            metadata=metadata,
            status="completed",
            source_kind=source_kind,
            video_path=video_path,
            english_subtitle_path=english_subtitle_path,
            chinese_subtitle_path=artifacts.chinese_subtitle_path,
            chinese_ass_path=artifacts.chinese_ass_path,
            bilingual_ass_path=artifacts.bilingual_ass_path,
            burned_chinese_video_path=artifacts.burned_chinese_video_path,
            burned_bilingual_video_path=artifacts.burned_bilingual_video_path,
            source_cues=source_cues,
            translated_cues=translated_cues,
            translation_controls=controls,
            quality_report_path=artifacts.quality_report_path,
            quality_report=quality_report.to_dict(),
        )
        yield PipelineEvent(1.0, "处理完成，可直接打开播放器观看", artifacts=artifacts, stage="completed")

    def ensure_subtitle_artifacts(self, artifacts: JobArtifacts) -> JobArtifacts:
        artifacts = self._hydrate_existing_artifacts(artifacts, artifacts.duration_seconds)
        if artifacts.chinese_ass_path and artifacts.bilingual_ass_path:
            return artifacts

        state = load_job_state(artifacts.work_dir) or {}
        source_cues = deserialize_cues(state.get("source_cues"))
        translated_cues = deserialize_cues(state.get("translated_cues"))
        translated_ready = self._translated_cues_are_complete(source_cues, translated_cues)

        if artifacts.chinese_ass_path is None:
            chinese_cues = (
                polish_translated_cues(
                    translated_cues,
                    width=self.settings.target_line_width,
                    max_lines=self.settings.max_subtitle_lines,
                )
                if translated_ready
                else parse_srt_file(artifacts.chinese_subtitle_path)
            )
            safe_title = slugify_filename(artifacts.title)
            artifacts.chinese_ass_path = write_ass(
                chinese_cues,
                artifacts.work_dir / f"{safe_title}.zh-CN.ass",
                bilingual=False,
            )

        if artifacts.bilingual_ass_path is None:
            bilingual_cues: list[SubtitleCue] = []
            if translated_ready:
                bilingual_cues = build_bilingual_cues(translated_cues)
            elif source_cues and artifacts.chinese_subtitle_path:
                bilingual_cues = build_bilingual_cues_from_tracks(
                    source_cues,
                    parse_srt_file(artifacts.chinese_subtitle_path),
                )
            elif artifacts.english_subtitle_path and artifacts.chinese_subtitle_path:
                bilingual_cues = build_bilingual_cues_from_tracks(
                    parse_vtt_file(artifacts.english_subtitle_path),
                    parse_srt_file(artifacts.chinese_subtitle_path),
                )

            if bilingual_cues:
                safe_title = slugify_filename(artifacts.title)
                artifacts.bilingual_ass_path = write_ass(
                    bilingual_cues,
                    artifacts.work_dir / f"{safe_title}.bilingual.ass",
                    bilingual=True,
                )

        self._persist_artifact_paths(artifacts, state=state)
        return artifacts

    def ensure_quality_report(self, artifacts: JobArtifacts) -> JobArtifacts:
        artifacts = self._hydrate_existing_artifacts(artifacts, artifacts.duration_seconds)
        if artifacts.quality_report_path and artifacts.quality_report_path.exists():
            return artifacts

        state = load_job_state(artifacts.work_dir) or {}
        controls = self._resolve_translation_controls(state, None)
        source_cues = deserialize_cues(state.get("source_cues"))
        translated_cues = deserialize_cues(state.get("translated_cues"))

        if self._translated_cues_are_complete(source_cues, translated_cues):
            chinese_cues = polish_translated_cues(
                translated_cues,
                width=self.settings.target_line_width,
                max_lines=self.settings.max_subtitle_lines,
            )
        elif artifacts.chinese_subtitle_path:
            chinese_cues = parse_srt_file(artifacts.chinese_subtitle_path)
        else:
            return artifacts

        report = self._generate_quality_report(chinese_cues, artifacts.duration_seconds, controls)
        safe_title = slugify_filename(artifacts.title)
        artifacts.quality_report_path = write_quality_report_markdown(
            report,
            artifacts.work_dir / f"{safe_title}.quality-report.md",
        )
        state["translation_controls"] = controls.to_dict()
        state["quality_report"] = report.to_dict()
        self._persist_artifact_paths(artifacts, state=state)
        return artifacts

    def update_cue_translation(self, state: dict[str, object], cue_id: int, target_text: str) -> JobArtifacts:
        session = self._load_editor_session(state)
        cue_index = self._find_editor_cue_index(session.translated_cues, cue_id)
        normalized_target = normalize_chinese_text(target_text.strip())
        if not normalized_target:
            raise RuntimeError("字幕译文不能为空。")

        source_cue = session.source_cues[cue_index]
        session.translated_cues[cue_index] = source_cue.clone(target_text=normalized_target)
        session.edited_cue_ids.add(cue_id)
        return self._commit_editor_session(session)

    def retranslate_cue(self, state: dict[str, object], cue_id: int) -> JobArtifacts:
        session = self._load_editor_session(state)
        cue_index = self._find_editor_cue_index(session.translated_cues, cue_id)
        translator = self._build_translator(session.controls)
        translated_cue = translator.translate_cues([session.source_cues[cue_index]])[0]
        session.translated_cues[cue_index] = translated_cue
        session.edited_cue_ids.add(cue_id)
        return self._commit_editor_session(session)

    def set_cue_lock(self, state: dict[str, object], cue_id: int, locked: bool) -> JobArtifacts:
        session = self._load_editor_session(state)
        self._find_editor_cue_index(session.translated_cues, cue_id)
        if locked:
            session.locked_cue_ids.add(cue_id)
        else:
            session.locked_cue_ids.discard(cue_id)
        return self._commit_editor_session(session)

    def bulk_replace_term(self, state: dict[str, object], source_text: str, target_text: str) -> JobArtifacts:
        session = self._load_editor_session(state)
        needle = normalize_chinese_text(source_text.strip())
        replacement = normalize_chinese_text(target_text.strip())
        if not needle:
            raise RuntimeError("批量替换的原词不能为空。")
        changed = False
        for index, cue in enumerate(session.translated_cues):
            if cue.id in session.locked_cue_ids:
                continue
            if needle not in cue.target_text:
                continue
            session.translated_cues[index] = cue.clone(target_text=cue.target_text.replace(needle, replacement))
            session.edited_cue_ids.add(cue.id)
            changed = True
        if not changed:
            return session.artifacts
        return self._commit_editor_session(session)

    def get_editor_payload(self, state: dict[str, object], *, issues_only: bool = False, query: str = "") -> dict[str, object]:
        session = self._load_editor_session(state)
        quality_report = dict(session.state.get("quality_report") or {})
        issue_cue_ids = {
            int(cue_id)
            for issue in (quality_report.get("issues") or [])
            for cue_id in (issue.get("cue_ids") or [])
            if str(cue_id).strip().isdigit()
        }
        query_text = query.strip().lower()
        rows: list[dict[str, object]] = []
        for index, (source_cue, translated_cue) in enumerate(zip(session.source_cues, session.translated_cues)):
            if issues_only and source_cue.id not in issue_cue_ids:
                continue
            haystack = f"{source_cue.source_text}\n{translated_cue.target_text}".lower()
            if query_text and query_text not in haystack:
                continue
            rows.append(
                {
                    "cue_id": source_cue.id,
                    "index": index + 1,
                    "start": source_cue.start,
                    "end": source_cue.end,
                    "source_text": source_cue.source_text,
                    "target_text": translated_cue.target_text,
                    "edited": source_cue.id in session.edited_cue_ids,
                    "locked": source_cue.id in session.locked_cue_ids,
                    "has_issue": source_cue.id in issue_cue_ids,
                    "previous_source_text": session.source_cues[index - 1].source_text if index > 0 else "",
                    "next_source_text": (
                        session.source_cues[index + 1].source_text if index + 1 < len(session.source_cues) else ""
                    ),
                }
            )
        return {
            "work_dir": str(session.artifacts.work_dir),
            "title": session.metadata.title,
            "cue_count": len(session.source_cues),
            "rows": rows,
            "quality_report": quality_report,
            "edited_cue_ids": sorted(session.edited_cue_ids),
            "locked_cue_ids": sorted(session.locked_cue_ids),
        }

    def export_video(
        self,
        state: dict[str, str],
        *,
        bilingual: bool,
        progress: ProgressCallback | None = None,
        preview: bool = False,
        performance_mode: str = "balanced",
    ) -> JobArtifacts:
        emit = progress or (lambda _value, _message: None)
        final_artifacts: JobArtifacts | None = None
        for event in self.export_video_events(
            state,
            bilingual=bilingual,
            preview=preview,
            performance_mode=performance_mode,
        ):
            emit(event.progress, event.message)
            if event.artifacts is not None:
                final_artifacts = event.artifacts
        if final_artifacts is None:
            raise RuntimeError("导出意外结束，未生成视频文件。")
        return final_artifacts

    def export_video_events(
        self,
        state: dict[str, str],
        *,
        bilingual: bool,
        preview: bool = False,
        performance_mode: str = "balanced",
    ) -> Generator[PipelineEvent, None, None]:
        artifacts = JobArtifacts.from_state(state)
        if artifacts.video_path is None or artifacts.chinese_subtitle_path is None:
            raise RuntimeError("还没有可导出的任务结果，请先生成中文字幕。")

        # Disk space check
        if artifacts.work_dir and artifacts.work_dir.exists():
            free_bytes = shutil.disk_usage(str(artifacts.work_dir)).free
            if free_bytes < 2 * 1024 * 1024 * 1024:
                raise RuntimeError(f"磁盘空间不足，剩余 {free_bytes / (1024**3):.1f}GB，导出需要至少 2GB。")

        label = "双语预览 MP4" if preview and bilingual else "中文字幕预览 MP4" if preview else "双语 MP4" if bilingual else "中文字幕 MP4"
        yield PipelineEvent(0.05, f"准备导出 {label}", stage="export")
        artifacts = self.ensure_subtitle_artifacts(artifacts)
        artifacts = self.ensure_quality_report(artifacts)
        subtitle_path = artifacts.bilingual_ass_path if bilingual else artifacts.chinese_ass_path
        if subtitle_path is None:
            raise RuntimeError("缺少可用的 ASS 字幕文件，请重新生成字幕后再试。")

        safe_title = slugify_filename(artifacts.title)
        output_path = artifacts.work_dir / (
            f"{safe_title}.bilingual.preview.mp4"
            if preview and bilingual
            else f"{safe_title}.zh-CN.preview.mp4"
            if preview
            else f"{safe_title}.bilingual.hardsub.mp4"
            if bilingual
            else f"{safe_title}.zh-CN.hardsub.mp4"
        )
        profile = self._performance_profile(performance_mode)

        exporter = self._build_exporter(performance_mode=performance_mode)
        export_timeout = (artifacts.duration_seconds or 3600) * 3 + 1800
        for ratio, message in exporter.burn_video_events(
            artifacts.video_path,
            subtitle_path,
            output_path,
            label=label,
            preset=profile["preview_export_preset"] if preview else profile["export_preset"],
            crf=profile["preview_export_crf"] if preview else profile["export_crf"],
            timeout_seconds=export_timeout,
        ):
            progress_value = 0.08 + 0.90 * ratio
            yield PipelineEvent(progress_value, message, stage="export")

        if bilingual:
            artifacts.burned_bilingual_video_path = output_path
        else:
            artifacts.burned_chinese_video_path = output_path
        self._persist_artifact_paths(artifacts)
        yield PipelineEvent(1.0, f"{label} 已可下载", artifacts=artifacts, stage="completed")

    def prepare_player_paths(self, artifacts: JobArtifacts, *, bilingual: bool) -> tuple[Path, Path]:
        if artifacts.video_path is None:
            raise RuntimeError("本地视频文件不存在，无法播放。")
        artifacts = self.ensure_subtitle_artifacts(artifacts)
        artifacts = self.ensure_quality_report(artifacts)
        subtitle_path = artifacts.bilingual_ass_path if bilingual else artifacts.chinese_ass_path
        if subtitle_path is None:
            raise RuntimeError("缺少可用的播放字幕，请重新生成字幕后再试。")
        return artifacts.video_path, subtitle_path

    def _hydrate_existing_artifacts(self, artifacts: JobArtifacts, duration_seconds: int | None) -> JobArtifacts:
        artifacts.duration_seconds = duration_seconds if duration_seconds is not None else artifacts.duration_seconds
        artifacts.chinese_ass_path = artifacts.chinese_ass_path or self.youtube.find_existing_chinese_ass(artifacts.work_dir)
        artifacts.bilingual_ass_path = artifacts.bilingual_ass_path or self.youtube.find_existing_bilingual_ass(artifacts.work_dir)
        artifacts.burned_chinese_video_path = (
            artifacts.burned_chinese_video_path or self.youtube.find_existing_burned_chinese_video(artifacts.work_dir)
        )
        artifacts.burned_bilingual_video_path = (
            artifacts.burned_bilingual_video_path or self.youtube.find_existing_burned_bilingual_video(artifacts.work_dir)
        )
        artifacts.quality_report_path = artifacts.quality_report_path or self.youtube.find_existing_quality_report(artifacts.work_dir)
        if not self._subtitle_path_is_complete(artifacts.chinese_subtitle_path, artifacts.duration_seconds):
            artifacts.chinese_subtitle_path = None
            artifacts.chinese_ass_path = None
            artifacts.bilingual_ass_path = None
            artifacts.burned_chinese_video_path = None
            artifacts.burned_bilingual_video_path = None
            artifacts.quality_report_path = None
            return artifacts
        if not self._subtitle_path_has_translation_content(artifacts.chinese_subtitle_path):
            artifacts.chinese_subtitle_path = None
            artifacts.chinese_ass_path = None
            artifacts.bilingual_ass_path = None
            artifacts.burned_chinese_video_path = None
            artifacts.burned_bilingual_video_path = None
            artifacts.quality_report_path = None
            return artifacts

        if not self._subtitle_path_is_complete(artifacts.chinese_ass_path, artifacts.duration_seconds):
            artifacts.chinese_ass_path = None
            artifacts.burned_chinese_video_path = None
        elif not self._subtitle_path_has_translation_content(artifacts.chinese_ass_path):
            artifacts.chinese_ass_path = None
            artifacts.burned_chinese_video_path = None

        if not self._subtitle_path_is_complete(artifacts.bilingual_ass_path, artifacts.duration_seconds):
            artifacts.bilingual_ass_path = None
            artifacts.burned_bilingual_video_path = None
        elif not self._subtitle_path_has_translation_content(artifacts.bilingual_ass_path, bilingual=True):
            artifacts.bilingual_ass_path = None
            artifacts.burned_bilingual_video_path = None

        if artifacts.quality_report_path and not artifacts.quality_report_path.exists():
            artifacts.quality_report_path = None

        if (
            artifacts.burned_chinese_video_path
            and artifacts.chinese_ass_path
            and artifacts.burned_chinese_video_path.stat().st_mtime < artifacts.chinese_ass_path.stat().st_mtime
        ):
            artifacts.burned_chinese_video_path = None

        if (
            artifacts.burned_bilingual_video_path
            and artifacts.bilingual_ass_path
            and artifacts.burned_bilingual_video_path.stat().st_mtime < artifacts.bilingual_ass_path.stat().st_mtime
        ):
            artifacts.burned_bilingual_video_path = None
        return artifacts

    def _load_editor_session(self, state: dict[str, object]) -> EditorSession:
        if not state:
            raise RuntimeError("当前还没有可编辑的字幕任务。")

        work_dir_value = str(state.get("work_dir", "")).strip()
        persisted_state = load_job_state(Path(work_dir_value)) if work_dir_value else None
        payload = dict(persisted_state or state)
        artifacts = artifacts_from_state(payload)
        if artifacts is None:
            raise RuntimeError("当前任务缺少可编辑的字幕结果，请先生成中文字幕。")

        artifacts = self._hydrate_existing_artifacts(artifacts, artifacts.duration_seconds)
        source_cues = deserialize_cues(payload.get("source_cues"))
        translated_cues = deserialize_cues(payload.get("translated_cues"))
        if not self._translated_cues_are_complete(source_cues, translated_cues):
            raise RuntimeError("当前任务缺少可编辑的字幕句子数据，请先重新生成一次字幕。")

        edited_cue_ids = {
            int(item)
            for item in (payload.get("edited_cue_ids") or [])
            if str(item).strip().isdigit()
        }
        locked_cue_ids = {
            int(item)
            for item in (payload.get("locked_cue_ids") or [])
            if str(item).strip().isdigit()
        }
        metadata = VideoMetadata(
            video_id=payload.get("video_id", ""),
            title=payload.get("title", ""),
            duration_seconds=int(payload["duration_seconds"]) if payload.get("duration_seconds") else None,
            thumbnail_url=None,
            manual_english_subtitle_lang=None,
            automatic_english_subtitle_lang=None,
            channel_id=payload.get("channel_id"),
            channel_name=payload.get("channel_name"),
            uploader=payload.get("uploader"),
        )
        return EditorSession(
            state=payload,
            metadata=metadata,
            controls=self._resolve_translation_controls(payload, None),
            source_cues=source_cues,
            translated_cues=translated_cues,
            artifacts=artifacts,
            edited_cue_ids=edited_cue_ids,
            locked_cue_ids=locked_cue_ids,
        )

    @staticmethod
    def _find_editor_cue_index(cues: list[SubtitleCue], cue_id: int) -> int:
        for index, cue in enumerate(cues):
            if cue.id == cue_id:
                return index
        raise RuntimeError(f"未找到编号为 {cue_id} 的字幕句子。")

    def _commit_editor_session(self, session: EditorSession) -> JobArtifacts:
        chinese_cues = polish_translated_cues(
            session.translated_cues,
            width=self.settings.target_line_width,
            max_lines=self.settings.max_subtitle_lines,
        )
        bilingual_cues = build_bilingual_cues(session.translated_cues)
        quality_report = self._generate_quality_report(
            chinese_cues,
            session.metadata.duration_seconds,
            session.controls,
        )

        safe_title = slugify_filename(session.metadata.title)
        chinese_subtitle_path = session.artifacts.work_dir / f"{safe_title}.zh-CN.srt"
        chinese_ass_path = session.artifacts.work_dir / f"{safe_title}.zh-CN.ass"
        bilingual_ass_path = session.artifacts.work_dir / f"{safe_title}.bilingual.ass"
        quality_report_path = session.artifacts.work_dir / f"{safe_title}.quality-report.md"

        write_srt(chinese_cues, chinese_subtitle_path)
        write_ass(chinese_cues, chinese_ass_path, bilingual=False)
        write_ass(bilingual_cues, bilingual_ass_path, bilingual=True)
        write_quality_report_markdown(quality_report, quality_report_path)

        artifacts = self._hydrate_existing_artifacts(
            JobArtifacts(
                video_id=session.metadata.video_id,
                title=session.metadata.title,
                source_kind=session.artifacts.source_kind,
                video_path=session.artifacts.video_path,
                english_subtitle_path=session.artifacts.english_subtitle_path,
                chinese_subtitle_path=chinese_subtitle_path,
                chinese_ass_path=chinese_ass_path,
                bilingual_ass_path=bilingual_ass_path,
                burned_chinese_video_path=None,
                burned_bilingual_video_path=None,
                quality_report_path=quality_report_path,
                duration_seconds=session.metadata.duration_seconds,
                work_dir=session.artifacts.work_dir,
            ),
            session.metadata.duration_seconds,
        )
        self._save_state(
            work_dir=session.artifacts.work_dir,
            metadata=session.metadata,
            status="completed",
            source_kind=session.artifacts.source_kind,
            video_path=artifacts.video_path,
            english_subtitle_path=artifacts.english_subtitle_path,
            chinese_subtitle_path=artifacts.chinese_subtitle_path,
            chinese_ass_path=artifacts.chinese_ass_path,
            bilingual_ass_path=artifacts.bilingual_ass_path,
            burned_chinese_video_path=artifacts.burned_chinese_video_path,
            burned_bilingual_video_path=artifacts.burned_bilingual_video_path,
            source_cues=session.source_cues,
            translated_cues=session.translated_cues,
            translation_controls=session.controls,
            quality_report_path=artifacts.quality_report_path,
            quality_report=quality_report.to_dict(),
        )

        saved_state = load_job_state(session.artifacts.work_dir) or {}
        saved_state["edited_cue_ids"] = sorted(session.edited_cue_ids)
        saved_state["locked_cue_ids"] = sorted(session.locked_cue_ids)
        save_job_state(session.artifacts.work_dir, saved_state)
        return artifacts

    def _restore_source_cues(self, state: dict, source_kind: str) -> list[SubtitleCue]:
        if state.get("source_kind") != source_kind:
            return []
        return deserialize_cues(state.get("source_cues"))

    def _restore_translated_cues(
        self,
        state: dict,
        source_cues: list[SubtitleCue],
        source_kind: str,
        controls: TranslationControlConfig,
    ) -> list[SubtitleCue]:
        if state.get("source_kind") != source_kind:
            return []
        if not self._controls_match_state(state, controls):
            return []

        saved_source_cues = deserialize_cues(state.get("source_cues"))
        if not self._cues_match(saved_source_cues, source_cues):
            return []

        translated_cues = deserialize_cues(state.get("translated_cues"))
        source_map = {cue.id: cue for cue in source_cues}
        restored: list[SubtitleCue] = []
        for cue in translated_cues:
            source_cue = source_map.get(cue.id)
            if source_cue is None:
                continue
            if cue.source_text != source_cue.source_text or not cue.target_text:
                continue
            restored.append(cue)
        restored.sort(key=lambda cue: cue.id)
        return restored

    @staticmethod
    def _cues_match(saved_cues: list[SubtitleCue], current_cues: list[SubtitleCue]) -> bool:
        if len(saved_cues) != len(current_cues):
            return False
        return all(
            saved.id == current.id and saved.source_text == current.source_text
            for saved, current in zip(saved_cues, current_cues)
        )

    def _build_translator(
        self,
        controls: TranslationControlConfig,
        *,
        performance_mode: str = "balanced",
    ) -> DeepSeekTranslator:
        profile = self._performance_profile(performance_mode)
        runtime_settings = replace(
            self.settings,
            translation_parallel_workers=profile["translation_parallel_workers"],
        )
        return DeepSeekTranslator(runtime_settings, controls)

    def _build_exporter(self, *, performance_mode: str = "balanced") -> VideoExportService:
        return VideoExportService(self.settings)

    def _performance_profile(self, performance_mode: str) -> dict[str, object]:
        mode = (performance_mode or "balanced").strip().lower()
        if mode == "fast":
            return {
                "name": "fast",
                "whisper_model": self.settings.whisper_fallback_model,
                "whisper_fallback_model": self.settings.whisper_fallback_model,
                "translation_parallel_workers": max(self.settings.translation_parallel_workers, 4),
                "export_preset": "ultrafast",
                "export_crf": 22,
                "preview_export_preset": "ultrafast",
                "preview_export_crf": 25,
            }
        if mode == "quality":
            return {
                "name": "quality",
                "whisper_model": self.settings.whisper_model,
                "whisper_fallback_model": self.settings.whisper_fallback_model,
                "translation_parallel_workers": max(1, self.settings.translation_parallel_workers - 1),
                "export_preset": "medium",
                "export_crf": 16,
                "preview_export_preset": "superfast",
                "preview_export_crf": 22,
            }
        return {
            "name": "balanced",
            "whisper_model": self.settings.whisper_model,
            "whisper_fallback_model": self.settings.whisper_fallback_model,
            "translation_parallel_workers": self.settings.translation_parallel_workers,
            "export_preset": "superfast",
            "export_crf": 18,
            "preview_export_preset": "ultrafast",
            "preview_export_crf": 24,
        }

    def _resolve_translation_controls(
        self,
        state: dict,
        controls: TranslationControlConfig | None,
    ) -> TranslationControlConfig:
        if controls is not None:
            return controls
        stored = TranslationControlConfig.from_dict(state.get("translation_controls"))
        if stored != TranslationControlConfig():
            return stored
        return self.settings.translation_controls()

    @staticmethod
    def _controls_match_state(state: dict, controls: TranslationControlConfig) -> bool:
        stored = TranslationControlConfig.from_dict(state.get("translation_controls"))
        if not state.get("translation_controls"):
            return controls == TranslationControlConfig()
        return stored == controls

    def _persist_artifact_paths(self, artifacts: JobArtifacts, *, state: dict | None = None) -> None:
        payload = dict(state or load_job_state(artifacts.work_dir) or {})
        payload.update(
            {
                "version": payload.get("version", 1),
                "status": payload.get("status", "completed"),
                "video_id": artifacts.video_id,
                "title": artifacts.title,
                "source_kind": artifacts.source_kind,
                "video_path": str(artifacts.video_path) if artifacts.video_path else "",
                "english_subtitle_path": str(artifacts.english_subtitle_path) if artifacts.english_subtitle_path else "",
                "chinese_subtitle_path": str(artifacts.chinese_subtitle_path) if artifacts.chinese_subtitle_path else "",
                "chinese_ass_path": str(artifacts.chinese_ass_path) if artifacts.chinese_ass_path else "",
                "bilingual_ass_path": str(artifacts.bilingual_ass_path) if artifacts.bilingual_ass_path else "",
                "burned_chinese_video_path": (
                    str(artifacts.burned_chinese_video_path) if artifacts.burned_chinese_video_path else ""
                ),
                "burned_bilingual_video_path": (
                    str(artifacts.burned_bilingual_video_path) if artifacts.burned_bilingual_video_path else ""
                ),
                "quality_report_path": str(artifacts.quality_report_path) if artifacts.quality_report_path else "",
                "duration_seconds": artifacts.duration_seconds if artifacts.duration_seconds is not None else "",
                "work_dir": str(artifacts.work_dir),
            }
        )
        save_job_state(artifacts.work_dir, payload)

    def _subtitle_path_is_complete(self, path: Path | None, duration_seconds: int | None) -> bool:
        if path is None or not path.exists():
            return False
        if duration_seconds is None:
            return True

        last_end = 0.0
        if path.suffix.lower() == ".srt":
            cues = parse_srt_file(path)
            last_end = cues[-1].end if cues else 0.0
        elif path.suffix.lower() == ".ass":
            last_end = self._read_ass_last_end(path)
        else:
            return True

        required_end = max(duration_seconds * 0.85, duration_seconds - 60)
        return last_end >= required_end

    def _subtitle_path_has_translation_content(self, path: Path | None, *, bilingual: bool = False) -> bool:
        if path is None or not path.exists():
            return False

        if path.suffix.lower() == ".srt":
            texts = [cue.target_text for cue in parse_srt_file(path)]
        elif path.suffix.lower() == ".ass":
            texts = [text for _, text in self._read_ass_entries(path, bilingual=bilingual)]
        else:
            return True

        return self._texts_look_translated(texts)

    def _texts_look_translated(self, texts: list[str]) -> bool:
        if not texts:
            return False

        translated_flags = [self._looks_like_chinese_text(text) for text in texts]
        overall_ratio = sum(translated_flags) / len(translated_flags)
        tail_size = min(len(translated_flags), max(40, len(translated_flags) // 5))
        tail_ratio = sum(translated_flags[-tail_size:]) / max(tail_size, 1)
        return overall_ratio >= 0.55 and tail_ratio >= 0.45

    def _assert_translation_quality(
        self,
        source_cues: list[SubtitleCue],
        translated_cues: list[SubtitleCue],
        translator: DeepSeekTranslator,
    ) -> None:
        if not self._translated_cues_are_complete(source_cues, translated_cues):
            raise RuntimeError("翻译结果不完整，无法生成长视频成品。")

        suspicious_ids = translator.suspicious_translation_ids(translated_cues)
        if not suspicious_ids:
            return

        overall_ratio = len(suspicious_ids) / max(len(translated_cues), 1)
        tail_size = min(len(translated_cues), max(80, len(translated_cues) // 5))
        tail_suspicious = translator.suspicious_translation_ids(translated_cues[-tail_size:])
        tail_ratio = len(tail_suspicious) / max(tail_size, 1)

        if overall_ratio > 0.18 or tail_ratio > 0.16:
            raise RuntimeError(
                f"翻译质量校验未通过，检测到 {len(suspicious_ids)} 条疑似保留英文的字幕。"
            )

    def _generate_quality_report(
        self,
        chinese_cues: list[SubtitleCue],
        duration_seconds: int | None,
        controls: TranslationControlConfig,
    ) -> QualityReport:
        glossary = {entry.source: entry.target for entry in controls.glossary}
        return generate_quality_report(
            chinese_cues,
            expected_duration=float(duration_seconds) if duration_seconds is not None else None,
            glossary=glossary or None,
        )

    @staticmethod
    def _translated_cues_are_complete(source_cues: list[SubtitleCue], translated_cues: list[SubtitleCue]) -> bool:
        if not source_cues or not translated_cues:
            return False
        if len(source_cues) != len(translated_cues):
            return False
        return all(
            source.id == translated.id
            and source.source_text == translated.source_text
            and bool(translated.target_text)
            for source, translated in zip(source_cues, translated_cues)
        )

    @staticmethod
    def _looks_like_chinese_text(text: str) -> bool:
        if not text:
            return False
        for character in text:
            if "\u3400" <= character <= "\u4dbf" or "\u4e00" <= character <= "\u9fff":
                return True
        return any(marker in text for marker in "，。！？；：、“”‘’（）《》【】")

    @staticmethod
    def _read_ass_last_end(path: Path) -> float:
        import re

        last_end = 0.0
        pattern = re.compile(r"^Dialogue:\s*\d+,([^,]+),([^,]+),")
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            match = pattern.match(line)
            if match is None:
                continue
            end = match.group(2).replace(".", ":").split(":")
            if len(end) != 4:
                continue
            hours, minutes, seconds, centiseconds = end
            value = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(centiseconds) / 100
            if value > last_end:
                last_end = value
        return last_end

    @staticmethod
    def _read_ass_entries(path: Path, *, bilingual: bool) -> list[tuple[float, str]]:
        import re

        entries: list[tuple[float, str]] = []
        pattern = re.compile(r"^Dialogue:\s*\d+,([^,]+),([^,]+),[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,,(.*)$")
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            match = pattern.match(line)
            if match is None:
                continue
            end = match.group(2).replace(".", ":").split(":")
            if len(end) != 4:
                continue
            hours, minutes, seconds, centiseconds = end
            payload = match.group(3)
            if bilingual:
                payload = payload.split(r"\N", 1)[1] if r"\N" in payload else ""
            text = payload.replace(r"\N", " ").replace(r"\\", "\\").strip()
            value = int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(centiseconds) / 100
            entries.append((value, text))
        return entries

    def _save_state(
        self,
        *,
        work_dir: Path,
        metadata: VideoMetadata,
        status: str,
        source_kind: str,
        video_path: Path | None,
        english_subtitle_path: Path | None,
        chinese_subtitle_path: Path | None,
        chinese_ass_path: Path | None,
        bilingual_ass_path: Path | None,
        burned_chinese_video_path: Path | None,
        burned_bilingual_video_path: Path | None,
        source_cues: list[SubtitleCue],
        translated_cues: list[SubtitleCue],
        translation_controls: TranslationControlConfig,
        quality_report_path: Path | None,
        quality_report: dict | None,
        last_error: str | None = None,
    ) -> None:
        save_job_state(
            work_dir,
            {
                "version": 1,
                "status": status,
                "video_id": metadata.video_id,
                "title": metadata.title,
                "source_kind": source_kind,
                "channel_id": metadata.channel_id or "",
                "channel_name": metadata.channel_name or "",
                "uploader": metadata.uploader or "",
                "video_path": str(video_path) if video_path else "",
                "english_subtitle_path": str(english_subtitle_path) if english_subtitle_path else "",
                "chinese_subtitle_path": str(chinese_subtitle_path) if chinese_subtitle_path else "",
                "chinese_ass_path": str(chinese_ass_path) if chinese_ass_path else "",
                "bilingual_ass_path": str(bilingual_ass_path) if bilingual_ass_path else "",
                "burned_chinese_video_path": str(burned_chinese_video_path) if burned_chinese_video_path else "",
                "burned_bilingual_video_path": str(burned_bilingual_video_path) if burned_bilingual_video_path else "",
                "quality_report_path": str(quality_report_path) if quality_report_path else "",
                "duration_seconds": metadata.duration_seconds if metadata.duration_seconds is not None else "",
                "work_dir": str(work_dir),
                "translation_controls": translation_controls.to_dict(),
                "quality_report": quality_report or {},
                "last_error": last_error or "",
                "source_cues": serialize_cues(source_cues),
                "translated_cues": serialize_cues(translated_cues),
            },
        )
