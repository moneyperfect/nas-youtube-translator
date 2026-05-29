import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import Settings, save_user_settings
from ytsubviewer.models import VideoMetadata
from ytsubviewer.pipeline import SubtitlePipeline
from ytsubviewer.runtime import inspect_environment, prepare_runtime_environment


class AppPackagingTests(unittest.TestCase):
    def test_settings_load_reads_saved_user_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_dir = base / "config"
            data_root = base / "data-root"
            config_path = config_dir / "settings.json"
            save_user_settings(
                deepseek_api_key="demo-key",
                data_root=str(data_root),
                path=config_path,
            )

            with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "YTSUBVIEWER_DATA_ROOT": ""}, clear=False):
                settings = Settings.load(
                    {
                        "project_root": base,
                        "app_root": base,
                        "resource_root": base,
                        "config_dir": config_dir,
                        "config_path": config_path,
                    }
                )

            self.assertEqual(settings.deepseek_api_key, "demo-key")
            self.assertEqual(settings.data_root, data_root)
            self.assertEqual(settings.workspace_dir, data_root / "workspace")
            self.assertEqual(settings.jobs_dir, data_root / "workspace" / "jobs")

    def test_prepare_runtime_environment_updates_cache_envs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ffmpeg = base / "ffmpeg.exe"
            mpv = base / "mpv.exe"
            ffmpeg.write_text("tool", encoding="utf-8")
            mpv.write_text("tool", encoding="utf-8")
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=base,
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "data" / "workspace",
                jobs_dir=base / "data" / "workspace" / "jobs",
                cache_dir=base / "data" / ".cache",
                temp_dir=base / "data" / ".tmp",
                logs_dir=base / "data" / "logs",
                hf_home=base / "data" / ".cache" / "huggingface",
                xdg_cache_home=base / "data" / ".cache",
                ffmpeg_command=str(ffmpeg),
                mpv_command=str(mpv),
            )

            previous_temp = os.environ.get("TEMP")
            previous_tmp = os.environ.get("TMP")
            previous_hf_home = os.environ.get("HF_HOME")
            previous_xdg = os.environ.get("XDG_CACHE_HOME")
            try:
                prepare_runtime_environment(settings)
                self.assertEqual(os.environ["TEMP"], str(settings.temp_dir))
                self.assertEqual(os.environ["TMP"], str(settings.temp_dir))
                self.assertEqual(os.environ["HF_HOME"], str(settings.hf_home))
                self.assertEqual(os.environ["XDG_CACHE_HOME"], str(settings.xdg_cache_home))
                self.assertTrue(settings.logs_dir.exists())
            finally:
                for key, value in [
                    ("TEMP", previous_temp),
                    ("TMP", previous_tmp),
                    ("HF_HOME", previous_hf_home),
                    ("XDG_CACHE_HOME", previous_xdg),
                ]:
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_inspect_environment_reports_missing_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=base,
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "data" / "workspace",
                jobs_dir=base / "data" / "workspace" / "jobs",
                cache_dir=base / "data" / ".cache",
                temp_dir=base / "data" / ".tmp",
                logs_dir=base / "data" / "logs",
                hf_home=base / "data" / ".cache" / "huggingface",
                xdg_cache_home=base / "data" / ".cache",
                deepseek_api_key=None,
                ffmpeg_command="ffmpeg",
                mpv_command="mpv",
            )
            with (
                mock.patch("ytsubviewer.runtime.program_exists", return_value=True),
                mock.patch("ytsubviewer.runtime._check_tcp_connectivity", return_value=(True, "网络可用")),
                mock.patch("ytsubviewer.runtime._detect_compute_mode", return_value=("GPU", "ok")),
            ):
                report = inspect_environment(settings)

            self.assertEqual(report["overall_status"], "缺少配置")
            checks = {item["name"]: item for item in report["checks"]}
            self.assertEqual(checks["API Key"]["status"], "error")

    def test_pipeline_can_reuse_legacy_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_jobs = base / "legacy-jobs"
            current_jobs = base / "current-jobs"
            legacy_jobs.mkdir(parents=True, exist_ok=True)
            current_jobs.mkdir(parents=True, exist_ok=True)
            work_dir = legacy_jobs / "abc_demo video"
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "video.mp4").write_text("video", encoding="utf-8")
            (work_dir / "demo video.zh-CN.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n你好\n",
                encoding="utf-8",
            )
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=base,
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=current_jobs,
                legacy_jobs_dirs=(legacy_jobs,),
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="abc",
                title="demo video",
                duration_seconds=2,
                thumbnail_url=None,
                manual_english_subtitle_lang=None,
                automatic_english_subtitle_lang=None,
            )

            artifacts = pipeline.find_existing_artifacts(metadata)

            self.assertIsNotNone(artifacts)
            assert artifacts is not None
            self.assertEqual(artifacts.work_dir, work_dir)


if __name__ == "__main__":
    unittest.main()
