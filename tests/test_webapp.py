import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.background_jobs import BackgroundGenerationManager
from ytsubviewer.config import Settings
from ytsubviewer.job_state import save_job_state
from ytsubviewer.models import JobArtifacts
from ytsubviewer.models import TranslationControlConfig, VideoMetadata
from ytsubviewer.pipeline import PipelineEvent, SubtitlePipeline
from ytsubviewer.webapp import create_web_app


class WebAppTests(unittest.TestCase):
    @staticmethod
    def _request(app, method: str, path: str, **kwargs):
        async def _run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(_run())

    def test_bootstrap_returns_environment_and_style_presets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=Path(__file__).resolve().parents[1],
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
                deepseek_api_key="dummy",
            )
            app = create_web_app(app_runtime_settings=settings, mount_legacy=False)
            response = self._request(app, "GET", "/api/bootstrap")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["settings"]["api_key_ready"])
            self.assertTrue(payload["style_presets"])
            self.assertIn("overall_status", payload["environment"])

    def test_analyze_returns_automatic_subtitle_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=Path(__file__).resolve().parents[1],
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
                deepseek_api_key="dummy",
            )
            metadata = VideoMetadata(
                video_id="demo123",
                title="Demo Video",
                duration_seconds=120,
                thumbnail_url="https://example.com/thumb.jpg",
                manual_english_subtitle_lang=None,
                automatic_english_subtitle_lang="en",
            )
            fake_pipeline = SimpleNamespace(
                analyze=lambda _url: metadata,
                find_existing_artifacts=lambda _metadata: None,
            )
            app = create_web_app(
                app_runtime_settings=settings,
                pipeline=fake_pipeline,
                generation_manager=BackgroundGenerationManager(settings, fake_pipeline),
                mount_legacy=False,
            )
            response = self._request(
                app,
                "POST",
                "/api/analyze",
                json={
                    "url": "https://www.youtube.com/watch?v=demo123",
                    "style_preset": "default",
                    "glossary_text": "",
                    "protected_terms_text": "",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("自动英文字幕", payload["strategy_text"])
            self.assertFalse(payload["has_existing_result"])

    def test_current_job_restores_persisted_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=Path(__file__).resolve().parents[1],
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
                deepseek_api_key="dummy",
            )
            metadata = VideoMetadata(
                video_id="demo123",
                title="Demo Video",
                duration_seconds=90,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = settings.jobs_dir / "demo123_Demo Video"
            work_dir.mkdir(parents=True, exist_ok=True)
            fake_pipeline = SimpleNamespace(
                youtube=SimpleNamespace(prepare_work_dir=lambda _metadata: work_dir),
                generate_events=lambda _url, controls=None: iter([PipelineEvent(0.5, "still running")]),
                ensure_subtitle_artifacts=lambda artifacts: artifacts,
                ensure_quality_report=lambda artifacts: artifacts,
            )
            manager = BackgroundGenerationManager(settings, fake_pipeline)
            snapshot = manager.start_generation(
                url="https://example.com/watch?v=demo123",
                metadata=metadata,
                strategy_text="优先使用人工英文字幕：en",
                controls=TranslationControlConfig(style_preset="default"),
                glossary_text="",
                protected_terms_text="",
            )
            app = create_web_app(
                app_runtime_settings=settings,
                pipeline=SubtitlePipeline(settings),
                generation_manager=manager,
                mount_legacy=False,
            )
            response = self._request(app, "GET", "/api/job/current")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["job"]["job_id"], snapshot.job_id)
            self.assertEqual(payload["job"]["status"], "running")
            time.sleep(0.1)

    def test_export_endpoint_creates_background_export_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=Path(__file__).resolve().parents[1],
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
                deepseek_api_key="dummy",
            )
            work_dir = settings.jobs_dir / "demo_job"
            work_dir.mkdir(parents=True, exist_ok=True)
            video = work_dir / "video.mp4"
            subtitle = work_dir / "demo.zh-CN.srt"
            output = work_dir / "demo.bilingual.hardsub.mp4"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
            save_job_state(
                work_dir,
                {
                    "version": 1,
                    "status": "completed",
                    "video_id": "demo",
                    "title": "demo",
                    "source_kind": "manual_subtitle",
                    "video_path": str(video),
                    "english_subtitle_path": "",
                    "chinese_subtitle_path": str(subtitle),
                    "chinese_ass_path": "",
                    "bilingual_ass_path": "",
                    "burned_chinese_video_path": "",
                    "burned_bilingual_video_path": "",
                    "quality_report_path": "",
                    "duration_seconds": 2,
                    "work_dir": str(work_dir),
                    "quality_report": {},
                    "source_cues": [],
                    "translated_cues": [],
                },
            )
            artifacts = JobArtifacts(
                video_id="demo",
                title="demo",
                source_kind="manual_subtitle",
                video_path=video,
                english_subtitle_path=None,
                chinese_subtitle_path=subtitle,
                work_dir=work_dir,
                duration_seconds=2,
            )
            fake_pipeline = SimpleNamespace(
                analyze=lambda _url: VideoMetadata(
                    video_id="demo",
                    title="Demo",
                    duration_seconds=2,
                    thumbnail_url=None,
                    manual_english_subtitle_lang="en",
                    automatic_english_subtitle_lang=None,
                ),
                find_existing_artifacts=lambda _metadata: None,
                export_video_events=lambda _state, bilingual=False: iter(
                    [PipelineEvent(1.0, "导出完成", artifacts=artifacts)]
                ),
                ensure_subtitle_artifacts=lambda value: value,
                ensure_quality_report=lambda value: value,
            )
            manager = BackgroundGenerationManager(settings, fake_pipeline)
            app = create_web_app(
                app_runtime_settings=settings,
                pipeline=fake_pipeline,
                generation_manager=manager,
                mount_legacy=False,
            )

            response = self._request(
                app,
                "POST",
                "/api/export",
                json={
                    "work_dir": str(work_dir),
                    "bilingual": True,
                    "preview": False,
                    "performance_mode": "balanced",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["job"]["kind"], "export")
            self.assertIn(payload["job"]["status"], {"pending", "running", "completed"})

    def test_creator_profile_defaults_are_reused_on_analyze(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=Path(__file__).resolve().parents[1],
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
                deepseek_api_key="dummy",
            )
            metadata = VideoMetadata(
                video_id="demo123",
                title="Demo Video",
                duration_seconds=120,
                thumbnail_url="https://example.com/thumb.jpg",
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
                channel_id="channel-1",
                channel_name="Creator One",
            )
            fake_pipeline = SimpleNamespace(
                analyze=lambda _url: metadata,
                find_existing_artifacts=lambda _metadata: None,
            )
            app = create_web_app(
                app_runtime_settings=settings,
                pipeline=fake_pipeline,
                generation_manager=BackgroundGenerationManager(settings, fake_pipeline),
                mount_legacy=False,
            )

            save_response = self._request(
                app,
                "POST",
                "/api/creator-profile/save",
                json={
                    "url": "https://www.youtube.com/watch?v=demo123",
                    "style_preset": "conference",
                    "glossary_text": "Blackwell -> Blackwell 架构",
                    "protected_terms_text": "CUDA",
                },
            )
            self.assertEqual(save_response.status_code, 200)

            response = self._request(
                app,
                "POST",
                "/api/analyze",
                json={
                    "url": "https://www.youtube.com/watch?v=demo123",
                    "style_preset": "default",
                    "glossary_text": "",
                    "protected_terms_text": "",
                    "use_creator_defaults": True,
                    "performance_mode": "balanced",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["profile"]["channel_name"], "Creator One")
            self.assertEqual(payload["resolved_controls"]["style_preset"], "conference")
            self.assertIn("Blackwell", payload["resolved_controls"]["glossary_text"])

    def test_license_dev_activation_is_available_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                app_root=base,
                resource_root=Path(__file__).resolve().parents[1],
                config_dir=base / "config",
                config_path=base / "config" / "settings.json",
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                cache_dir=base / "cache",
                temp_dir=base / "tmp",
                logs_dir=base / "logs",
                hf_home=base / "cache" / "huggingface",
                xdg_cache_home=base / "cache",
                deepseek_api_key="dummy",
            )
            app = create_web_app(app_runtime_settings=settings, mount_legacy=False)

            response = self._request(
                app,
                "POST",
                "/api/license/activate",
                json={"license_key": "DEV-LICENSE"},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["mode"], "activated")
            self.assertTrue(payload["active"])


if __name__ == "__main__":
    unittest.main()
