from __future__ import annotations

from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from ytsubviewer.config import Settings
from ytsubviewer.models import VideoMetadata
from ytsubviewer.utils import find_first, slugify_filename


class YouTubeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract_metadata(self, url: str) -> VideoMetadata:
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)

        return VideoMetadata(
            video_id=str(info["id"]),
            title=info.get("title") or str(info["id"]),
            duration_seconds=info.get("duration"),
            thumbnail_url=info.get("thumbnail"),
            manual_english_subtitle_lang=self._pick_english_track(info.get("subtitles") or {}),
            automatic_english_subtitle_lang=self._pick_english_track(info.get("automatic_captions") or {}),
            channel_id=str(info.get("channel_id") or "").strip() or None,
            channel_name=str(info.get("channel") or info.get("uploader") or "").strip() or None,
            uploader=str(info.get("uploader") or "").strip() or None,
        )

    def prepare_work_dir(self, metadata: VideoMetadata) -> Path:
        safe_title = slugify_filename(metadata.title)
        work_dir = self.settings.jobs_dir / f"{metadata.video_id}_{safe_title}"
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def candidate_work_dirs(self, metadata: VideoMetadata) -> list[Path]:
        safe_title = slugify_filename(metadata.title)
        folder_name = f"{metadata.video_id}_{safe_title}"
        candidates = [self.prepare_work_dir(metadata)]
        for root in self.settings.legacy_jobs_dirs:
            candidate = root / folder_name
            if candidate.exists():
                candidates.append(candidate)
        seen: set[str] = set()
        deduped: list[Path] = []
        for candidate in candidates:
            key = str(candidate.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def find_existing_video(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("video.*")))

    def find_existing_subtitle(self, work_dir: Path) -> Path | None:
        candidates = []
        manual = self.find_existing_manual_subtitle(work_dir)
        automatic = self.find_existing_automatic_subtitle(work_dir)
        if manual is not None:
            candidates.append(manual)
        if automatic is not None:
            candidates.append(automatic)
        return find_first(candidates)

    def find_existing_manual_subtitle(self, work_dir: Path) -> Path | None:
        candidates = [
            path
            for path in (sorted(work_dir.glob("source*.vtt")) + sorted(work_dir.glob("source*.srv3")))
            if not path.name.startswith("source.auto")
        ]
        return find_first(candidates)

    def find_existing_automatic_subtitle(self, work_dir: Path) -> Path | None:
        candidates = sorted(work_dir.glob("source.auto*.vtt")) + sorted(work_dir.glob("source.auto*.srv3"))
        return find_first(candidates)

    def find_existing_chinese_subtitle(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("*.zh-CN.srt")))

    def find_existing_chinese_ass(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("*.zh-CN.ass")))

    def find_existing_bilingual_ass(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("*.bilingual.ass")))

    def find_existing_burned_chinese_video(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("*.zh-CN.hardsub.mp4")))

    def find_existing_burned_bilingual_video(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("*.bilingual.hardsub.mp4")))

    def find_existing_quality_report(self, work_dir: Path) -> Path | None:
        return find_first(sorted(work_dir.glob("*.quality-report.md")))

    def download_video(self, url: str, work_dir: Path) -> Path:
        template = str(work_dir / "video.%(ext)s")
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "format": self.settings.yt_format,
                "merge_output_format": "mp4",
                "outtmpl": template,
                "ffmpeg_location": str(Path(self.settings.ffmpeg_command).parent),
            }
        ) as ydl:
            ydl.download([url])

        video = self.find_existing_video(work_dir)
        if video is None:
            raise RuntimeError("视频下载完成，但未找到本地视频文件。")
        return video

    def download_manual_subtitle(self, url: str, language: str, work_dir: Path) -> Path | None:
        if not language:
            return None

        template = str(work_dir / "source.%(ext)s")
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": False,
                "subtitleslangs": [language],
                "subtitlesformat": "vtt/best",
                "outtmpl": template,
            }
        ) as ydl:
            ydl.download([url])

        return self.find_existing_subtitle(work_dir)

    def download_automatic_subtitle(self, url: str, language: str, work_dir: Path) -> Path | None:
        if not language:
            return None

        template = str(work_dir / "source.auto.%(ext)s")
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "writesubtitles": False,
                "writeautomaticsub": True,
                "subtitleslangs": [language],
                "subtitlesformat": "vtt/best",
                "outtmpl": template,
            }
        ) as ydl:
            ydl.download([url])

        return self.find_existing_subtitle(work_dir)

    @staticmethod
    def _pick_english_track(track_map: dict[str, Any]) -> str | None:
        english_keys = sorted(key for key in track_map.keys() if key.lower().startswith("en"))
        return english_keys[0] if english_keys else None
