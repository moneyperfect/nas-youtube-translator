import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import Settings
from ytsubviewer.job_state import artifacts_from_state, load_job_state, save_job_state
from ytsubviewer.models import SubtitleCue, VideoMetadata
from ytsubviewer.pipeline import SubtitlePipeline
from ytsubviewer.services.export import VideoExportService
from ytsubviewer.services.transcribe import TranscriptionService
from ytsubviewer.services.translate import DeepSeekTranslator
from ytsubviewer.subtitle_processing import (
    build_bilingual_cues,
    build_bilingual_cues_from_tracks,
    parse_srt_file,
    polish_translated_cues,
    write_ass,
    write_srt,
)
from ytsubviewer.utils import (
    extract_json_payload,
    format_eta,
    seconds_to_ass_timestamp,
    seconds_to_srt_timestamp,
    wrap_cjk_text,
)


class SubtitleProcessingTests(unittest.TestCase):
    def test_seconds_to_srt_timestamp(self) -> None:
        self.assertEqual(seconds_to_srt_timestamp(3723.456), "01:02:03,456")

    def test_seconds_to_ass_timestamp(self) -> None:
        self.assertEqual(seconds_to_ass_timestamp(3723.456), "1:02:03.46")

    def test_wrap_cjk_text_prefers_breaks(self) -> None:
        text = "这是一个比较长的中文句子，需要被整理成更适合观看字幕的两行内容。"
        wrapped = wrap_cjk_text(text, width=10, max_lines=2)
        self.assertIn("\n", wrapped)
        self.assertLessEqual(len(wrapped.splitlines()), 2)

    def test_format_eta(self) -> None:
        self.assertEqual(format_eta(65), "1分5秒")

    def test_polish_translated_cues_can_split_long_text(self) -> None:
        cue = SubtitleCue(
            id=1,
            start=0.0,
            end=8.0,
            source_text="Long source",
            target_text="这是第一句，这是第二句，这是第三句，这是第四句。",
        )
        polished = polish_translated_cues([cue], width=10, max_lines=2)
        self.assertGreaterEqual(len(polished), 2)
        self.assertTrue(all(item.target_text for item in polished))

    def test_write_srt(self) -> None:
        cues = [
            SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello", target_text="你好"),
            SubtitleCue(id=2, start=2.0, end=4.0, source_text="World", target_text="世界"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_srt(cues, Path(temp_dir) / "sample.srt")
            content = path.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:02,000", content)
            self.assertIn("你好", content)

    def test_write_ass_supports_bilingual_lines(self) -> None:
        cues = [
            SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello there", target_text="你好"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_ass(cues, Path(temp_dir) / "sample.bilingual.ass", bilingual=True)
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.00,Bilingual", content)
            self.assertIn("Hello there\\N你好", content)

    def test_build_bilingual_cues_splits_long_text_without_extra_lines(self) -> None:
        cue = SubtitleCue(
            id=1,
            start=0.0,
            end=10.0,
            source_text="This is a very long English sentence that should be split for bilingual subtitle display.",
            target_text="这是一段非常长的中文句子，需要为了双语字幕显示而拆分成更小的段落。",
        )
        bilingual = build_bilingual_cues([cue], english_max_chars=24, chinese_max_chars=10)
        self.assertGreaterEqual(len(bilingual), 2)
        self.assertTrue(all("\n" not in item.source_text for item in bilingual))
        self.assertTrue(all("\n" not in item.target_text for item in bilingual))

    def test_build_bilingual_cues_from_tracks_uses_time_overlap(self) -> None:
        english_cues = [
            SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello there."),
            SubtitleCue(id=2, start=2.0, end=4.0, source_text="General Kenobi."),
        ]
        chinese_cues = [
            SubtitleCue(id=1, start=0.0, end=2.2, source_text="", target_text="你好"),
            SubtitleCue(id=2, start=2.2, end=4.0, source_text="", target_text="将军"),
        ]
        bilingual = build_bilingual_cues_from_tracks(english_cues, chinese_cues)
        self.assertEqual(len(bilingual), 2)
        self.assertIn("Hello there.", bilingual[0].source_text)

    def test_parse_srt_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.srt"
            path.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:02,000 --> 00:00:04,000\n世界\n",
                encoding="utf-8",
            )
            cues = parse_srt_file(path)
            self.assertEqual(len(cues), 2)
            self.assertEqual(cues[1].target_text, "世界")


class TranslationResponseTests(unittest.TestCase):
    def test_extract_json_payload(self) -> None:
        payload = extract_json_payload(
            """```json
            [{"id": 1, "translation": "你好"}]
            ```"""
        )
        self.assertEqual(payload[0]["translation"], "你好")

    def test_build_batches_respects_limits(self) -> None:
        settings = Settings(
            translation_batch_size=2,
            translation_max_chars=50,
        )
        translator = DeepSeekTranslator(settings)
        cues = [
            SubtitleCue(id=1, start=0.0, end=1.0, source_text="one"),
            SubtitleCue(id=2, start=1.0, end=2.0, source_text="two"),
            SubtitleCue(id=3, start=2.0, end=3.0, source_text="three"),
        ]
        batches = translator.build_batches(cues)
        self.assertEqual(len(batches), 2)
        self.assertEqual([cue.id for cue in batches[0]], [1, 2])
        self.assertEqual([cue.id for cue in batches[1]], [3])

    def test_translate_batch_falls_back_to_split_batches(self) -> None:
        settings = Settings(deepseek_api_key="dummy")
        translator = DeepSeekTranslator(settings)
        cues = [
            SubtitleCue(id=1, start=0.0, end=1.0, source_text="one"),
            SubtitleCue(id=2, start=1.0, end=2.0, source_text="two"),
        ]

        def fake_request(batch, repair=False):
            if len(batch) == 2:
                return [{"id": 1, "translation": "一"}]
            return [{"id": batch[0].id, "translation": "好"}]

        with mock.patch.object(translator, "_request_translation_rows", side_effect=fake_request):
            result = translator._translate_batch(cues)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].target_text, "好")
        self.assertEqual(result[1].target_text, "好")

    def test_translation_needs_repair_for_leftover_english_sentence(self) -> None:
        translator = DeepSeekTranslator(Settings())
        cue = SubtitleCue(
            id=1,
            start=0.0,
            end=1.0,
            source_text="This combination of dynamics is what makes the Nvidia architecture expand its reach.",
            target_text="This combination of dynamics is what makes the Nvidia architecture expand its reach.",
        )
        self.assertTrue(translator.translation_needs_repair(cue))

    def test_repair_low_quality_translations_only_rewrites_suspicious_lines(self) -> None:
        translator = DeepSeekTranslator(Settings(deepseek_api_key="dummy"))
        cues = [
            SubtitleCue(id=1, start=0.0, end=1.0, source_text="Hello there.", target_text="你好。"),
            SubtitleCue(
                id=2,
                start=1.0,
                end=2.0,
                source_text="This combination of dynamics is what makes the Nvidia architecture expand its reach.",
                target_text="This combination of dynamics is what makes the Nvidia architecture expand its reach.",
            ),
        ]

        with mock.patch.object(
            translator,
            "_translate_batch",
            side_effect=lambda batch, repair=False: [batch[0].clone(target_text="这种动态组合让英伟达架构得以扩展能力。")],
        ) as patched:
            repaired = translator.repair_low_quality_translations(cues)

        self.assertEqual(repaired[0].target_text, "你好。")
        self.assertEqual(repaired[1].target_text, "这种动态组合让英伟达架构得以扩展能力。")
        patched.assert_called_once()

    def test_artifacts_from_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            video = base / "video.mp4"
            subtitle = base / "video.zh-CN.srt"
            chinese_ass = base / "video.zh-CN.ass"
            bilingual_ass = base / "video.bilingual.ass"
            burned_chinese = base / "video.zh-CN.hardsub.mp4"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text("subtitle", encoding="utf-8")
            chinese_ass.write_text("ass", encoding="utf-8")
            bilingual_ass.write_text("ass", encoding="utf-8")
            burned_chinese.write_text("video", encoding="utf-8")
            state = {
                "video_id": "abc",
                "title": "demo",
                "source_kind": "manual_subtitle",
                "video_path": str(video),
                "english_subtitle_path": "",
                "chinese_subtitle_path": str(subtitle),
                "chinese_ass_path": str(chinese_ass),
                "bilingual_ass_path": str(bilingual_ass),
                "burned_chinese_video_path": str(burned_chinese),
                "burned_bilingual_video_path": "",
                "work_dir": str(base),
            }
            artifacts = artifacts_from_state(state)
            self.assertIsNotNone(artifacts)
            assert artifacts is not None
            self.assertEqual(artifacts.video_path, video)
            self.assertEqual(artifacts.chinese_ass_path, chinese_ass)
            self.assertEqual(artifacts.bilingual_ass_path, bilingual_ass)
            self.assertEqual(artifacts.burned_chinese_video_path, burned_chinese)


