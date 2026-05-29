import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import Settings
from ytsubviewer.job_state import load_job_state
from ytsubviewer.models import SubtitleCue, TranslationControlConfig, VideoMetadata
from ytsubviewer.pipeline import SubtitlePipeline
from ytsubviewer.subtitle_processing import parse_srt_file


class EditorFlowTests(unittest.TestCase):
    def _build_pipeline_with_job(self) -> tuple[SubtitlePipeline, Path, dict]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        base = Path(temp_dir.name)
        settings = Settings(
            project_root=base,
            workspace_dir=base / "workspace",
            jobs_dir=base / "workspace" / "jobs",
        )
        pipeline = SubtitlePipeline(settings)
        work_dir = settings.jobs_dir / "demo_job"
        work_dir.mkdir(parents=True, exist_ok=True)

        video_path = work_dir / "video.mp4"
        subtitle_path = work_dir / "demo.zh-CN.srt"
        video_path.write_text("video", encoding="utf-8")
        subtitle_path.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:02,000 --> 00:00:04,000\n世界\n",
            encoding="utf-8",
        )

        source_cues = [
            SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello there."),
            SubtitleCue(id=2, start=2.0, end=4.0, source_text="General Kenobi."),
        ]
        translated_cues = [
            source_cues[0].clone(target_text="你好"),
            source_cues[1].clone(target_text="世界"),
        ]
        metadata = VideoMetadata(
            video_id="demo",
            title="demo",
            duration_seconds=4,
            thumbnail_url=None,
            manual_english_subtitle_lang=None,
            automatic_english_subtitle_lang=None,
        )
        controls = TranslationControlConfig(style_preset="creator")
        pipeline._save_state(
            work_dir=work_dir,
            metadata=metadata,
            status="completed",
            source_kind="manual_subtitle",
            video_path=video_path,
            english_subtitle_path=None,
            chinese_subtitle_path=subtitle_path,
            chinese_ass_path=None,
            bilingual_ass_path=None,
            burned_chinese_video_path=None,
            burned_bilingual_video_path=None,
            source_cues=source_cues,
            translated_cues=translated_cues,
            translation_controls=controls,
            quality_report_path=None,
            quality_report=None,
        )
        state = load_job_state(work_dir)
        assert state is not None
        return pipeline, work_dir, state

    def test_update_cue_translation_rewrites_outputs(self) -> None:
        pipeline, work_dir, state = self._build_pipeline_with_job()

        artifacts = pipeline.update_cue_translation(state, 2, "绝地将军")

        saved_state = load_job_state(work_dir)
        self.assertIsNotNone(saved_state)
        assert saved_state is not None
        self.assertEqual(saved_state["edited_cue_ids"], [2])
        self.assertEqual(saved_state["translated_cues"][1]["target_text"], "绝地将军")
        self.assertTrue(saved_state["quality_report_path"])
        self.assertTrue(Path(saved_state["chinese_ass_path"]).exists())
        self.assertTrue(Path(saved_state["bilingual_ass_path"]).exists())
        self.assertIsNone(artifacts.burned_chinese_video_path)
        self.assertIsNone(artifacts.burned_bilingual_video_path)

        srt_cues = parse_srt_file(Path(saved_state["chinese_subtitle_path"]))
        self.assertEqual(srt_cues[1].target_text.replace("\n", ""), "绝地将军")

    def test_retranslate_cue_updates_single_line(self) -> None:
        pipeline, work_dir, state = self._build_pipeline_with_job()
        translated = SubtitleCue(
            id=1,
            start=0.0,
            end=2.0,
            source_text="Hello there.",
            target_text="你好啊",
        )
        fake_translator = mock.Mock()
        fake_translator.translate_cues.return_value = [translated]

        with mock.patch.object(pipeline, "_build_translator", return_value=fake_translator):
            pipeline.retranslate_cue(state, 1)

        saved_state = load_job_state(work_dir)
        self.assertIsNotNone(saved_state)
        assert saved_state is not None
        self.assertEqual(saved_state["edited_cue_ids"], [1])
        self.assertEqual(saved_state["translated_cues"][0]["target_text"], "你好啊")
        fake_translator.translate_cues.assert_called_once()


if __name__ == "__main__":
    unittest.main()
