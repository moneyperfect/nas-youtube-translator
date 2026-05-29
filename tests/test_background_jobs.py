import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.background_jobs import BackgroundGenerationManager
from ytsubviewer.config import Settings
from ytsubviewer.models import JobArtifacts, TranslationControlConfig, VideoMetadata
from ytsubviewer.pipeline import PipelineEvent


class BackgroundJobManagerTests(unittest.TestCase):
    def test_background_generation_survives_as_persisted_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            metadata = VideoMetadata(
                video_id="demo123",
                title="Demo Video",
                duration_seconds=90,
                thumbnail_url="https://example.com/thumb.jpg",
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = settings.jobs_dir / "demo123_Demo Video"
            work_dir.mkdir(parents=True, exist_ok=True)
            video_path = work_dir / "video.mp4"
            subtitle_path = work_dir / "demo.zh-CN.srt"
            video_path.write_text("video", encoding="utf-8")
            subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

            artifacts = JobArtifacts(
                video_id=metadata.video_id,
                title=metadata.title,
                source_kind="manual_subtitle",
                video_path=video_path,
                english_subtitle_path=None,
                chinese_subtitle_path=subtitle_path,
                work_dir=work_dir,
                duration_seconds=metadata.duration_seconds,
            )

            fake_pipeline = SimpleNamespace(
                youtube=SimpleNamespace(prepare_work_dir=lambda _metadata: work_dir),
                generate_events=lambda _url, controls=None: iter(
                    [
                        PipelineEvent(0.15, "分析视频信息"),
                        PipelineEvent(1.0, "处理完成", artifacts=artifacts),
                    ]
                ),
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

            deadline = time.time() + 3
            latest = manager.get_current_snapshot()
            while latest is not None and latest.status != "completed" and time.time() < deadline:
                time.sleep(0.05)
                latest = manager.get_current_snapshot()

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(snapshot.job_id, latest.job_id)
            self.assertEqual(latest.status, "completed")
            self.assertEqual(latest.work_dir, str(work_dir))
            self.assertIn("处理完成", latest.log_lines[-1])

    def test_stale_running_snapshot_is_marked_failed_after_restart_like_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                data_root=base / "data",
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            fake_pipeline = SimpleNamespace(
                youtube=SimpleNamespace(prepare_work_dir=lambda _metadata: settings.jobs_dir / "demo"),
                generate_events=lambda _url, controls=None: iter(()),
            )
            manager = BackgroundGenerationManager(settings, fake_pipeline)
            manager.current_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            manager.current_snapshot_path.write_text(
                (
                    '{\n'
                    '  "job_id": "stale-job",\n'
                    '  "url": "https://example.com/watch?v=stale",\n'
                    '  "status": "running",\n'
                    '  "progress": 0.66,\n'
                    '  "log_lines": ["still running"],\n'
                    '  "started_at": 1,\n'
                    '  "updated_at": 1,\n'
                    '  "work_dir": ""\n'
                    '}\n'
                ),
                encoding="utf-8",
            )

            restored = manager.get_current_snapshot()

            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.status, "failed")
            self.assertTrue(restored.error)
            self.assertIn("中断", restored.error)


if __name__ == "__main__":
    unittest.main()
