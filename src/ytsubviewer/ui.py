from __future__ import annotations

from collections.abc import Generator
import logging
from pathlib import Path

import gradio as gr

from ytsubviewer.background_jobs import BackgroundGenerationManager, GenerationJobSnapshot
from ytsubviewer.config import Settings, save_user_settings, settings as app_settings
from ytsubviewer.job_state import artifacts_from_state, deserialize_cues, load_job_state
from ytsubviewer.models import JobArtifacts, SubtitleCue, TranslationControlConfig
from ytsubviewer.pipeline import SubtitlePipeline
from ytsubviewer.runtime import inspect_environment
from ytsubviewer.services.translate import DeepSeekTranslator
from ytsubviewer.utils import format_duration, seconds_to_srt_timestamp


pipeline = SubtitlePipeline(app_settings)
generation_manager = BackgroundGenerationManager(app_settings, pipeline)
logger = logging.getLogger(__name__)

STYLE_PRESETS = DeepSeekTranslator.available_style_presets()
STYLE_CHOICES = [(preset.label, preset.name) for preset in STYLE_PRESETS.values()]

EDITOR_FILTER_ALL = "全部字幕"
EDITOR_FILTER_ISSUES = "仅问题句"
EDITOR_FILTER_EDITED = "仅已调整"
EDITOR_FILTER_CHOICES = [EDITOR_FILTER_ALL, EDITOR_FILTER_ISSUES, EDITOR_FILTER_EDITED]

APP_CSS = """
:root {
    --app-bg: #f3f3ef;
    --app-card: #ffffff;
    --app-text: #111111;
    --app-muted: #5c5c5c;
    --app-line: #111111;
    --app-soft: #ecece7;
}

body {
    background: var(--app-bg);
    color: var(--app-text);
}

.gradio-container {
    max-width: 1360px !important;
    margin: 0 auto !important;
    padding: 28px 20px 56px !important;
    background: var(--app-bg) !important;
}

.hero-banner {
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding: 28px 30px;
    border: 1.5px solid var(--app-line);
    border-radius: 30px;
    background: linear-gradient(180deg, #ffffff 0%, #f4f4f0 100%);
}

.hero-banner h1 {
    margin: 0;
    font-size: 2.15rem;
    line-height: 1.15;
    font-weight: 700;
    letter-spacing: -0.03em;
}

.hero-banner p {
    margin: 0;
    color: var(--app-muted);
    font-size: 1rem;
    line-height: 1.65;
}

.hero-kicker {
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}

.hero-steps {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}

.hero-step {
    padding: 10px 14px;
    border: 1.5px solid var(--app-line);
    border-radius: 999px;
    background: #fff;
    font-size: 0.92rem;
    font-weight: 600;
}

.surface-card,
.editor-shell,
.gr-accordion {
    border: 1.5px solid var(--app-line) !important;
    border-radius: 26px !important;
    background: var(--app-card) !important;
    box-shadow: none !important;
}

.surface-card,
.editor-shell {
    padding: 6px !important;
}

.surface-card h2,
.surface-card h3,
.editor-shell h2,
.editor-shell h3 {
    margin-top: 0 !important;
}

.card-title p,
.card-title h3 {
    margin-bottom: 8px !important;
}

.gr-button {
    min-height: 46px !important;
    border-radius: 999px !important;
    border: 1.5px solid var(--app-line) !important;
    box-shadow: none !important;
    font-weight: 700 !important;
}

.gr-button-primary {
    background: #111111 !important;
    color: #ffffff !important;
}

.gr-button-secondary {
    background: #ffffff !important;
    color: #111111 !important;
}

input,
textarea,
select {
    border-radius: 18px !important;
    border: 1.5px solid var(--app-line) !important;
    background: #ffffff !important;
    box-shadow: none !important;
}

.compact-note p {
    color: var(--app-muted);
    font-size: 0.92rem;
}

.result-panel .wrap,
.status-panel .wrap,
.quality-panel .wrap {
    min-height: 160px;
}

.download-panel .wrap {
    min-height: 112px;
}

.editor-shell textarea {
    font-size: 0.98rem !important;
    line-height: 1.65 !important;
}

.muted-box textarea {
    background: #fafaf8 !important;
}

@media (max-width: 960px) {
    .hero-banner {
        padding: 22px 20px;
        border-radius: 24px;
    }

    .hero-banner h1 {
        font-size: 1.7rem;
    }
}
"""

HERO_HTML = """
<section class="hero-banner">
  <div class="hero-kicker">LOCAL YOUTUBE SUBTITLE TOOL</div>
  <div>
    <h1>把英文长视频变成可直接观看的中文字幕</h1>
    <p>先粘贴链接，再分析和生成字幕。需要时再打开下方编辑器逐句微调，整个流程尽量保持简单、直观、少步骤。</p>
  </div>
  <div class="hero-steps">
    <span class="hero-step">1. 粘贴 YouTube 链接</span>
    <span class="hero-step">2. 生成中文字幕</span>
    <span class="hero-step">3. 播放、下载或导出视频</span>
  </div>
</section>
"""


def _reload_pipeline() -> Settings:
    global pipeline
    fresh_settings = Settings.load()
    pipeline = SubtitlePipeline(fresh_settings)
    generation_manager.bind_pipeline(pipeline)
    return fresh_settings


def _environment_markdown(current_settings: Settings) -> str:
    report = inspect_environment(current_settings)
    lines = [
        "## 应用状态",
        "",
        f"- 当前状态：{report['overall_status']}",
        f"- 数据目录：{report['data_root']}",
        f"- 配置文件：{report['config_path']}",
        f"- 日志文件：{report['logs_path']}",
        "",
        "### 环境检测",
    ]
    for item in report["checks"]:
        lines.append(f"- [{item['status']}] {item['name']}：{item['message']}")
    return "\n".join(lines)


def refresh_application_environment() -> tuple[str, str, str, str, dict]:
    current_settings = _reload_pipeline()
    report = inspect_environment(current_settings)
    status = "应用环境已刷新。" if report["overall_status"] == "可开始使用" else f"当前状态：{report['overall_status']}"
    generate_button = gr.update(interactive=bool(current_settings.deepseek_api_key))
    return (
        _environment_markdown(current_settings),
        str(current_settings.data_root),
        str(current_settings.config_path),
        status,
        generate_button,
    )


