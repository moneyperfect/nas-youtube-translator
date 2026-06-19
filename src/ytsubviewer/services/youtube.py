from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from ytsubviewer.config import Settings
from ytsubviewer.models import VideoMetadata
from ytsubviewer.services.base import BaseService
from ytsubviewer.utils import find_first, slugify_filename

logger = logging.getLogger(__name__)


class YouTubeLoginRequired(Exception):
    """Raised when YouTube requires user login to proceed."""


class YouTubeService(BaseService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def _ensure_cookies(self) -> Path | None:
        """Ensure cookies.txt exists and is fresh. Auto-fetches via Playwright if needed."""
        from ytsubviewer.services.cookie_manager import ensure_cookies
        return ensure_cookies(self.settings.data_root)

    def _ydl_options(self, **overrides) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        opts.update(overrides)
        cookies_path = self._ensure_cookies()
        if cookies_path and cookies_path.exists():
            opts["cookiefile"] = str(cookies_path)
        # 代理配置：从环境变量读取
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            opts["proxy"] = proxy
        return opts

    def _ydl_extract_with_fallback(self, url: str, opts: dict[str, Any]) -> dict[str, Any]:
        try:
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as first_err:
            if "Sign in" not in str(first_err) and "bot" not in str(first_err):
                raise
            logger.warning("Bot detection triggered, trying Chrome cookie extraction...")
            cookies_file = self.settings.data_root / "cookies.txt"
            if cookies_file.exists():
                cookies_file.unlink(missing_ok=True)
            # Try extracting from Chrome (works if Chrome is not running)
            from ytsubviewer.services.cookie_manager import ensure_cookies as _ensure
            result = _ensure(self.settings.data_root)
            if result and result.exists():
                opts["cookiefile"] = str(result)
                with YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)
            raise YouTubeLoginRequired(
                "YouTube 需要登录信息。请关闭 Chrome 后点击「获取 Cookies」按钮，系统会自动提取你 Chrome 中的 YouTube 登录信息并重启 Chrome。"
            ) from first_err

    def extract_metadata(self, url: str) -> VideoMetadata:
        info = self._ydl_extract_with_fallback(url, self._ydl_options(skip_download=True))
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
        opts = self._ydl_options(
            format=self.settings.yt_format,
            merge_output_format="mp4",
            outtmpl=template,
            ffmpeg_location=str(Path(self.settings.ffmpeg_command).parent),
        )
        with YoutubeDL(opts) as ydl:
            ydl.download([url])

        video = self.find_existing_video(work_dir)
        if video is None:
            raise RuntimeError("视频下载完成，但未找到本地视频文件。")
        return video

    def download_manual_subtitle(self, url: str, language: str, work_dir: Path) -> Path | None:
        if not language:
            return None

        template = str(work_dir / "source.%(ext)s")
        opts = self._ydl_options(
            skip_download=True,
            writesubtitles=True,
            writeautomaticsub=False,
            subtitleslangs=[language],
            subtitlesformat="vtt/best",
            outtmpl=template,
        )
        with YoutubeDL(opts) as ydl:
            ydl.download([url])

        return self.find_existing_subtitle(work_dir)

    def download_automatic_subtitle(self, url: str, language: str, work_dir: Path) -> Path | None:
        if not language:
            return None

        template = str(work_dir / "source.auto.%(ext)s")
        opts = self._ydl_options(
            skip_download=True,
            writesubtitles=False,
            writeautomaticsub=True,
            subtitleslangs=[language],
            subtitlesformat="vtt/best",
            outtmpl=template,
        )
        with YoutubeDL(opts) as ydl:
            ydl.download([url])

        return self.find_existing_subtitle(work_dir)

    @staticmethod
    def _pick_english_track(track_map: dict[str, Any]) -> str | None:
        english_keys = sorted(key for key in track_map.keys() if key.lower().startswith("en"))
        return english_keys[0] if english_keys else None
