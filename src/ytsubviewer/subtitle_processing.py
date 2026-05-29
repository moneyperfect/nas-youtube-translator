from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import webvtt

from ytsubviewer.models import SubtitleCue
from ytsubviewer.utils import (
    normalize_chinese_text,
    normalize_english_text,
    seconds_to_ass_timestamp,
    seconds_to_srt_timestamp,
    wrap_cjk_text,
)


def parse_vtt_file(path: Path) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    cue_id = 1
    previous_signature: tuple[float, float, str] | None = None
    for item in webvtt.read(str(path)):
        text = _clean_vtt_text(item.text)
        if not text:
            continue
        start = _time_to_seconds(item.start)
        end = _time_to_seconds(item.end)
        signature = (start, end, text)
        if signature == previous_signature:
            continue
        previous_signature = signature
        cues.append(SubtitleCue(id=cue_id, start=start, end=end, source_text=text))
        cue_id += 1
    return split_source_cues(cues)


def parse_srt_file(path: Path) -> list[SubtitleCue]:
    content = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", content.strip())
    cues: list[SubtitleCue] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        match = re.match(r"(.+?)\s*-->\s*(.+)", lines[1])
        if match is None:
            continue
        start = _time_to_seconds(match.group(1))
        end = _time_to_seconds(match.group(2))
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        cues.append(
            SubtitleCue(
                id=len(cues) + 1,
                start=start,
                end=end,
                source_text="",
                target_text=text,
            )
        )
    return cues


def split_source_cues(cues: list[SubtitleCue], max_chars: int = 90) -> list[SubtitleCue]:
    refined: list[SubtitleCue] = []
    cue_id = 1
    for cue in cues:
        pieces = split_english_text(cue.source_text, max_chars=max_chars)
        if len(pieces) == 1:
            refined.append(cue.clone(id=cue_id, source_text=pieces[0]))
            cue_id += 1
            continue

        cursor = cue.start
        durations = _piece_durations(cue.start, cue.end, pieces)
        for index, piece in enumerate(pieces):
            end = cue.end if index == len(pieces) - 1 else cursor + durations[index]
            refined.append(cue.clone(id=cue_id, start=cursor, end=end, source_text=piece))
            cue_id += 1
            cursor = end
    return refined


def polish_translated_cues(cues: list[SubtitleCue], width: int = 20, max_lines: int = 2) -> list[SubtitleCue]:
    polished: list[SubtitleCue] = []
    cue_id = 1
    for cue in cues:
        pieces = split_chinese_text(cue.target_text, max_chars=width * max_lines)
        if not pieces:
            continue

        cursor = cue.start
        durations = _piece_durations(cue.start, cue.end, pieces)
        for index, piece in enumerate(pieces):
            end = cue.end if index == len(pieces) - 1 else cursor + durations[index]
            polished.append(
                SubtitleCue(
                    id=cue_id,
                    start=cursor,
                    end=end,
                    source_text=cue.source_text,
                    target_text=wrap_cjk_text(piece, width=width, max_lines=max_lines),
                )
            )
            cue_id += 1
            cursor = end
    return polished


def build_bilingual_cues(
    cues: list[SubtitleCue],
    *,
    english_max_chars: int = 54,
    chinese_max_chars: int = 22,
) -> list[SubtitleCue]:
    bilingual: list[SubtitleCue] = []
    cue_id = 1
    for cue in cues:
        english_text = normalize_english_text(cue.source_text)
        chinese_text = normalize_chinese_text(cue.target_text)
        if not english_text and not chinese_text:
            continue

        piece_count = max(
            len(split_english_text(english_text, max_chars=english_max_chars)) if english_text else 1,
            len(split_chinese_text(chinese_text, max_chars=chinese_max_chars)) if chinese_text else 1,
        )
        english_pieces = _partition_english_text(english_text, piece_count) if english_text else [""] * piece_count
        chinese_pieces = _partition_cjk_text(chinese_text, piece_count) if chinese_text else [""] * piece_count
        durations = _piece_durations(cue.start, cue.end, chinese_pieces if chinese_text else english_pieces)
        cursor = cue.start
        for index in range(piece_count):
            english_piece = normalize_english_text(english_pieces[index])
            chinese_piece = normalize_chinese_text(chinese_pieces[index])
            if not english_piece and not chinese_piece:
                continue
            end = cue.end if index == piece_count - 1 else cursor + durations[index]
            bilingual.append(
                SubtitleCue(
                    id=cue_id,
                    start=cursor,
                    end=end,
                    source_text=english_piece,
                    target_text=chinese_piece,
                )
            )
            cue_id += 1
            cursor = end
    return bilingual


def build_bilingual_cues_from_tracks(
    english_cues: list[SubtitleCue],
    chinese_cues: list[SubtitleCue],
    *,
    english_max_chars: int = 54,
    chinese_max_chars: int = 22,
) -> list[SubtitleCue]:
    merged: list[SubtitleCue] = []
    english_index = 0
    for chinese_cue in chinese_cues:
        while english_index < len(english_cues) and english_cues[english_index].end <= chinese_cue.start:
            english_index += 1

        matches: list[str] = []
        cursor = english_index
        while cursor < len(english_cues) and english_cues[cursor].start < chinese_cue.end:
            if _cue_overlap(english_cues[cursor], chinese_cue) > 0:
                matches.append(english_cues[cursor].source_text)
            cursor += 1

        merged.append(
            SubtitleCue(
                id=len(merged) + 1,
                start=chinese_cue.start,
                end=chinese_cue.end,
                source_text=normalize_english_text(" ".join(_dedupe_preserving_order(matches))),
                target_text=normalize_chinese_text(chinese_cue.target_text),
            )
        )

    return build_bilingual_cues(
        merged,
        english_max_chars=english_max_chars,
        chinese_max_chars=chinese_max_chars,
    )