def save_application_configuration(api_key: str) -> tuple[str, str, str, str, str, dict]:
    save_user_settings(deepseek_api_key=api_key)
    current_settings = _reload_pipeline()
    status = (
        "配置已保存。已重新加载应用设置。"
        if current_settings.deepseek_api_key
        else "配置已保存，但仍未填写 DeepSeek API key。"
    )
    generate_button = gr.update(interactive=bool(current_settings.deepseek_api_key))
    return (
        _environment_markdown(current_settings),
        str(current_settings.data_root),
        str(current_settings.config_path),
        current_settings.deepseek_api_key or "",
        status,
        generate_button,
    )


def _strategy_text(manual_lang: str | None, automatic_lang: str | None) -> str:
    if manual_lang:
        return f"优先使用人工英文字幕：{manual_lang}"
    if automatic_lang:
        return f"未检测到人工英文字幕，将优先使用 YouTube 自动英文字幕：{automatic_lang}"
    return "未检测到英文字幕，将回退到本地转写。"


def _render_status(log_lines: list[str], final_message: str | None = None) -> str:
    title = final_message or "处理中"
    recent_lines = log_lines[-16:]
    body = "\n".join(f"- {line}" for line in recent_lines)
    return f"## {title}\n\n{body}" if body else f"## {title}"


def _video_overview_markdown(
    title: str = "",
    duration: str = "",
    strategy: str = "",
) -> str:
    if not title and not duration and not strategy:
        return (
            "### 视频概览\n\n"
            "- 先点击“分析视频”，这里会显示视频标题、时长和当前会采用的字幕来源策略。"
        )

    lines = ["### 视频概览", ""]
    if title:
        lines.append(f"- 标题：{title}")
    if duration:
        lines.append(f"- 时长：{duration}")
    if strategy:
        lines.append(f"- 字幕来源：{strategy}")
    return "\n".join(lines)


def _tracking_payload(snapshot: GenerationJobSnapshot | None = None) -> dict:
    if snapshot is None:
        return {"job_id": "", "work_dir": "", "status": "idle"}
    return {
        "job_id": snapshot.job_id,
        "work_dir": snapshot.work_dir,
        "status": snapshot.status,
    }


def _build_controls(
    style_preset: str,
    glossary_text: str,
    protected_terms_text: str,
) -> TranslationControlConfig:
    settings = Settings(
        translation_style_preset=(style_preset or "default").strip() or "default",
        translation_glossary_json=glossary_text.strip(),
        translation_protected_terms_json=protected_terms_text.strip(),
    )
    return settings.translation_controls()


def _empty_state(
    *,
    metadata_title: str = "",
    duration_seconds: int | None = None,
    controls: TranslationControlConfig | None = None,
    glossary_text: str = "",
    protected_terms_text: str = "",
) -> dict:
    controls = controls or TranslationControlConfig()
    return {
        "video_id": "",
        "title": metadata_title,
        "source_kind": "",
        "video_path": "",
        "english_subtitle_path": "",
        "chinese_subtitle_path": "",
        "chinese_ass_path": "",
        "bilingual_ass_path": "",
        "burned_chinese_video_path": "",
        "burned_bilingual_video_path": "",
        "quality_report_path": "",
        "duration_seconds": str(duration_seconds) if duration_seconds is not None else "",
        "work_dir": "",
        "translation_controls": controls.to_dict(),
        "translation_style_preset": controls.style_preset,
        "translation_glossary_text": glossary_text,
        "translation_protected_terms_text": protected_terms_text,
        "quality_report": {},
        "edited_cue_ids": [],
    }


def _artifact_path(path) -> str | None:
    return str(path) if path else None


def _profile_markdown(controls: TranslationControlConfig) -> str:
    preset = DeepSeekTranslator.get_style_preset(controls.style_preset)
    lines = [
        "## 翻译配置",
        "",
        f"- 风格预设：{preset.label}",
        f"- 风格说明：{preset.description}",
        f"- 术语表条数：{len(controls.glossary)}",
        f"- 保留词数量：{len(controls.protected_terms)}",
    ]
    if controls.glossary:
        preview = "；".join(f"{entry.source} -> {entry.target}" for entry in controls.glossary[:3])
        lines.append(f"- 术语表示例：{preview}")
    if controls.protected_terms:
        preview = "、".join(controls.protected_terms[:5])
        lines.append(f"- 保留词示例：{preview}")
    return "\n".join(lines)


def _quality_markdown(state: dict) -> str:
    report = state.get("quality_report") or {}
    if not report:
        return "## 质量质检\n\n- 暂无质检结果。生成字幕后会自动输出质量报告。"

    lines = [
        "## 质量质检",
        "",
        f"- 总字幕条数：{report.get('total_cues', 0)}",
        f"- 问题总数：{report.get('issue_count', 0)}",
        f"- 错误：{report.get('error_count', 0)}",
        f"- 警告：{report.get('warning_count', 0)}",
        f"- 残留英文：{report.get('leftover_english_count', 0)}",
        f"- 长行：{report.get('long_line_count', 0)}",
        f"- 时间重叠：{report.get('overlap_count', 0)}",
        f"- 术语问题：{report.get('terminology_count', 0)}",
    ]
    issues = report.get("issues") or []
    if issues:
        lines.extend(["", "### 重点问题"])
        for issue in issues[:6]:
            cue_ids = issue.get("cue_ids") or []
            cue_label = f"（cue: {', '.join(str(item) for item in cue_ids[:4])}）" if cue_ids else ""
            lines.append(f"- [{issue.get('severity', 'warning')}] {issue.get('message', '')}{cue_label}")
    return "\n".join(lines)


def _artifact_outputs(
    artifacts: JobArtifacts | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None, dict, dict, dict]:
    if artifacts is None or artifacts.video_path is None or artifacts.chinese_subtitle_path is None:
        disabled = gr.update(interactive=False)
        return None, None, None, None, None, disabled, disabled, disabled

    enabled = gr.update(interactive=True)
    return (
        _artifact_path(artifacts.video_path),
        _artifact_path(artifacts.chinese_subtitle_path),
        _artifact_path(artifacts.quality_report_path),
        _artifact_path(artifacts.burned_chinese_video_path),
        _artifact_path(artifacts.burned_bilingual_video_path),
        enabled,
        enabled,
        enabled,
    )


