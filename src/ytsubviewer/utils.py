from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Iterable, Iterator, Sequence, TypeVar


T = TypeVar("T")


def slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]", " ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or "video"


def seconds_to_srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def seconds_to_ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def format_duration(seconds: int | None, lang: str = "zh") -> str:
    if seconds is None:
        return "未知" if lang == "zh" else "Unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def format_eta(seconds: float | int | None, lang: str = "zh") -> str:
    if seconds is None:
        return "未知" if lang == "zh" else "Unknown"
    total_seconds = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if lang == "en":
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{secs}s")
        return " ".join(parts)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_english_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([(\[\"'])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]\"'])", r"\1", text)
    return text.strip()


def normalize_chinese_text(text: str) -> str:
    replacements = {
        ",": "，",
        ".": "。",
        "?": "？",
        "!": "！",
        ";": "；",
        ":": "：",
        "(": "（",
        ")": "）",
    }
    text = normalize_whitespace(text)
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"([，。！？；：])\1+", r"\1", text)
    return text.strip()


def wrap_cjk_text(text: str, width: int = 20, max_lines: int = 2) -> str:
    text = normalize_chinese_text(text)
    if not text:
        return ""

    lines: list[str] = []
    cursor = 0
    while cursor < len(text):
        if len(lines) == max_lines - 1:
            lines.append(text[cursor:])
            break

        window = text[cursor:cursor + width + 2]
        if len(window) <= width:
            lines.append(window)
            break

        split_at = -1
        for marker in "，。！？；：":
            index = window.rfind(marker, 0, width + 1)
            if index > split_at:
                split_at = index
        if split_at <= 0:
            split_at = width
        else:
            split_at += 1

        lines.append(text[cursor:cursor + split_at])
        cursor += split_at

    lines = [line.strip() for line in lines if line.strip()]
    return "\n".join(lines[:max_lines])


def extract_json_payload(text: str) -> list[dict]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON array found in model response")
    return json.loads(stripped[start:end + 1])


def program_exists(program: str) -> bool:
    return shutil.which(program) is not None


def find_first(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def batched(items: Sequence[T], batch_size: int) -> Iterator[Sequence[T]]:
    for index in range(0, len(items), batch_size):
        yield items[index:index + batch_size]
