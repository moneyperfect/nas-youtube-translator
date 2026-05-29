from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

from ytsubviewer.config import Settings
from ytsubviewer.utils import format_eta, program_exists


class VideoExportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def burn_video_events(
        self,
        video_path: Path,
        subtitle_path: Path,
        output_path: Path,
        *,
        label: str,
        preset: str = "superfast",
        crf: int = 18,
        timeout_seconds: float | None = None,
    ) -> Generator[tuple[float, str], None, None]:
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink(missing_ok=True)

        if (
            output_path.exists()
            and output_path.stat().st_size > 0
            and output_path.stat().st_mtime >= max(video_path.stat().st_mtime, subtitle_path.stat().st_mtime)
        ):
            yield 1.0, f"{label} 已存在，直接复用"
            return

        ffmpeg_command = Path(self.settings.ffmpeg_command)
        if not program_exists(str(ffmpeg_command)):
            raise RuntimeError("系统中未找到 ffmpeg，无法导出烧录视频。")

        duration = self.probe_duration(video_path)
        filter_subtitle_path = self._prepare_safe_filter_subtitle(subtitle_path, output_path.parent)
        command = self.build_burn_command(
            video_path,
            filter_subtitle_path,
            output_path,
            preset=preset,
            crf=crf,
        )
        process = subprocess.Popen(
            command,
            cwd=video_path.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
        latest_ratio = 0.0
        latest_eta = "未知"
        diagnostic_lines: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if deadline and time.monotonic() > deadline:
                    process.kill()
                    raise RuntimeError(f"{label} 导出超时（已超过 {timeout_seconds} 秒）")

                stripped = line.strip()
                if not stripped:
                    continue

                if stripped.startswith("out_time_ms=") and duration:
                    out_time_ms = int(stripped.split("=", 1)[1] or "0")
                    latest_ratio = min(0.99, out_time_ms / 1_000_000 / duration)
                    if latest_ratio > 0:
                        latest_eta = format_eta((duration * (1 - latest_ratio)) / latest_ratio)
                    yield latest_ratio, f"{label} 导出中：{latest_ratio * 100:.1f}% ，预计剩余 {latest_eta}"
                    continue

                if stripped == "progress=end":
                    break

                if not stripped.startswith(
                    (
                        "frame=",
                        "fps=",
                        "bitrate=",
                        "total_size=",
                        "dup_frames=",
                        "drop_frames=",
                        "speed=",
                        "out_time_us=",
                        "out_time_ms=",
                        "out_time=",
                        "progress=",
                    )
                ):
                    diagnostic_lines.append(stripped)
                    diagnostic_lines = diagnostic_lines[-12:]
        finally:
            returncode = process.wait()
            if filter_subtitle_path != subtitle_path:
                filter_subtitle_path.unlink(missing_ok=True)

        if returncode != 0:
            diagnostic = " | ".join(diagnostic_lines[-3:]) if diagnostic_lines else ""
            detail = f"ffmpeg 返回错误码 {returncode}。"
            if diagnostic:
                detail += f" 关键信息：{diagnostic}"
            raise RuntimeError(f"{label} 导出失败，{detail}")

        yield 1.0, f"{label} 导出完成"

    def build_burn_command(
        self,
        video_path: Path,
        subtitle_path: Path,
        output_path: Path,
        *,
        preset: str = "superfast",
        crf: int = 18,
    ) -> list[str]:
        return [
            self.settings.ffmpeg_command,
            "-y",
            "-i",
            video_path.name,
            "-vf",
            f"ass='{self._escape_filter_path(subtitle_path.name)}'",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            output_path.name,
        ]

    def probe_duration(self, video_path: Path) -> float | None:
        ffprobe_path = self._find_ffprobe()
        if ffprobe_path is None:
            return None

        completed = subprocess.run(
            [
                str(ffprobe_path),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            return None
        try:
            return float(completed.stdout.strip())
        except ValueError:
            return None

    def _find_ffprobe(self) -> Path | None:
        ffmpeg_path = Path(self.settings.ffmpeg_command)
        for name in ["ffprobe", "ffprobe.exe"]:
            sibling = ffmpeg_path.parent / name
            if sibling.exists():
                return sibling
        found = shutil.which("ffprobe")
        return Path(found) if found else None

    def _prepare_safe_filter_subtitle(self, subtitle_path: Path, work_dir: Path) -> Path:
        if self._filter_path_is_safe(subtitle_path.name):
            return subtitle_path

        digest = hashlib.sha1(str(subtitle_path).encode("utf-8")).hexdigest()[:10]
        temp_subtitle_path = work_dir / f"_burn_input_{digest}.ass"
        shutil.copyfile(subtitle_path, temp_subtitle_path)
        return temp_subtitle_path

    @staticmethod
    def _escape_filter_path(value: str) -> str:
        return (
            value.replace("\\", "/")
            .replace(":", r"\:")
            .replace(",", r"\,")
            .replace(";", r"\;")
            .replace("[", r"\[")
            .replace("]", r"\]")
            .replace("'", r"\'")
        )

    @staticmethod
    def _filter_path_is_safe(value: str) -> bool:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- /")
        return all(char in allowed for char in value)