def _state_from_artifacts(
    artifacts: JobArtifacts,
    *,
    controls: TranslationControlConfig,
    glossary_text: str,
    protected_terms_text: str,
) -> dict:
    state = load_job_state(artifacts.work_dir) or artifacts.to_state()
    state["translation_controls"] = state.get("translation_controls") or controls.to_dict()
    state["translation_style_preset"] = controls.style_preset
    state["translation_glossary_text"] = glossary_text
    state["translation_protected_terms_text"] = protected_terms_text
    state["edited_cue_ids"] = state.get("edited_cue_ids") or []
    return state


def _issue_map(state: dict) -> dict[int, list[dict]]:
    issues_by_cue: dict[int, list[dict]] = {}
    report = state.get("quality_report") or {}
    for issue in report.get("issues") or []:
        for cue_id in issue.get("cue_ids") or []:
            try:
                normalized_id = int(cue_id)
            except (TypeError, ValueError):
                continue
            issues_by_cue.setdefault(normalized_id, []).append(issue)
    return issues_by_cue


def _translated_cues(state: dict) -> list[SubtitleCue]:
    return deserialize_cues(state.get("translated_cues"))


def _edited_cue_ids(state: dict) -> set[int]:
    return {
        int(item)
        for item in (state.get("edited_cue_ids") or [])
        if str(item).strip().isdigit()
    }


def _cue_matches_filter(
    cue: SubtitleCue,
    filter_mode: str,
    *,
    issue_map: dict[int, list[dict]],
    edited_ids: set[int],
) -> bool:
    if filter_mode == EDITOR_FILTER_ISSUES:
        return cue.id in issue_map
    if filter_mode == EDITOR_FILTER_EDITED:
        return cue.id in edited_ids
    return True


def _cue_label(cue: SubtitleCue, *, issue_count: int, edited: bool) -> str:
    prefix: list[str] = []
    if issue_count:
        prefix.append(f"问题{issue_count}")
    if edited:
        prefix.append("已调整")
    badge = f"[{' / '.join(prefix)}] " if prefix else ""
    preview = cue.source_text.replace("\n", " ").strip()
    if len(preview) > 52:
        preview = f"{preview[:49]}..."
    timestamp = seconds_to_srt_timestamp(cue.start).replace(",", ".")
    return f"{badge}#{cue.id} {timestamp} {preview}"


def _context_text(cue: SubtitleCue | None) -> str:
    if cue is None:
        return ""
    source = cue.source_text.replace("\n", " ").strip()
    target = cue.target_text.replace("\n", " ").strip()
    timestamp = seconds_to_srt_timestamp(cue.start).replace(",", ".")
    lines = [f"{timestamp} | {source}"]
    if target:
        lines.append(target)
    return "\n".join(lines)


def _selected_cue_id(state: dict, filter_mode: str, requested_cue_id: str | int | None) -> int | None:
    cues = _translated_cues(state)
    issue_map = _issue_map(state)
    edited_ids = _edited_cue_ids(state)
    filtered_ids = [
        cue.id
        for cue in cues
        if _cue_matches_filter(cue, filter_mode, issue_map=issue_map, edited_ids=edited_ids)
    ]
    if not filtered_ids:
        return None
    if requested_cue_id is not None and str(requested_cue_id).strip():
        try:
            normalized = int(requested_cue_id)
        except (TypeError, ValueError):
            normalized = None
        if normalized in filtered_ids:
            return normalized
    return filtered_ids[0]


def _editor_panel_updates(
    state: dict,
    filter_mode: str,
    selected_cue_id: str | int | None,
    *,
    message: str = "",
) -> tuple[str, dict, str, str, str, dict, str, dict, dict, str]:
    cues = _translated_cues(state)
    if not cues:
        return (
            "## 字幕编辑器\n\n- 生成中文字幕后，这里会显示可编辑的字幕句子。",
            gr.update(choices=[], value=None, interactive=False),
            "## 当前句问题\n\n- 暂无可编辑句子。",
            "",
            "",
            gr.update(value="", interactive=False),
            "",
            gr.update(interactive=False),
            gr.update(interactive=False),
            message or "字幕生成完成后即可在这里做单句修改和重译。",
        )

    issue_map = _issue_map(state)
    edited_ids = _edited_cue_ids(state)
    filtered_cues = [
        cue
        for cue in cues
        if _cue_matches_filter(cue, filter_mode, issue_map=issue_map, edited_ids=edited_ids)
    ]
    if not filtered_cues:
        return (
            "## 字幕编辑器\n\n- 当前筛选结果为空。可以切回“全部字幕”查看完整句子列表。",
            gr.update(choices=[], value=None, interactive=False),
            "## 当前句问题\n\n- 当前筛选结果为空。",
            "",
            "",
            gr.update(value="", interactive=False),
            "",
            gr.update(interactive=False),
            gr.update(interactive=False),
            message or "当前筛选条件下没有可编辑句子。",
        )

    selected_id = _selected_cue_id(state, filter_mode, selected_cue_id)
    if selected_id is None:
        selected_id = filtered_cues[0].id

    choices = [
        (
            _cue_label(cue, issue_count=len(issue_map.get(cue.id, [])), edited=cue.id in edited_ids),
            str(cue.id),
        )
        for cue in filtered_cues
    ]
    cue_index_map = {cue.id: index for index, cue in enumerate(cues)}
    current_index = cue_index_map[selected_id]
    current_cue = cues[current_index]
    previous_cue = cues[current_index - 1] if current_index > 0 else None
    next_cue = cues[current_index + 1] if current_index + 1 < len(cues) else None

    issue_lines = ["## 当前句问题", ""]
    cue_issues = issue_map.get(current_cue.id, [])
    if cue_issues:
        for issue in cue_issues:
            issue_lines.append(f"- [{issue.get('severity', 'warning')}] {issue.get('message', '')}")
    else:
        issue_lines.append("- 当前句未检测到明显问题。")

    summary_lines = [
        "## 字幕编辑器",
        "",
        f"- 当前可编辑句子：{len(filtered_cues)} / {len(cues)}",
        f"- 质量问题句：{len(issue_map)}",
        f"- 已调整句子：{len(edited_ids)}",
        f"- 当前筛选：{filter_mode}",
    ]

    return (
        "\n".join(summary_lines),
        gr.update(choices=choices, value=str(current_cue.id), interactive=True),
        "\n".join(issue_lines),
        _context_text(previous_cue),
        current_cue.source_text,
        gr.update(value=current_cue.target_text, interactive=True),
        _context_text(next_cue),
        gr.update(interactive=True),
        gr.update(interactive=True),
        message or f"正在编辑第 {current_cue.id} 条字幕。",
    )


