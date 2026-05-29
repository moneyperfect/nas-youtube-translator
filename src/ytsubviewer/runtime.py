from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from ytsubviewer.config import Settings
from ytsubviewer.utils import program_exists


_CONFIGURED = False


def configure_windows_dll_search_path(extra_roots: list[Path] | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED or os.name != "nt":
        return

    candidates: list[Path] = []
    for entry in map(Path, sys.path):
        if "site-packages" not in str(entry):
            continue
        candidates.extend(
            [
                entry / "nvidia" / "cublas" / "bin",
                entry / "nvidia" / "cuda_runtime" / "bin",
                entry / "nvidia" / "cudnn" / "bin",
                entry / "ctranslate2",
            ]
        )

    for root in extra_roots or []:
        candidates.extend(
            [
                root / "nvidia" / "cublas" / "bin",
                root / "nvidia" / "cuda_runtime" / "bin",
                root / "nvidia" / "cudnn" / "bin",
                root / "ctranslate2",
            ]
        )

    seen: set[str] = set()
    existing = os.environ.get("PATH", "")
    path_parts = existing.split(os.pathsep) if existing else []
    prepended: list[str] = []

    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = str(candidate.resolve())
        lower = resolved.lower()
        if lower in seen:
            continue
        seen.add(lower)

        try:
            os.add_dll_directory(resolved)
        except (AttributeError, FileNotFoundError):
            pass

        if all(part.lower() != lower for part in path_parts):
            prepended.append(resolved)

    if prepended:
        os.environ["PATH"] = os.pathsep.join(prepended + path_parts)

    _CONFIGURED = True


def prepare_runtime_environment(settings: Settings) -> None:
    settings.ensure_directories()
    os.environ["TEMP"] = str(settings.temp_dir)
    os.environ["TMP"] = str(settings.temp_dir)
    os.environ["HF_HOME"] = str(settings.hf_home)
    os.environ["XDG_CACHE_HOME"] = str(settings.xdg_cache_home)
    os.environ["FFMPEG_COMMAND"] = settings.ffmpeg_command
    os.environ["MPV_COMMAND"] = settings.mpv_command
    os.environ["YTSUBVIEWER_DATA_ROOT"] = str(settings.data_root)

    path_parts = os.environ.get("PATH", "").split(os.pathsep) if os.environ.get("PATH") else []
    prepend_dirs: list[str] = []
    for command in [settings.ffmpeg_command, settings.mpv_command]:
        candidate = Path(command)
        if candidate.exists():
            command_dir = str(candidate.parent.resolve())
            if all(part.lower() != command_dir.lower() for part in path_parts + prepend_dirs):
                prepend_dirs.append(command_dir)
    if prepend_dirs:
        os.environ["PATH"] = os.pathsep.join(prepend_dirs + path_parts)

    configure_windows_dll_search_path(extra_roots=[settings.resource_root, settings.app_root])


def setup_logging(settings: Settings) -> Path:
    settings.ensure_directories()
    log_path = settings.logs_dir / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )
    logging.getLogger(__name__).info("Application startup")
    return log_path


def find_available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _check_tcp_connectivity(url: str, timeout: float = 2.5) -> tuple[bool, str]:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False, "无法解析服务地址"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "网络可用"
    except OSError as exc:
        return False, f"无法连接到 {host}:{port}（{exc}）"


def _detect_compute_mode() -> tuple[str, str]:
    configure_windows_dll_search_path()
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
    except Exception:
        count = 0
    if count > 0:
        return "GPU", f"检测到 {count} 个 CUDA 设备，可优先使用 GPU 转写"
    return "CPU", "未检测到可用 CUDA，转写时将自动回退到 CPU"


def inspect_environment(settings: Settings) -> dict[str, object]:
    settings.ensure_directories()
    api_key_ready = bool(settings.deepseek_api_key)
    ffmpeg_ready = program_exists(settings.ffmpeg_command)
    mpv_ready = program_exists(settings.mpv_command)
    network_ready, network_message = _check_tcp_connectivity(settings.deepseek_base_url)
    compute_mode, compute_message = _detect_compute_mode()

    checks = [
        {
            "name": "API Key",
            "status": "ok" if api_key_ready else "error",
            "message": "已配置 DeepSeek API key" if api_key_ready else "未配置 DeepSeek API key",
        },
        {
            "name": "数据目录",
            "status": "ok",
            "message": f"当前数据目录：{settings.data_root}",
        },
        {
            "name": "FFmpeg",
            "status": "ok" if ffmpeg_ready else "error",
            "message": settings.ffmpeg_command if ffmpeg_ready else "未找到 ffmpeg",
        },
        {
            "name": "mpv",
            "status": "ok" if mpv_ready else "error",
            "message": settings.mpv_command if mpv_ready else "未找到 mpv",
        },
        {
            "name": "网络",
            "status": "ok" if network_ready else "warning",
            "message": network_message,
        },
        {
            "name": "推理模式",
            "status": "ok" if compute_mode == "GPU" else "warning",
            "message": compute_message,
        },
        {
            "name": "模型缓存",
            "status": "info",
            "message": f"缓存目录：{settings.hf_home}；首次转写会按需下载模型",
        },
    ]

    if not api_key_ready:
        overall = "缺少配置"
    elif not ffmpeg_ready or not mpv_ready:
        overall = "依赖异常"
    elif not network_ready:
        overall = "缺少网络"
    else:
        overall = "可开始使用"

    return {
        "overall_status": overall,
        "checks": checks,
        "data_root": str(settings.data_root),
        "config_path": str(settings.config_path),
        "logs_path": str(settings.logs_dir / "app.log"),
        "compute_mode": compute_mode,
    }


def runtime_state_dir(settings: Settings) -> Path:
    path = settings.data_root / ".runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_launch_metadata(
    settings: Settings,
    *,
    url: str,
    port: int,
    status: str,
    detail: str = "",
) -> Path:
    state_dir = runtime_state_dir(settings)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "port": port,
        "status": status,
        "detail": detail,
        "logs_path": str(settings.logs_dir / "app.log"),
    }
    metadata_path = state_dir / "launcher-status.json"
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (state_dir / "last-url.txt").write_text(url, encoding="utf-8")
    return metadata_path


def wait_for_http_ready(url: str, *, timeout_seconds: float = 20.0, interval_seconds: float = 0.4) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if 200 <= int(response.status) < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(interval_seconds)
    return False


def open_url_in_browser(url: str) -> bool:
    import subprocess
    import webbrowser

    if os.name == "nt" and hasattr(os, "startfile"):
        try:
            os.startfile(url)
            return True
        except Exception:
            pass

    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", url])
            return True
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", url])
            return True
        except Exception:
            pass

    return webbrowser.open(url, new=1)


def show_windows_message(title: str, message: str, *, error: bool = False) -> None:
    if os.name == "nt":
        try:
            import ctypes

            flags = 0x10 if error else 0x40
            ctypes.windll.user32.MessageBoxW(0, message, title, flags)
            return
        except Exception:
            logging.getLogger(__name__).exception("Failed to show Windows message box")
    prefix = "ERROR" if error else "INFO"
    print(f"[{prefix}] {title}: {message}", file=sys.stderr)
