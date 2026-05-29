from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

import uvicorn


SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import settings
from ytsubviewer.runtime import (
    find_available_port,
    open_url_in_browser,
    prepare_runtime_environment,
    setup_logging,
    show_windows_message,
    wait_for_http_ready,
    write_launch_metadata,
)
from ytsubviewer.webapp import create_web_app


prepare_runtime_environment(settings)
setup_logging(settings)

app = create_web_app()


def _launch_browser_when_ready(url: str, port: int) -> None:
    logger = logging.getLogger("ytsubviewer.launcher")
    logger.info("Preparing browser launch for %s", url)
    write_launch_metadata(settings, url=url, port=port, status="starting", detail="应用正在启动")

    ready = wait_for_http_ready(f"{url}/api/health")
    if not ready:
        message = (
            "YTSubViewer 已尝试启动，但本地页面没有按预期就绪。\n\n"
            f"你可以稍后手动打开：\n{url}\n\n"
            f"日志位置：\n{settings.logs_dir / 'app.log'}"
        )
        write_launch_metadata(settings, url=url, port=port, status="server_not_ready", detail=message)
        logger.error("Local server did not become ready in time: %s", url)
        show_windows_message("YTSubViewer 启动失败", message, error=True)
        return

    if open_url_in_browser(url):
        write_launch_metadata(settings, url=url, port=port, status="browser_opened", detail="已尝试打开浏览器")
        logger.info("Browser launch requested: %s", url)
        return

    message = (
        "YTSubViewer 已在后台启动，但浏览器没有被系统自动打开。\n\n"
        f"请手动访问：\n{url}\n\n"
        f"这个地址也已保存到：\n{settings.data_root / '.runtime' / 'last-url.txt'}"
    )
    write_launch_metadata(settings, url=url, port=port, status="browser_open_failed", detail=message)
    logger.warning("Browser auto-open failed: %s", url)
    show_windows_message("YTSubViewer 已启动", message, error=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="YTSubViewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    port = args.port or find_available_port()
    url = f"http://{args.host}:{port}"

    def _shutdown(signum: int, frame: object) -> None:
        logger = logging.getLogger("ytsubviewer.launcher")
        logger.info("Received signal %s, shutting down gracefully...", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    if os.name != "nt":
        signal.signal(signal.SIGHUP, _shutdown)

    if not args.no_browser and args.host in ("127.0.0.1", "localhost"):
        threading.Thread(target=_launch_browser_when_ready, args=(url, port), daemon=True).start()

    uvicorn.run(
        app,
        host=args.host,
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.getLogger("ytsubviewer.launcher").exception("Application failed during startup")
        write_launch_metadata(
            settings,
            url="http://127.0.0.1",
            port=0,
            status="startup_exception",
            detail=str(exc),
        )
        show_windows_message(
            "YTSubViewer 启动失败",
            "应用启动时发生异常。\n\n"
            f"错误信息：\n{exc}\n\n"
            f"日志位置：\n{settings.logs_dir / 'app.log'}",
            error=True,
        )
        raise