class TranscriptionTests(unittest.TestCase):
    def test_transcribe_uses_binary_file_handle_for_audio_input(self) -> None:
        service = TranscriptionService(Settings())
        captured = {}

        class FakeModel:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def transcribe(self, audio_input, **kwargs):
                captured["has_read"] = hasattr(audio_input, "read")
                captured["name"] = getattr(audio_input, "name", "")
                return [SimpleNamespace(words=[], text="hello", start=0.0, end=1.0)], None

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "sample.wav"
            audio_path.write_bytes(b"fake")
            fake_module = SimpleNamespace(WhisperModel=FakeModel)
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
                result = service._transcribe_with_model(audio_path, "tiny", device="cpu", compute_type="int8")

        self.assertEqual(len(result), 1)
        self.assertTrue(captured["has_read"])
        self.assertTrue(captured["name"].endswith("sample.wav"))

    def test_transcribe_falls_back_to_cpu_when_cuda_runtime_missing(self) -> None:
        service = TranscriptionService(Settings())
        expected = [SubtitleCue(id=1, start=0.0, end=1.0, source_text="hello")]

        def fake_transcribe(audio_path, model_name, *, device, compute_type):
            if device == "cuda":
                raise RuntimeError("GPU 转写初始化失败：Library cublas64_12.dll is not found or cannot be loaded")
            return expected

        with mock.patch.object(service, "_transcribe_with_model", side_effect=fake_transcribe):
            result = service.transcribe(Path("sample.wav"))

        self.assertEqual(result, expected)

    def test_transcribe_keeps_failing_when_cpu_attempt_also_fails(self) -> None:
        service = TranscriptionService(Settings())

        def fake_transcribe(audio_path, model_name, *, device, compute_type):
            if device == "cuda":
                raise RuntimeError("GPU 转写初始化失败：Library cublas64_12.dll is not found or cannot be loaded")
            raise RuntimeError("CPU 转写初始化失败：model load failed")

        with mock.patch.object(service, "_transcribe_with_model", side_effect=fake_transcribe):
            with self.assertRaises(RuntimeError) as context:
                service.transcribe(Path("sample.wav"))

        self.assertIn("CPU 转写初始化失败", str(context.exception))