def _editor_action_outputs(
    *,
    artifacts: JobArtifacts,
    state: dict,
    filter_mode: str,
    selected_cue_id: int | None,
    status_title: str,
    status_lines: list[str],
    editor_message: str,
) -> tuple[str, str, str | None, str | None, str | None, dict, dict, dict, dict, str, dict, str, str, dict, str, dict, dict, str]:
    quality_file = state.get("quality_report_path") or None
    burned_chinese_file = state.get("burned_chinese_video_path") or None
    burned_bilingual_file = state.get("burned_bilingual_video_path") or None
    _, _, _, _, _, open_button, export_cn_button, export_bi_button = _artifact_outputs(artifacts)
    editor_outputs = _editor_panel_updates(state, filter_mode, selected_cue_id, message=editor_message)
    return (
        _render_status(status_lines, status_title),
        _quality_markdown(state),
        quality_file,
        burned_chinese_file,
        burned_bilingual_file,
        state,
        open_button,
        export_cn_button,
        export_bi_button,
        *editor_outputs,
    )


def _state_from_snapshot(snapshot: GenerationJobSnapshot) -> dict:
    controls = TranslationControlConfig.from_dict(snapshot.translation_controls)
    if snapshot.work_dir:
        persisted = load_job_state(Path(snapshot.work_dir))
        if persisted:
            persisted["translation_controls"] = persisted.get("translation_controls") or controls.to_dict()
            persisted["translation_style_preset"] = controls.style_preset
            persisted["translation_glossary_text"] = snapshot.glossary_text
            persisted["translation_protected_terms_text"] = snapshot.protected_terms_text
            persisted["edited_cue_ids"] = persisted.get("edited_cue_ids") or []
            return persisted
    return _empty_state(
        metadata_title=snapshot.title,
        duration_seconds=snapshot.duration_seconds,
        controls=controls,
        glossary_text=snapshot.glossary_text,
        protected_terms_text=snapshot.protected_terms_text,
    )


def _job_snapshot_outputs(
    snapshot: GenerationJobSnapshot | None,
    tracked_job: dict | None,
) -> tuple:
    tracked_job = tracked_job or _tracking_payload()
    api_ready = bool(pipeline.settings.deepseek_api_key)
    generate_update = gr.update(interactive=api_ready)
    analyze_update = gr.update(interactive=True)

    if snapshot is None:
        skip = gr.skip()
        return (
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            tracked_job,
            generate_update,
            analyze_update,
        )

    if snapshot.status in {"running", "pending"}:
        generate_update = gr.update(interactive=False)
        analyze_update = gr.update(interactive=False)

    if snapshot.status in {"completed", "failed"} and tracked_job.get("status") == snapshot.status:
        skip = gr.skip()
        return (
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            skip,
            _tracking_payload(snapshot),
            generate_update,
            analyze_update,
        )

    state = _state_from_snapshot(snapshot)
    controls = TranslationControlConfig.from_dict(
        state.get("translation_controls") or snapshot.translation_controls
    )
    artifacts = artifacts_from_state(state)
    if artifacts is not None:
        artifacts = pipeline.ensure_subtitle_artifacts(artifacts)
        artifacts = pipeline.ensure_quality_report(artifacts)
        state = _state_from_artifacts(
            artifacts,
            controls=controls,
            glossary_text=snapshot.glossary_text,
            protected_terms_text=snapshot.protected_terms_text,
        )

    if snapshot.status == "running":
        status_lines = [f"总体进度：{max(1, round(snapshot.progress * 100))}%"] + list(snapshot.log_lines)
        editor_outputs = _editor_panel_updates(
            _empty_state(
                metadata_title=snapshot.title,
                duration_seconds=snapshot.duration_seconds,
                controls=controls,
                glossary_text=snapshot.glossary_text,
                protected_terms_text=snapshot.protected_terms_text,
            ),
            EDITOR_FILTER_ALL,
            None,
            message="后台任务进行中。等任务完成后，这里会自动恢复可编辑状态。",
        )
        video_file = subtitle_file = quality_file = burned_cn_file = burned_bi_file = None
        open_button = export_cn_button = export_bi_button = gr.update(interactive=False)
    elif snapshot.status == "failed":
        status_lines = list(snapshot.log_lines)
        if snapshot.error:
            status_lines.append(f"失败原因：{snapshot.error}")
        editor_outputs = _editor_panel_updates(
            _empty_state(
                metadata_title=snapshot.title,
                duration_seconds=snapshot.duration_seconds,
                controls=controls,
                glossary_text=snapshot.glossary_text,
                protected_terms_text=snapshot.protected_terms_text,
            ),
            EDITOR_FILTER_ALL,
            None,
            message="任务失败。修复问题后，可以重新点击“生成中文字幕”。",
        )
        video_file = subtitle_file = quality_file = burned_cn_file = burned_bi_file = None
        open_button = export_cn_button = export_bi_button = gr.update(interactive=False)
    else:
        status_lines = list(snapshot.log_lines)
        (
            video_file,
            subtitle_file,
            quality_file,
            burned_cn_file,
            burned_bi_file,
            open_button,
            export_cn_button,
            export_bi_button,
        ) = _artifact_outputs(artifacts)
        editor_outputs = _editor_panel_updates(
            state,
            EDITOR_FILTER_ALL,
            None,
            message="任务完成。现在可以直接播放、导出，或进入逐句编辑。",
        )

    status_title = {
        "running": "后台处理中",
        "failed": "处理失败",
        "completed": "处理完成",
    }.get(snapshot.status, "任务状态")

    return (
        _video_overview_markdown(
            snapshot.title,
            format_duration(snapshot.duration_seconds),
            snapshot.strategy_text,
        ),
        _profile_markdown(controls),
        snapshot.thumbnail_url,
        _render_status(status_lines, status_title),
        _quality_markdown(state),
        video_file,
        subtitle_file,
        quality_file,
        burned_cn_file,
        burned_bi_file,
        state,
        open_button,
        export_cn_button,
        export_bi_button,
        *editor_outputs,
        _tracking_payload(snapshot),
        generate_update,
        analyze_update,
    )


def restore_generation_view(tracked_job: dict | None) -> tuple:
    snapshot = generation_manager.get_current_snapshot()
    return _job_snapshot_outputs(snapshot, tracked_job)


def poll_generation_view(tracked_job: dict | None) -> tuple:
    snapshot = generation_manager.get_current_snapshot()
    return _job_snapshot_outputs(snapshot, tracked_job)


