import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import Settings
from ytsubviewer.job_state import load_job_state
from ytsubviewer.models import SubtitleCue, VideoMetadata
from ytsubviewer.pipeline import SubtitlePipeline
from ytsubviewer.services.transcribe import TranscriptionService


class TranscriptionResilienceTests(unittest.TestCase):
    def test_transcribe_retries_after_gpu_failure(self) -> None:
        service = TranscriptionService(Settings())
        audio_path = Path("demo.wav")
        cues = [SubtitleCue(id=1, start=0.0, end=1.0, source_text="hello")]

        with mock.patch.object(
            service,
            "_build_attempts",
            return_value=[
                ("distil-large-v3", "cuda", "float16"),
                ("medium", "cpu", "int8"),
            ],
        ), mock.patch.object(
            service,
            "_transcribe_with_model",
            side_effect=[RuntimeError("GPU decode failure"), cues],
        ):
            restored = service.transcribe(audio_path)

        self.assertEqual(restored, cues)

    def test_transcribe_wraps_generator_iteration_failure(self) -> None:
        class FakeModel:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def transcribe(self, *_args, **_kwargs):
                def _segments():
                    yield SimpleNamespace(words=[], text="Hello world", start=0.0, end=1.0)
                    raise ValueError("decoder stream broke")

                return _segments(), None

        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeModel

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            audio_path.write_bytes(b"fake-audio")
            service = TranscriptionService(Settings())

            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
                with self.assertRaises(RuntimeError) as ctx:
                    service._transcribe_with_model(
                        audio_path,
                        "distil-large-v3",
                        device="cuda",
                        compute_type="float16",
                    )

        self.assertIn("decoder stream broke", str(ctx.exception))
        self.assertIn("GPU 转写失败", str(ctx.exception))

    def test_pipeline_persists_failed_state_when_transcription_stage_crashes(self) -> None:
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
            video_path = work_dir / "video.mp4"
            audio_path = work_dir / "audio.wav"
            video_path.write_text("video", encoding="utf-8")
            audio_path.write_text("audio", encoding="utf-8")

            metadata = VideoMetadata(
                video_id="demo",
                title="Demo",
                duration_seconds=600,
                thumbnail_url=None,
                manual_english_subtitle_lang=None,
                automatic_english_subtitle_lang=None,
            )

            pipeline.youtube = SimpleNamespace(
                extract_metadata=lambda _url: metadata,
                prepare_work_dir=lambda _metadata: work_dir,
                candidate_work_dirs=lambda _metadata: [work_dir],
                find_existing_video=lambda _work_dir: video_path,
                find_existing_subtitle=lambda _work_dir: None,
                find_existing_chinese_subtitle=lambda _work_dir: None,
                find_existing_quality_report=lambda _work_dir: None,
            )
            pipeline.transcriber = SimpleNamespace(
                extract_audio=lambda _video_path, _work_dir: audio_path,
                transcribe=mock.Mock(side_effect=RuntimeError("simulated transcription failure")),
            )

            with self.assertRaises(RuntimeError) as ctx:
                list(pipeline.generate_events("https://example.com/watch?v=demo"))

            self.assertIn("simulated transcription failure", str(ctx.exception))
            state = load_job_state(work_dir)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["source_kind"], "faster_whisper")
            self.assertEqual(state["video_path"], str(video_path))
            self.assertIn("simulated transcription failure", state["last_error"])


if __name__ == "__main__":
    unittest.main()