def write_srt(cues: list[SubtitleCue], path: Path) -> Path:
    lines: list[str] = []
    for cue in cues:
        text = cue.target_text.strip()
        if not text:
            continue
        lines.append(str(cue.id))
        lines.append(f"{seconds_to_srt_timestamp(cue.start)} --> {seconds_to_srt_timestamp(cue.end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_ass(cues: list[SubtitleCue], path: Path, *, bilingual: bool = False) -> Path:
    style_name = "Bilingual" if bilingual else "Chinese"
    font_size = 40 if bilingual else 44
    lines = [
        "[Script Info]",
        f"Title: {path.stem}",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
        "MarginV,Encoding",
        (
            f"Style: {style_name},Microsoft YaHei,{font_size},&H00FFFFFF,&H000000FF,&H00111111,&H64000000,"
            "0,0,0,0,100,100,0,0,1,2.2,0.8,2,48,48,40,1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        text = _ass_text_for_cue(cue, bilingual=bilingual)
        if not text:
            continue
        lines.append(
            "Dialogue: 0,"
            f"{seconds_to_ass_timestamp(cue.start)},{seconds_to_ass_timestamp(cue.end)},{style_name},,"
            f"0,0,0,,{text}"
        )
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


def split_english_text(text: str, max_chars: int = 90) -> list[str]:
    text = normalize_english_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts = [chunk.strip() for chunk in re.split(r"(?<=[.!?;:])\s+", text) if chunk.strip()]
    if len(parts) == 1:
        return _split_english_by_words(text, max_chars=max_chars)

    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = normalize_english_text(f"{current} {part}".strip())
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_chinese_text(text: str, max_chars: int = 22) -> list[str]:
    text = normalize_chinese_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts = [piece.strip() for piece in re.split(r"(?<=[，。！？；：])", text) if piece.strip()]
    if len(parts) == 1:
        return [text[index:index + max_chars] for index in range(0, len(text), max_chars)]

    merged: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{part}"
        if current and len(candidate) > max_chars:
            merged.append(current)
            current = part
        else:
            current = candidate
    if current:
        merged.append(current)
    return merged


def _clean_vtt_text(text: str) -> str:
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    deduped: list[str] = []
    for line in lines:
        line = re.sub(r"<[^>]+>", "", line)
        if line not in deduped:
            deduped.append(line)
    return normalize_english_text(" ".join(deduped))


def _split_english_by_words(text: str, max_chars: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = normalize_english_text(" ".join(current + [word]))
        if current and len(candidate) > max_chars:
            chunks.append(normalize_english_text(" ".join(current)))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(normalize_english_text(" ".join(current)))
    return chunks


def _partition_english_text(text: str, piece_count: int) -> list[str]:
    text = normalize_english_text(text)
    if piece_count <= 1 or not text:
        return [text]

    words = text.split()
    if len(words) >= piece_count:
        return [normalize_english_text(" ".join(group)) for group in _partition_units(words, piece_count)]
    return [normalize_english_text("".join(group)) for group in _partition_units(list(text), piece_count)]


def _partition_cjk_text(text: str, piece_count: int) -> list[str]:
    text = normalize_chinese_text(text)
    if piece_count <= 1 or not text:
        return [text]

    parts = split_chinese_text(text, max_chars=max(1, len(text) // piece_count + 2))
    if len(parts) == piece_count:
        return parts
    if len(parts) > piece_count:
        return ["".join(group) for group in _partition_units(parts, piece_count)]
    return ["".join(group) for group in _partition_units(list(text), piece_count)]


def _partition_units(units: list[str], piece_count: int) -> list[list[str]]:
    piece_count = max(1, piece_count)
    if not units:
        return [[] for _ in range(piece_count)]

    total = len(units)
    result: list[list[str]] = []
    start = 0
    for index in range(piece_count):
        remaining_groups = piece_count - index
        remaining_items = total - start
        size = max(1, round(remaining_items / remaining_groups))
        end = min(total, start + size)
        result.append(units[start:end])
        start = end
    while len(result) < piece_count:
        result.append([])
    return result[:piece_count]


def _piece_durations(start: float, end: float, pieces: list[str]) -> list[float]:
    duration = max(end - start, 0.1)
    weights = [max(len(piece.strip()), 1) for piece in pieces]
    total_weight = max(sum(weights), 1)
    return [duration * (weight / total_weight) for weight in weights]


def _cue_overlap(left: SubtitleCue, right: SubtitleCue) -> float:
    return max(0.0, min(left.end, right.end) - max(left.start, right.start))


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _ass_text_for_cue(cue: SubtitleCue, *, bilingual: bool) -> str:
    if bilingual:
        english = _escape_ass_text(cue.source_text.replace("\n", " ").strip())
        chinese = _escape_ass_text(cue.target_text.replace("\n", " ").strip())
        if english and chinese:
            return f"{english}\\N{chinese}"
        return english or chinese
    return _escape_ass_text(cue.target_text.strip())


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _time_to_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours = "0"
        minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
