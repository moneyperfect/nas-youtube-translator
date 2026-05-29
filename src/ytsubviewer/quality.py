from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Mapping, Sequence
import re

from ytsubviewer.models import SubtitleCue


_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.#/_-]*")
_TERM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b|\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b|\b[A-Z][a-z]{2,}\b")
_CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_WHITESPACE_RE = re.compile(r"\s+")

COMMON_SOURCE_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "but",
    "by",
    "can",
    "for",
    "from",
    "have",
    "he",
    "her",
    "his",
    "i",
    "in",
    "is",
    "it",
    "its",
    "like",
    "me",
    "more",
    "my",
    "of",
    "on",
    "or",
    "our",
    "out",
    "so",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "up",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "you",
}


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: str
    message: str
    cue_ids: tuple[int, ...] = ()
    start: float | None = None
    end: float | None = None
    details: dict[str, str] = field(default_factory=dict)


@dataclass
class QualityReport:
    total_cues: int
    issues: list[QualityIssue] = field(default_factory=list)
    empty_translation_count: int = 0
    leftover_english_count: int = 0
    long_line_count: int = 0
    overlap_count: int = 0
    terminology_count: int = 0

    def add_issue(self, issue: QualityIssue) -> None:
        self.issues.append(issue)
        if issue.code == "empty_translation":
            self.empty_translation_count += 1
        elif issue.code == "leftover_english":
            self.leftover_english_count += 1
        elif issue.code == "long_line":
            self.long_line_count += 1
        elif issue.code == "timing_overlap":
            self.overlap_count += 1
        elif issue.code == "terminology_inconsistent":
            self.terminology_count += 1

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def has_blockers(self) -> bool:
        return self.error_count > 0

    def to_dict(self) -> dict:
        return {
            "total_cues": self.total_cues,
            "issue_count": len(self.issues),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "empty_translation_count": self.empty_translation_count,
            "leftover_english_count": self.leftover_english_count,
            "long_line_count": self.long_line_count,
            "overlap_count": self.overlap_count,
            "terminology_count": self.terminology_count,
            "issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "cue_ids": list(issue.cue_ids),
                    "start": issue.start,
                    "end": issue.end,
                    "details": dict(issue.details),
                }
                for issue in self.issues
            ],
        }

    def summary(self) -> str:
        parts = [
            f"总字幕 {self.total_cues} 条",
            f"问题 {len(self.issues)} 个",
        ]
        if self.error_count:
            parts.append(f"错误 {self.error_count} 个")
        if self.warning_count:
            parts.append(f"警告 {self.warning_count} 个")
        return "，".join(parts)


def generate_quality_report(
    cues: Sequence[SubtitleCue],
    *,
    expected_duration: float | None = None,
    glossary: Mapping[str, Sequence[str] | str] | None = None,
    max_line_length: int = 42,
    overlap_tolerance: float = 0.15,
) -> QualityReport:
    report = QualityReport(total_cues=len(cues))
    ordered_cues = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.id))

    _collect_basic_issues(report, ordered_cues, max_line_length=max_line_length, overlap_tolerance=overlap_tolerance)
    _collect_terminology_issues(report, ordered_cues, glossary=glossary)

    if expected_duration is not None:
        _collect_duration_issue(report, ordered_cues, expected_duration)

    return report