class PipelineStateTests(unittest.TestCase):
    def test_find_existing_artifacts_can_reuse_completed_files_without_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="abc123",
                title="demo",
                duration_seconds=4,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            video = work_dir / "video.mp4"
            english = work_dir / "source.en.vtt"
            subtitle = work_dir / "demo.zh-CN.srt"
            video.write_text("video", encoding="utf-8")
            english.write_text("WEBVTT", encoding="utf-8")
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:04,000\n完整字幕\n",
                encoding="utf-8",
            )

            artifacts = pipeline.find_existing_artifacts(metadata)

            self.assertIsNotNone(artifacts)
            assert artifacts is not None
            self.assertEqual(artifacts.source_kind, "manual_subtitle")
            self.assertEqual(artifacts.video_path, video)
            self.assertEqual(artifacts.english_subtitle_path, english)
            self.assertEqual(artifacts.chinese_subtitle_path, subtitle)

    def test_find_existing_artifacts_ignores_incomplete_srt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="short123",
                title="demo",
                duration_seconds=1800,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            (work_dir / "video.mp4").write_text("video", encoding="utf-8")
            (work_dir / "demo.zh-CN.srt").write_text(
                "1\n00:00:00,000 --> 00:03:00,000\n只有前三分钟\n",
                encoding="utf-8",
            )

            artifacts = pipeline.find_existing_artifacts(metadata)
            self.assertIsNone(artifacts)

    def test_find_existing_artifacts_ignores_srt_with_untranslated_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="tail123",
                title="demo",
                duration_seconds=120,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            (work_dir / "video.mp4").write_text("video", encoding="utf-8")
            (work_dir / "demo.zh-CN.srt").write_text(
                (
                    "1\n00:00:00,000 --> 00:00:20,000\n第一段中文\n\n"
                    "2\n00:00:20,000 --> 00:00:40,000\n第二段中文\n\n"
                    "3\n00:00:40,000 --> 00:01:00,000\n第三段中文\n\n"
                    "4\n00:01:00,000 --> 00:01:20,000\nFourth segment remains in English.\n\n"
                    "5\n00:01:20,000 --> 00:01:40,000\nFifth segment remains in English.\n\n"
                    "6\n00:01:40,000 --> 00:02:00,000\nSixth segment remains in English.\n"
                ),
                encoding="utf-8",
            )

            artifacts = pipeline.find_existing_artifacts(metadata)
            self.assertIsNone(artifacts)

    def test_generate_events_resume_does_not_resplit_saved_source_cues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                deepseek_api_key="dummy",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="resume123",
                title="resume-demo",
                duration_seconds=4,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            video = work_dir / "video.mp4"
            english = work_dir / "source.en.vtt"
            video.write_text("video", encoding="utf-8")
            english.write_text("WEBVTT", encoding="utf-8")

            source_cues = [
                SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello there."),
                SubtitleCue(id=2, start=2.0, end=4.0, source_text="General Kenobi."),
            ]
            translated_cues = [source_cues[0].clone(target_text="你好。")]
            save_job_state(
                work_dir,
                {
                    "version": 1,
                    "status": "translating",
                    "video_id": metadata.video_id,
                    "title": metadata.title,
                    "source_kind": "manual_subtitle",
                    "video_path": str(video),
                    "english_subtitle_path": str(english),
                    "chinese_subtitle_path": "",
                    "chinese_ass_path": "",
                    "bilingual_ass_path": "",
                    "burned_chinese_video_path": "",
                    "burned_bilingual_video_path": "",
                    "work_dir": str(work_dir),
                    "source_cues": [cue.to_dict() for cue in source_cues],
                    "translated_cues": [cue.to_dict() for cue in translated_cues],
                },
            )

            mock_translator = mock.Mock()
            mock_translator.build_batches.return_value = [[source_cues[1]]]
            mock_translator.translate_cues_stream.return_value = iter(
                [(1, 1, [source_cues[1].clone(target_text="肯诺比将军。")])]
            )
            mock_translator.suspicious_translation_ids.return_value = []

            with (
                mock.patch.object(pipeline.youtube, "extract_metadata", return_value=metadata),
                mock.patch.object(pipeline.youtube, "prepare_work_dir", return_value=work_dir),
                mock.patch.object(pipeline.youtube, "find_existing_video", return_value=video),
                mock.patch.object(pipeline.youtube, "find_existing_subtitle", return_value=english),
                mock.patch.object(pipeline.youtube, "find_existing_chinese_subtitle", return_value=None),
                mock.patch.object(pipeline.youtube, "find_existing_burned_chinese_video", return_value=None),
                mock.patch.object(pipeline.youtube, "find_existing_burned_bilingual_video", return_value=None),
                mock.patch(
                    "ytsubviewer.pipeline.split_source_cues",
                    side_effect=AssertionError("resume path should not re-split source cues"),
                ),
                mock.patch.object(pipeline, "_build_translator", return_value=mock_translator),
            ):
                events = list(pipeline.generate_events("https://www.youtube.com/watch?v=resume123"))

            self.assertTrue(any("继续翻译，已复用 1/2 条字幕" in event.message for event in events))
            self.assertIsNotNone(events[-1].artifacts)

            state = load_job_state(work_dir)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state["status"], "completed")
            self.assertEqual(len(state["source_cues"]), 2)
            self.assertEqual(len(state["translated_cues"]), 2)
            self.assertTrue(state["chinese_ass_path"])
            self.assertTrue(state["bilingual_ass_path"])

    def test_generate_events_prefers_automatic_subtitles_before_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
                deepseek_api_key="dummy",
                prefer_automatic_subtitles=True,
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="auto123",
                title="auto-demo",
                duration_seconds=4,
                thumbnail_url=None,
                manual_english_subtitle_lang=None,
                automatic_english_subtitle_lang="en",
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            video = work_dir / "video.mp4"
            english = work_dir / "source.auto.en.vtt"
            video.write_text("video", encoding="utf-8")

            translated = [
                SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello there.", target_text="你好。"),
                SubtitleCue(id=2, start=2.0, end=4.0, source_text="General Kenobi.", target_text="肯诺比将军。"),
            ]

            mock_translator = mock.Mock()
            mock_translator.build_batches.return_value = [[translated[0], translated[1]]]
            mock_translator.translate_cues_stream.return_value = iter([(1, 1, translated)])
            mock_translator.suspicious_translation_ids.return_value = []

            def fake_download_automatic_subtitle(_url: str, _lang: str, _work_dir: Path) -> Path:
                english.write_text(
                    (
                        "WEBVTT\n\n"
                        "00:00:00.000 --> 00:00:02.000\n"
                        "Hello there.\n\n"
                        "00:00:02.000 --> 00:00:04.000\n"
                        "General Kenobi.\n"
                    ),
                    encoding="utf-8",
                )
                return english

            with (
                mock.patch.object(pipeline.youtube, "extract_metadata", return_value=metadata),
                mock.patch.object(pipeline.youtube, "prepare_work_dir", return_value=work_dir),
                mock.patch.object(pipeline.youtube, "find_existing_video", return_value=video),
                mock.patch.object(pipeline.youtube, "download_automatic_subtitle", side_effect=fake_download_automatic_subtitle),
                mock.patch.object(pipeline.youtube, "find_existing_burned_chinese_video", return_value=None),
                mock.patch.object(pipeline.youtube, "find_existing_burned_bilingual_video", return_value=None),
                mock.patch.object(pipeline.transcriber, "transcribe", side_effect=AssertionError("should not transcribe")),
                mock.patch.object(pipeline, "_build_translator", return_value=mock_translator),
            ):
                events = list(pipeline.generate_events("https://www.youtube.com/watch?v=auto123"))

            self.assertTrue(any("下载 YouTube 自动英文字幕" in event.message for event in events))
            self.assertTrue(any("解析 YouTube 自动英文字幕" in event.message for event in events))
            self.assertEqual(events[-1].artifacts.source_kind, "automatic_subtitle")
            mock_translator.translate_cues_stream.assert_called_once()

    def test_ensure_subtitle_artifacts_can_backfill_ass_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="backfill123",
                title="backfill-demo",
                duration_seconds=4,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            video = work_dir / "video.mp4"
            english = work_dir / "source.en.vtt"
            chinese = work_dir / "backfill-demo.zh-CN.srt"
            video.write_text("video", encoding="utf-8")
            english.write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello there.\n\n00:00:02.000 --> 00:00:04.000\nGeneral Kenobi.\n",
                encoding="utf-8",
            )
            chinese.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:02,000 --> 00:00:04,000\n将军\n",
                encoding="utf-8",
            )

            artifacts = pipeline.find_existing_artifacts(metadata)
            self.assertIsNotNone(artifacts)
            assert artifacts is not None
            artifacts = pipeline.ensure_subtitle_artifacts(artifacts)
            self.assertIsNotNone(artifacts.chinese_ass_path)
            self.assertIsNotNone(artifacts.bilingual_ass_path)
            assert artifacts.chinese_ass_path is not None
            assert artifacts.bilingual_ass_path is not None
            self.assertTrue(artifacts.chinese_ass_path.exists())
            self.assertTrue(artifacts.bilingual_ass_path.exists())

    def test_ensure_subtitle_artifacts_preserves_saved_cues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="preserve123",
                title="preserve-demo",
                duration_seconds=4,
                thumbnail_url=None,
                manual_english_subtitle_lang="en",
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            video = work_dir / "video.mp4"
            subtitle = work_dir / "preserve-demo.zh-CN.srt"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n2\n00:00:02,000 --> 00:00:04,000\n世界\n",
                encoding="utf-8",
            )
            source_cues = [
                SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello."),
                SubtitleCue(id=2, start=2.0, end=4.0, source_text="World."),
            ]
            translated_cues = [
                source_cues[0].clone(target_text="你好"),
                source_cues[1].clone(target_text="世界"),
            ]
            save_job_state(
                work_dir,
                {
                    "version": 1,
                    "status": "completed",
                    "video_id": metadata.video_id,
                    "title": metadata.title,
                    "source_kind": "manual_subtitle",
                    "video_path": str(video),
                    "english_subtitle_path": "",
                    "chinese_subtitle_path": str(subtitle),
                    "chinese_ass_path": "",
                    "bilingual_ass_path": "",
                    "burned_chinese_video_path": "",
                    "burned_bilingual_video_path": "",
                    "duration_seconds": metadata.duration_seconds,
                    "work_dir": str(work_dir),
                    "source_cues": [cue.to_dict() for cue in source_cues],
                    "translated_cues": [cue.to_dict() for cue in translated_cues],
                },
            )

            artifacts = pipeline.find_existing_artifacts(metadata)
            self.assertIsNotNone(artifacts)
            assert artifacts is not None
            pipeline.ensure_subtitle_artifacts(artifacts)

            state = load_job_state(work_dir)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(len(state["source_cues"]), 2)
            self.assertEqual(len(state["translated_cues"]), 2)

    def test_ensure_subtitle_artifacts_uses_source_and_srt_when_saved_translations_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            settings = Settings(
                project_root=base,
                workspace_dir=base / "workspace",
                jobs_dir=base / "workspace" / "jobs",
            )
            pipeline = SubtitlePipeline(settings)
            metadata = VideoMetadata(
                video_id="fallback123",
                title="fallback-demo",
                duration_seconds=4,
                thumbnail_url=None,
                manual_english_subtitle_lang=None,
                automatic_english_subtitle_lang=None,
            )
            work_dir = pipeline.youtube.prepare_work_dir(metadata)
            video = work_dir / "video.mp4"
            subtitle = work_dir / "fallback-demo.zh-CN.srt"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n第一句中文\n\n2\n00:00:02,000 --> 00:00:04,000\n第二句中文\n",
                encoding="utf-8",
            )
            source_cues = [
                SubtitleCue(id=1, start=0.0, end=2.0, source_text="First English sentence."),
                SubtitleCue(id=2, start=2.0, end=4.0, source_text="Second English sentence."),
            ]
            translated_cues = [source_cues[0].clone(target_text="第一句中文")]
            save_job_state(
                work_dir,
                {
                    "version": 1,
                    "status": "completed",
                    "video_id": metadata.video_id,
                    "title": metadata.title,
                    "source_kind": "faster_whisper",
                    "video_path": str(video),
                    "english_subtitle_path": "",
                    "chinese_subtitle_path": str(subtitle),
                    "chinese_ass_path": "",
                    "bilingual_ass_path": "",
                    "burned_chinese_video_path": "",
                    "burned_bilingual_video_path": "",
                    "duration_seconds": metadata.duration_seconds,
                    "work_dir": str(work_dir),
                    "source_cues": [cue.to_dict() for cue in source_cues],
                    "translated_cues": [cue.to_dict() for cue in translated_cues],
                },
            )

            artifacts = pipeline.find_existing_artifacts(metadata)
            assert artifacts is not None
            artifacts = pipeline.ensure_subtitle_artifacts(artifacts)

            assert artifacts.bilingual_ass_path is not None
            content = artifacts.bilingual_ass_path.read_text(encoding="utf-8-sig")
            self.assertIn("First English sentence.\\N第一句中文", content)
            self.assertIn("Second English sentence.\\N第二句中文", content)


class ExportTests(unittest.TestCase):
    def test_build_burn_command_uses_ass_filter(self) -> None:
        settings = Settings(ffmpeg_command="ffmpeg")
        exporter = VideoExportService(settings)
        command = exporter.build_burn_command(
            Path("video.mp4"),
            Path("sample.zh-CN.ass"),
            Path("output.mp4"),
        )
        self.assertIn("ass='sample.zh-CN.ass'", command)
        self.assertEqual(command[-1], "output.mp4")

    def test_prepare_safe_filter_subtitle_copies_problematic_filename(self) -> None:
        settings = Settings(ffmpeg_command="ffmpeg")
        exporter = VideoExportService(settings)
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            subtitle = work_dir / "Nvidia's Future, Physical AI.bilingual.ass"
            subtitle.write_text("[Script Info]\n", encoding="utf-8")

            safe_path = exporter._prepare_safe_filter_subtitle(subtitle, work_dir)

            self.assertNotEqual(safe_path, subtitle)
            self.assertTrue(safe_path.exists())
            self.assertTrue(safe_path.name.startswith("_burn_input_"))
            self.assertEqual(safe_path.read_text(encoding="utf-8"), "[Script Info]\n")


if __name__ == "__main__":
    unittest.main()