def start_background_generation(
    url: str,
    style_preset: str,
    glossary_text: str,
    protected_terms_text: str,
) -> tuple:
    if not url.strip():
        raise gr.Error("请输入 YouTube 链接。")
    if not pipeline.settings.deepseek_api_key:
        raise gr.Error("请先在“应用设置与环境”里填写 DeepSeek API key。")

    controls = _build_controls(style_preset, glossary_text, protected_terms_text)
    metadata = pipeline.analyze(url)
    snapshot = generation_manager.start_generation(
        url=url,
        metadata=metadata,
        strategy_text=_strategy_text(
            metadata.manual_english_subtitle_lang,
            metadata.automatic_english_subtitle_lang,
        ),
        controls=controls,
        glossary_text=glossary_text,
        protected_terms_text=protected_terms_text,
    )
    return _job_snapshot_outputs(snapshot, _tracking_payload())


def analyze_url(
    url: str,
    style_preset: str,
    glossary_text: str,
    protected_terms_text: str,
) -> tuple:
    if not url.strip():
        raise gr.Error("请输入 YouTube 链接。")

    controls = _build_controls(style_preset, glossary_text, protected_terms_text)
    metadata = pipeline.analyze(url)
    existing_artifacts = pipeline.find_existing_artifacts(metadata)
    state = _empty_state(
        metadata_title=metadata.title,
        duration_seconds=metadata.duration_seconds,
        controls=controls,
        glossary_text=glossary_text,
        protected_terms_text=protected_terms_text,
    )

    if existing_artifacts is not None:
        existing_artifacts = pipeline.ensure_subtitle_artifacts(existing_artifacts)
        existing_artifacts = pipeline.ensure_quality_report(existing_artifacts)
        state = _state_from_artifacts(
            existing_artifacts,
            controls=controls,
            glossary_text=glossary_text,
            protected_terms_text=protected_terms_text,
        )
        if pipeline._controls_match_state(state, controls):
            status = (
                "## 分析完成\n\n"
                "- 已读取视频标题、时长和字幕策略\n"
                "- 检测到本地已有处理结果\n"
                "- 当前翻译配置与已有结果一致，可直接播放、导出或进入字幕编辑器"
            )
        else:
            status = (
                "## 分析完成\n\n"
                "- 已读取视频标题、时长和字幕策略\n"
                "- 检测到本地已有结果，但它使用的是旧翻译配置\n"
                "- 点击“生成中文字幕”后会基于当前风格预设、术语表和保留词重新生成"
            )
    else:
        status = (
            "## 分析完成\n\n"
            "- 已读取视频标题、时长和字幕策略\n"
            "- 可以开始生成中文字幕\n"
            "- 建议先确认风格预设、术语表和保留词"
        )

    video_file, subtitle_file, quality_file, burned_cn_file, burned_bi_file, open_button, export_cn_button, export_bi_button = _artifact_outputs(existing_artifacts)
    editor_outputs = _editor_panel_updates(
        state,
        EDITOR_FILTER_ALL,
        None,
        message="如果已有字幕结果，这里可以直接开始单句修改或重译。",
    )
    return (
        _video_overview_markdown(
            metadata.title,
            format_duration(metadata.duration_seconds),
            _strategy_text(metadata.manual_english_subtitle_lang, metadata.automatic_english_subtitle_lang),
        ),
        _profile_markdown(controls),
        metadata.thumbnail_url,
        status,
        _quality_markdown(state),
        video_file,
        subtitle_file,
        quality_file,
        burned_cn_file,
        burned_bi_file,
        state,
        open_button,
        export_cn_button,
        export_bi_button,
        *editor_outputs,
        _tracking_payload(
            GenerationJobSnapshot(
                job_id="",
                url=url,
                title=metadata.title,
                duration_seconds=metadata.duration_seconds,
                strategy_text=_strategy_text(
                    metadata.manual_english_subtitle_lang,
                    metadata.automatic_english_subtitle_lang,
                ),
                thumbnail_url=metadata.thumbnail_url,
                work_dir=str(existing_artifacts.work_dir) if existing_artifacts is not None else "",
                status="completed" if existing_artifacts is not None else "idle",
                translation_controls=controls.to_dict(),
                glossary_text=glossary_text,
                protected_terms_text=protected_terms_text,
            )
            if existing_artifacts is not None
            else None
        ),
        gr.update(interactive=bool(pipeline.settings.deepseek_api_key)),
        gr.update(interactive=True),
    )


