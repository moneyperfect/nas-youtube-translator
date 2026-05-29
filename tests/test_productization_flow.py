import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import Settings
from ytsubviewer.job_state import load_job_state, save_job_state
from ytsubviewer.models import JobArtifacts, SubtitleCue, TranslationControlConfig
from ytsubviewer.pipeline import SubtitlePipeline


class ProductizationFlowTests(unittest.TestCase):
    def test_ensure_quality_report_writes_file_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            work_dir = settings.jobs_dir / "demo_job"
            work_dir.mkdir(parents=True, exist_ok=True)
            video = work_dir / "video.mp4"
            subtitle = work_dir / "demo.zh-CN.srt"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n第一句中文\n\n2\n00:00:02,000 --> 00:00:04,000\n第二句中文\n",
                encoding="utf-8",
            )
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
                    "duration_seconds": 4,
                    "work_dir": str(work_dir),
                    "translation_controls": TranslationControlConfig(style_preset="creator").to_dict(),
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
                duration_seconds=4,
            )

            artifacts = pipeline.ensure_quality_report(artifacts)

            self.assertIsNotNone(artifacts.quality_report_path)
            assert artifacts.quality_report_path is not None
            self.assertTrue(artifacts.quality_report_path.exists())
            state = load_job_state(work_dir)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertTrue(state["quality_report_path"])
            self.assertGreaterEqual(state["quality_report"]["total_cues"], 2)

    def test_restore_translated_cues_skips_mismatched_controls(self) -> None:
        pipeline = SubtitlePipeline(Settings())
        source_cues = [SubtitleCue(id=1, start=0.0, end=1.0, source_text="Hello there.")]
        translated_cues = [source_cues[0].clone(target_text="你好。")]
        state = {
            "source_kind": "manual_subtitle",
            "translation_controls": TranslationControlConfig(style_preset="conference").to_dict(),
            "source_cues": [cue.to_dict() for cue in source_cues],
            "translated_cues": [cue.to_dict() for cue in translated_cues],
        }

        restored = pipeline._restore_translated_cues(
            state,
            source_cues,
            "manual_subtitle",
            TranslationControlConfig(style_preset="creator"),
        )

        self.assertEqual(restored, [])


if __name__ == "__main__":
    unittest.main()