def write_quality_report_markdown(report: QualityReport, path: Path) -> Path:
    lines = [
        "# 字幕质量报告",
        "",
        f"- 总字幕条数：{report.total_cues}",
        f"- 问题总数：{len(report.issues)}",
        f"- 错误：{report.error_count}",
        f"- 警告：{report.warning_count}",
        f"- 空字幕：{report.empty_translation_count}",
        f"- 残留英文：{report.leftover_english_count}",
        f"- 长行：{report.long_line_count}",
        f"- 时间重叠：{report.overlap_count}",
        f"- 术语问题：{report.terminology_count}",
        "",
    ]
    if not report.issues:
        lines.extend(["## 结果", "", "未检测到明显问题。", ""])
    else:
        lines.extend(["## 问题明细", ""])
        for issue in report.issues:
            cue_label = f" cue={','.join(str(item) for item in issue.cue_ids)}" if issue.cue_ids else ""
            time_label = ""
            if issue.start is not None and issue.end is not None:
                time_label = f" [{issue.start:.2f}s - {issue.end:.2f}s]"
            lines.append(f"- [{issue.severity}] {issue.code}{cue_label}{time_label} {issue.message}")
            for key, value in issue.details.items():
                lines.append(f"  - {key}: {value}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_quality_report_json(report: QualityReport, path: Path) -> Path:
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _collect_basic_issues(
    report: QualityReport,
    cues: Sequence[SubtitleCue],
    *,
    max_line_length: int,
    overlap_tolerance: float,
) -> None:
    previous_end: float | None = None
    for cue in cues:
        target_text = _normalize_text(cue.target_text)
        source_text = _normalize_text(cue.source_text)

        if not target_text:
            report.add_issue(
                QualityIssue(
                    code="empty_translation",
                    severity="error",
                    message="字幕为空白，未生成可用译文。",
                    cue_ids=(cue.id,),
                    start=cue.start,
                    end=cue.end,
                )
            )
            continue

        if _looks_like_leftover_english(target_text):
            report.add_issue(
                QualityIssue(
                    code="leftover_english",
                    severity="warning",
                    message="译文中仍保留大量英文，可能需要重译。",
                    cue_ids=(cue.id,),
                    start=cue.start,
                    end=cue.end,
                    details={"target_text": target_text, "source_text": source_text},
                )
            )

        for line_index, line in enumerate(target_text.splitlines() or [target_text], start=1):
            if len(line.strip()) > max_line_length:
                report.add_issue(
                    QualityIssue(
                        code="long_line",
                        severity="warning",
                        message="字幕行过长，不利于观看。",
                        cue_ids=(cue.id,),
                        start=cue.start,
                        end=cue.end,
                        details={
                            "line_index": str(line_index),
                            "line_length": str(len(line.strip())),
                            "max_line_length": str(max_line_length),
                        },
                    )
                )

        if previous_end is not None and cue.start < previous_end - overlap_tolerance:
            report.add_issue(
                QualityIssue(
                    code="timing_overlap",
                    severity="warning",
                    message="字幕时间轴存在重叠。",
                    cue_ids=(cue.id,),
                    start=cue.start,
                    end=cue.end,
                    details={"previous_end": f"{previous_end:.2f}"},
                )
            )

        previous_end = max(previous_end or cue.end, cue.end)


def _collect_terminology_issues(
    report: QualityReport,
    cues: Sequence[SubtitleCue],
    *,
    glossary: Mapping[str, Sequence[str] | str] | None,
) -> None:
    if glossary:
        _collect_glossary_issues(report, cues, glossary)
        return

    term_usage: dict[str, dict[str, list[int] | set[str]]] = {}
    for cue in cues:
        source_terms = _extract_candidate_terms(cue.source_text)
        if not source_terms:
            continue
        target_text = _normalize_text(cue.target_text)
        for term in source_terms:
            entry = term_usage.setdefault(term, {"retained": set(), "translated": []})
            if _term_present(target_text, term):
                entry["retained"].add(cue.id)
            else:
                entry["translated"].append(cue.id)

    for term, usage in term_usage.items():
        retained_count = len(usage["retained"])
        translated_count = len(usage["translated"])
        if retained_count and translated_count:
            report.add_issue(
                QualityIssue(
                    code="terminology_inconsistent",
                    severity="warning",
                    message=f"术语 {term} 在不同字幕中出现了混合保留与翻译。",
                    cue_ids=tuple(sorted(usage["retained"] | set(usage["translated"]))),
                    details={
                        "term": term,
                        "retained_count": str(retained_count),
                        "translated_count": str(translated_count),
                    },
                )
            )


def _collect_glossary_issues(
    report: QualityReport,
    cues: Sequence[SubtitleCue],
    glossary: Mapping[str, Sequence[str] | str],
) -> None:
    normalized_glossary: dict[str, set[str]] = {}
    for term, forms in glossary.items():
        if isinstance(forms, str):
            normalized_glossary[term] = {_normalize_text(forms)}
        else:
            normalized_glossary[term] = {_normalize_text(form) for form in forms if _normalize_text(form)}

    for source_term, allowed_forms in normalized_glossary.items():
        matched_cues: list[int] = []
        offending_cues: list[int] = []
        for cue in cues:
            source_text = _normalize_text(cue.source_text)
            target_text = _normalize_text(cue.target_text)
            if source_term.lower() not in source_text.lower():
                continue
            matched_cues.append(cue.id)
            if not any(form and form.lower() in target_text.lower() for form in allowed_forms):
                offending_cues.append(cue.id)

        if offending_cues:
            report.add_issue(
                QualityIssue(
                    code="terminology_inconsistent",
                    severity="warning",
                    message=f"术语 {source_term} 未稳定使用预设译法。",
                    cue_ids=tuple(offending_cues),
                    details={
                        "term": source_term,
                        "allowed_forms": ",".join(sorted(allowed_forms)),
                        "matched_cues": str(len(matched_cues)),
                        "offending_cues": str(len(offending_cues)),
                    },
                )
            )


def _collect_duration_issue(report: QualityReport, cues: Sequence[SubtitleCue], expected_duration: float) -> None:
    if not cues:
        report.add_issue(
            QualityIssue(
                code="empty_output",
                severity="error",
                message="字幕列表为空。",
            )
        )
        return

    last_end = max(cue.end for cue in cues)
    if last_end < expected_duration * 0.85 and expected_duration >= 120:
        report.add_issue(
            QualityIssue(
                code="coverage_shortfall",
                severity="error",
                message="字幕覆盖时长明显不足。",
                details={
                    "last_end": f"{last_end:.2f}",
                    "expected_duration": f"{expected_duration:.2f}",
                },
            )
        )


def _looks_like_leftover_english(text: str) -> bool:
    words = _LATIN_WORD_RE.findall(text)
    if not words:
        return False

    chinese_chars = len(_CHINESE_RE.findall(text))
    latin_chars = sum(len(word) for word in words)
    if chinese_chars == 0 and len(words) >= 3:
        return True
    if chinese_chars == 0 and latin_chars >= 12:
        return True
    if chinese_chars > 0 and len(words) >= 8:
        return True

    ascii_ratio = latin_chars / max(len(re.sub(r"\s+", "", text)), 1)
    return ascii_ratio >= 0.55 and len(words) >= 2


def _extract_candidate_terms(text: str) -> set[str]:
    candidates = set()
    for match in _TERM_RE.findall(text):
        normalized = match.strip()
        if len(normalized) < 3:
            continue
        if normalized.lower() in COMMON_SOURCE_STOPWORDS:
            continue
        candidates.add(normalized)
    return candidates


def _term_present(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()