def generate_subtitle(
    url: str,
    style_preset: str,
    glossary_text: str,
    protected_terms_text: str,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> Generator[
    tuple[
        str,
        str,
        str | None,
        str | None,
        str | None,
        str | None,
        str | None,
        dict,
        dict,
        dict,
        dict,
        str,
        dict,
        str,
        str,
        str,
        dict,
        str,
        dict,
        dict,
        str,
    ],
    None,
    None,
]:
    if not url.strip():
        raise gr.Error("请输入 YouTube 链接。")
    if not pipeline.settings.deepseek_api_key:
        raise gr.Error("请先在“应用设置与环境”里填写 DeepSeek API key。")

    controls = _build_controls(style_preset, glossary_text, protected_terms_text)
    state = _empty_state(
        controls=controls,
        glossary_text=glossary_text,
        protected_terms_text=protected_terms_text,
    )
    log_lines = [
        "任务已提交，开始处理。",
        f"当前风格预设：{DeepSeekTranslator.get_style_preset(controls.style_preset).label}",
    ]
    disabled = gr.update(interactive=False)
    empty_editor = _editor_panel_updates(
        state,
        EDITOR_FILTER_ALL,
        None,
        message="字幕生成完成后，这里会自动切换到可编辑状态。",
    )
    yield (
        _render_status(log_lines),
        _quality_markdown(state),
        None,
        None,
        None,
        None,
        None,
        state,
        disabled,
        disabled,
        disabled,
        *empty_editor,
    )

    try:
        for event in pipeline.generate_events(url, controls=controls):
            progress(event.progress, desc=event.message)
            log_lines.append(event.message)
            if event.artifacts is None:
                yield (
                    _render_status(log_lines),
                    _quality_markdown(state),
                    None,
                    None,
                    None,
                    None,
                    None,
                    state,
                    disabled,
                    disabled,
                    disabled,
                    *empty_editor,
                )
                continue

            artifacts = event.artifacts
            state = _state_from_artifacts(
                artifacts,
                controls=controls,
                glossary_text=glossary_text,
                protected_terms_text=protected_terms_text,
            )
            (
                video_file,
                subtitle_file,
                quality_file,
                burned_cn_file,
                burned_bi_file,
                open_button,
                export_cn_button,
                export_bi_button,
            ) = _artifact_outputs(artifacts)
            final_log = log_lines + [
                f"来源：{artifacts.source_kind}",
                f"视频：{artifacts.video_path}",
                f"字幕：{artifacts.chinese_subtitle_path}",
                "当前结果已附带质量报告，可直接播放、导出或进入字幕编辑器。",
            ]
            editor_outputs = _editor_panel_updates(
                state,
                EDITOR_FILTER_ALL,
                None,
                message="字幕生成完成，现在可以做单句保存或单句重译。",
            )
            yield (
                _render_status(final_log, "处理完成"),
                _quality_markdown(state),
                video_file,
                subtitle_file,
                quality_file,
                burned_cn_file,
                burned_bi_file,
                state,
                open_button,
                export_cn_button,
                export_bi_button,
                *editor_outputs,
            )
    except Exception as exc:
        logger.exception("Subtitle generation failed for url=%s", url)
        log_lines.append(f"处理失败：{exc}")
        yield (
            _render_status(log_lines, "处理失败"),
            _quality_markdown(state),
            None,
            None,
            None,
            None,
            None,
            state,
            disabled,
            disabled,
            disabled,
            *empty_editor,
        )


def export_video(
    state: dict,
    *,
    bilingual: bool,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> Generator[tuple[str, str, str | None, str | None, str | None, dict], None, None]:
    if not state:
        raise gr.Error("还没有可导出的任务结果，请先生成中文字幕。")

    label = "双语 MP4" if bilingual else "中文字幕 MP4"
    log_lines = [f"开始导出 {label}。"]
    burned_cn_file = state.get("burned_chinese_video_path") or None
    burned_bi_file = state.get("burned_bilingual_video_path") or None
    quality_file = state.get("quality_report_path") or None
    yield _render_status(log_lines), _quality_markdown(state), quality_file, burned_cn_file, burned_bi_file, state

    try:
        for event in pipeline.export_video_events(state, bilingual=bilingual):
            progress(event.progress, desc=event.message)
            log_lines.append(event.message)
            if event.artifacts is not None:
                state = load_job_state(event.artifacts.work_dir) or event.artifacts.to_state()
                burned_cn_file = state.get("burned_chinese_video_path") or None
                burned_bi_file = state.get("burned_bilingual_video_path") or None
                quality_file = state.get("quality_report_path") or None
            yield (
                _render_status(log_lines, f"{label} 导出中"),
                _quality_markdown(state),
                quality_file,
                burned_cn_file,
                burned_bi_file,
                state,
            )
    except Exception as exc:
        logger.exception("Video export failed. bilingual=%s", bilingual)
        log_lines.append(f"{label} 导出失败：{exc}")
        yield (
            _render_status(log_lines, f"{label} 导出失败"),
            _quality_markdown(state),
            quality_file,
            burned_cn_file,
            burned_bi_file,
            state,
        )


def export_chinese_video(
    state: dict,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> Generator[tuple[str, str, str | None, str | None, str | None, dict], None, None]:
    yield from export_video(state, bilingual=False, progress=progress)


def export_bilingual_video(
    state: dict,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> Generator[tuple[str, str, str | None, str | None, str | None, dict], None, None]:
    yield from export_video(state, bilingual=True, progress=progress)


def refresh_editor(
    state: dict,
    filter_mode: str,
    selected_cue_id: str | None,
) -> tuple[str, dict, str, str, str, dict, str, dict, dict, str]:
    return _editor_panel_updates(state, filter_mode or EDITOR_FILTER_ALL, selected_cue_id)


def save_current_cue(
    state: dict,
    selected_cue_id: str | None,
    target_text: str,
    filter_mode: str,
) -> tuple[str, str, str | None, str | None, str | None, dict, dict, dict, dict, str, dict, str, str, dict, str, dict, dict, str]:
    if not state:
        raise gr.Error("还没有可编辑的任务结果，请先生成中文字幕。")
    if not selected_cue_id:
        raise gr.Error("请先选择要修改的字幕句子。")

    cue_id = int(selected_cue_id)
    artifacts = pipeline.update_cue_translation(state, cue_id, target_text)
    new_state = load_job_state(artifacts.work_dir) or artifacts.to_state()
    status_lines = [
        f"已保存第 {cue_id} 条字幕。",
        "已重建中文字幕、双语 ASS 和质量报告。",
        "如果之前导出过烧录视频，请按需重新导出最新版本。",
    ]
    return _editor_action_outputs(
        artifacts=artifacts,
        state=new_state,
        filter_mode=filter_mode or EDITOR_FILTER_ALL,
        selected_cue_id=cue_id,
        status_title="字幕已更新",
        status_lines=status_lines,
        editor_message=f"第 {cue_id} 条字幕已保存。",
    )


def retranslate_current_cue(
    state: dict,
    selected_cue_id: str | None,
    filter_mode: str,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> tuple[str, str, str | None, str | None, str | None, dict, dict, dict, dict, str, dict, str, str, dict, str, dict, dict, str]:
    if not state:
        raise gr.Error("还没有可编辑的任务结果，请先生成中文字幕。")
    if not selected_cue_id:
        raise gr.Error("请先选择要重译的字幕句子。")

    cue_id = int(selected_cue_id)
    progress(0.15, desc="载入当前句子")
    artifacts = pipeline.retranslate_cue(state, cue_id)
    progress(0.85, desc="重建字幕与质量报告")
    new_state = load_job_state(artifacts.work_dir) or artifacts.to_state()
    progress(1.0, desc="单句重译完成")
    status_lines = [
        f"已重译第 {cue_id} 条字幕。",
        "已同步重建中文字幕、双语 ASS 和质量报告。",
        "如果之前导出过烧录视频，请按需重新导出最新版本。",
    ]
    return _editor_action_outputs(
        artifacts=artifacts,
        state=new_state,
        filter_mode=filter_mode or EDITOR_FILTER_ALL,
        selected_cue_id=cue_id,
        status_title="单句重译完成",
        status_lines=status_lines,
        editor_message=f"第 {cue_id} 条字幕已按当前任务配置重新翻译。",
    )


def open_player(state: dict, bilingual_mode: bool) -> str:
    if not state:
        raise gr.Error("还没有可播放的结果，请先生成中文字幕。")
    artifacts = JobArtifacts.from_state(state)
    video_path, subtitle_path = pipeline.prepare_player_paths(artifacts, bilingual=bilingual_mode)
    return pipeline.player.open_with_subtitle(video_path, subtitle_path)


def create_app() -> gr.Blocks:
    with gr.Blocks(
        title="本地 YouTube 字幕翻译器",
        theme=gr.themes.Monochrome(),
        css=APP_CSS,
        fill_width=True,
    ) as app:
        current_settings = pipeline.settings
        default_controls = current_settings.translation_controls()
        initial_state = _empty_state(
            controls=default_controls,
            glossary_text=current_settings.translation_glossary_json,
            protected_terms_text=current_settings.translation_protected_terms_json,
        )
        state = gr.State(initial_state)
        tracked_job = gr.State(_tracking_payload())
        job_poll_timer = gr.Timer(value=2.0, active=True)

        gr.HTML(HERO_HTML)

        with gr.Row(equal_height=False):
            with gr.Column(scale=8):
                with gr.Group(elem_classes=["surface-card"]):
                    gr.Markdown("### 第 1 步 · 输入视频链接", elem_classes=["card-title"])
                    gr.Markdown(
                        "先分析，再生成。第一次使用前，只需要在右侧保存一次 DeepSeek API Key。",
                        elem_classes=["compact-note"],
                    )
                    url_input = gr.Textbox(
                        label="YouTube 链接",
                        placeholder="https://www.youtube.com/watch?v=...",
                        info="支持直接粘贴完整 YouTube 视频链接。",
                    )
                    with gr.Row():
                        analyze_button = gr.Button("分析视频", variant="secondary")
                        generate_button = gr.Button(
                            "生成中文字幕",
                            variant="primary",
                            interactive=bool(current_settings.deepseek_api_key),
                        )

                with gr.Row(equal_height=True):
                    with gr.Column(scale=5):
                        with gr.Group(elem_classes=["surface-card", "result-panel"]):
                            gr.Markdown("### 第 2 步 · 确认视频信息", elem_classes=["card-title"])
                            video_overview_output = gr.Markdown(_video_overview_markdown())
                            thumbnail_output = gr.Image(
                                label="视频封面",
                                interactive=False,
                                show_download_button=False,
                                show_share_button=False,
                                height=260,
                            )

                    with gr.Column(scale=5):
                        with gr.Group(elem_classes=["surface-card", "status-panel"]):
                            gr.Markdown("### 当前进度", elem_classes=["card-title"])
                            status_output = gr.Markdown(
                                _render_status(["先粘贴链接，再点击“分析视频”。"], "准备就绪")
                            )

                with gr.Group(elem_classes=["surface-card"]):
                    gr.Markdown("### 第 3 步 · 播放、下载与导出", elem_classes=["card-title"])
                    gr.Markdown(
                        "生成完成后，可以直接打开本地播放器观看，也可以下载字幕文件或导出带字幕的视频。",
                        elem_classes=["compact-note"],
                    )
                    with gr.Row():
                        bilingual_mode = gr.Checkbox(
                            label="打开播放器时使用双语字幕（上英下中）",
                            value=False,
                        )
                        open_button = gr.Button("打开本地播放器", interactive=False)
                    with gr.Row():
                        export_chinese_button = gr.Button("导出中文字幕 MP4", interactive=False)
                        export_bilingual_button = gr.Button("导出双语 MP4", interactive=False)
                    with gr.Row():
                        video_file = gr.File(label="原视频文件", interactive=False)
                        subtitle_file = gr.File(label="中文字幕 SRT", interactive=False)
                        quality_file = gr.File(label="质量报告", interactive=False)
                    with gr.Row():
                        burned_chinese_file = gr.File(label="中文字幕 MP4", interactive=False)
                        burned_bilingual_file = gr.File(label="双语 MP4", interactive=False)
                    player_output = gr.Textbox(
                        label="播放器状态",
                        interactive=False,
                        placeholder="打开播放器后，这里会显示执行结果。",
                    )

            with gr.Column(scale=4):
                with gr.Group(elem_classes=["surface-card"]):
                    gr.Markdown("### 首次使用设置", elem_classes=["card-title"])
                    gr.Markdown(
                        "这一栏只需要设置一次。保存 API Key 后，就可以直接开始生成字幕。",
                        elem_classes=["compact-note"],
                    )
                    api_key_input = gr.Textbox(
                        label="DeepSeek API Key",
                        value=current_settings.deepseek_api_key or "",
                        type="password",
                        placeholder="sk-...",
                    )
                    with gr.Row():
                        save_app_settings_button = gr.Button("保存 API Key", variant="primary")
                        refresh_environment_button = gr.Button("刷新环境", variant="secondary")
                    app_settings_status = gr.Textbox(
                        label="当前状态",
                        value="可开始使用" if current_settings.deepseek_api_key else "请先填写 DeepSeek API key",
                        interactive=False,
                    )
                    app_data_root_output = gr.Textbox(
                        label="数据保存目录",
                        value=str(current_settings.data_root),
                        interactive=False,
                    )
                    app_config_path_output = gr.Textbox(
                        label="配置文件路径",
                        value=str(current_settings.config_path),
                        interactive=False,
                    )

                with gr.Accordion(
                    "查看环境详情",
                    open=not bool(current_settings.deepseek_api_key),
                    elem_classes=["surface-card"],
                ):
                    app_environment_output = gr.Markdown(_environment_markdown(current_settings))

                with gr.Accordion("高级翻译设置", open=False, elem_classes=["surface-card"]):
                    gr.Markdown(
                        "默认设置已经可以直接使用。只有在你想统一术语或固定风格时，再展开这一栏。",
                        elem_classes=["compact-note"],
                    )
                    style_input = gr.Dropdown(
                        label="翻译风格",
                        choices=STYLE_CHOICES,
                        value=default_controls.style_preset,
                    )
                    glossary_input = gr.Textbox(
                        label="术语表",
                        placeholder='支持 JSON 或逐行输入，例如：Blackwell -> Blackwell 架构',
                        lines=4,
                        value=current_settings.translation_glossary_json,
                    )
                    protected_terms_input = gr.Textbox(
                        label="保留词 / 禁译词",
                        placeholder="支持逗号、分号或逐行输入，例如：CUDA, Omniverse, Blackwell",
                        lines=3,
                        value=current_settings.translation_protected_terms_json,
                    )
                    profile_output = gr.Markdown(value=_profile_markdown(default_controls))

                with gr.Group(elem_classes=["surface-card", "quality-panel"]):
                    gr.Markdown("### 自动质检", elem_classes=["card-title"])
                    quality_output = gr.Markdown(_quality_markdown(initial_state))

        with gr.Accordion("需要细修时，再打开字幕编辑器", open=False, elem_classes=["editor-shell"]):
            gr.Markdown(
                "这里适合在成品生成之后做逐句微调。新手可以先忽略这部分，等需要修句子时再展开。",
                elem_classes=["compact-note"],
            )
            with gr.Row():
                editor_filter = gr.Radio(
                    label="筛选范围",
                    choices=EDITOR_FILTER_CHOICES,
                    value=EDITOR_FILTER_ALL,
                )
                cue_selector = gr.Dropdown(
                    label="选择句子",
                    choices=[],
                    interactive=False,
                )
            with gr.Row(equal_height=True):
                with gr.Column(scale=3):
                    previous_context = gr.Textbox(
                        label="上一句参考",
                        lines=5,
                        interactive=False,
                        elem_classes=["muted-box"],
                    )
                with gr.Column(scale=4):
                    source_context = gr.Textbox(
                        label="当前英文原句",
                        lines=5,
                        interactive=False,
                    )
                    target_editor = gr.Textbox(
                        label="当前中文译文",
                        lines=6,
                        interactive=False,
                    )
                    with gr.Row():
                        save_cue_button = gr.Button("保存当前句", interactive=False)
                        retranslate_button = gr.Button("单句重译", interactive=False)
                    editor_status = gr.Textbox(label="编辑状态", interactive=False)
                with gr.Column(scale=3):
                    next_context = gr.Textbox(
                        label="下一句参考",
                        lines=5,
                        interactive=False,
                        elem_classes=["muted-box"],
                    )
            with gr.Row(equal_height=True):
                with gr.Column(scale=4):
                    cue_issue_output = gr.Markdown("## 当前句问题\n\n- 暂无可编辑句子。")
                with gr.Column(scale=6):
                    editor_summary = gr.Markdown(
                        "## 字幕编辑器\n\n- 生成中文字幕后，这里会显示可编辑的字幕句子。"
                    )

        app.load(
            fn=refresh_application_environment,
            outputs=[
                app_environment_output,
                app_data_root_output,
                app_config_path_output,
                app_settings_status,
                generate_button,
            ],
        )

        job_view_outputs = [
            video_overview_output,
            profile_output,
            thumbnail_output,
            status_output,
            quality_output,
            video_file,
            subtitle_file,
            quality_file,
            burned_chinese_file,
            burned_bilingual_file,
            state,
            open_button,
            export_chinese_button,
            export_bilingual_button,
            editor_summary,
            cue_selector,
            cue_issue_output,
            previous_context,
            source_context,
            target_editor,
            next_context,
            save_cue_button,
            retranslate_button,
            editor_status,
            tracked_job,
            generate_button,
            analyze_button,
        ]

        app.load(
            fn=restore_generation_view,
            inputs=[tracked_job],
            outputs=job_view_outputs,
        )

        save_app_settings_button.click(
            fn=save_application_configuration,
            inputs=[api_key_input],
            outputs=[
                app_environment_output,
                app_data_root_output,
                app_config_path_output,
                api_key_input,
                app_settings_status,
                generate_button,
            ],
        )

        refresh_environment_button.click(
            fn=refresh_application_environment,
            outputs=[
                app_environment_output,
                app_data_root_output,
                app_config_path_output,
                app_settings_status,
                generate_button,
            ],
        )

        analyze_button.click(
            fn=analyze_url,
            inputs=[url_input, style_input, glossary_input, protected_terms_input],
            outputs=job_view_outputs,
        )

        generate_button.click(
            fn=start_background_generation,
            inputs=[url_input, style_input, glossary_input, protected_terms_input],
            outputs=job_view_outputs,
        )

        job_poll_timer.tick(
            fn=poll_generation_view,
            inputs=[tracked_job],
            outputs=job_view_outputs,
        )

        editor_filter.change(
            fn=refresh_editor,
            inputs=[state, editor_filter, cue_selector],
            outputs=[
                editor_summary,
                cue_selector,
                cue_issue_output,
                previous_context,
                source_context,
                target_editor,
                next_context,
                save_cue_button,
                retranslate_button,
                editor_status,
            ],
        )

        cue_selector.change(
            fn=refresh_editor,
            inputs=[state, editor_filter, cue_selector],
            outputs=[
                editor_summary,
                cue_selector,
                cue_issue_output,
                previous_context,
                source_context,
                target_editor,
                next_context,
                save_cue_button,
                retranslate_button,
                editor_status,
            ],
        )

        save_cue_button.click(
            fn=save_current_cue,
            inputs=[state, cue_selector, target_editor, editor_filter],
            outputs=[
                status_output,
                quality_output,
                quality_file,
                burned_chinese_file,
                burned_bilingual_file,
                state,
                open_button,
                export_chinese_button,
                export_bilingual_button,
                editor_summary,
                cue_selector,
                cue_issue_output,
                previous_context,
                source_context,
                target_editor,
                next_context,
                save_cue_button,
                retranslate_button,
                editor_status,
            ],
        )

        retranslate_button.click(
            fn=retranslate_current_cue,
            inputs=[state, cue_selector, editor_filter],
            outputs=[
                status_output,
                quality_output,
                quality_file,
                burned_chinese_file,
                burned_bilingual_file,
                state,
                open_button,
                export_chinese_button,
                export_bilingual_button,
                editor_summary,
                cue_selector,
                cue_issue_output,
                previous_context,
                source_context,
                target_editor,
                next_context,
                save_cue_button,
                retranslate_button,
                editor_status,
            ],
        )

        open_button.click(
            fn=open_player,
            inputs=[state, bilingual_mode],
            outputs=[player_output],
        )

        export_chinese_button.click(
            fn=export_chinese_video,
            inputs=[state],
            outputs=[status_output, quality_output, quality_file, burned_chinese_file, burned_bilingual_file, state],
        )

        export_bilingual_button.click(
            fn=export_bilingual_video,
            inputs=[state],
            outputs=[status_output, quality_output, quality_file, burned_chinese_file, burned_bilingual_file, state],
        )

    return app
