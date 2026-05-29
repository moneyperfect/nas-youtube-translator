from __future__ import annotations

import subprocess
from pathlib import Path

from ytsubviewer.config import Settings
from ytsubviewer.utils import program_exists


class PlayerService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def open_with_subtitle(self, video_path: Path, subtitle_path: Path) -> str:
        if not video_path.exists():
            raise RuntimeError("本地视频文件不存在，无法播放。")
        if not subtitle_path.exists():
            raise RuntimeError("中文字幕文件不存在，无法播放。")
        if not program_exists(self.settings.mpv_command):
            raise RuntimeError(
                "系统中未找到 mpv。请先安装 mpv，再使用一键播放。Windows 可尝试：winget install shinchiro.mpv.net"
            )

        subprocess.Popen(
            [
                self.settings.mpv_command,
                str(video_path),
                f"--sub-file={subtitle_path}",
            ]
        )
        return "已调用 mpv 打开视频并挂载中文字幕。"
