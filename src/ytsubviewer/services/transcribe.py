from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from ytsubviewer.config import Settings
from ytsubviewer.models import SubtitleCue
from ytsubviewer.runtime import configure_windows_dll_search_path
from ytsubviewer.utils import normalize_english_text, program_exists


logger = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract_audio(self, video_path: Path, work_dir: Path) -> Path:
        if not program_exists(self.settings.ffmpeg_command):
            raise RuntimeError("系统中未找到 ffmpeg，无法抽取音频。")

        audio_path = work_dir / "audio.wav"
        command = [
            self.settings.ffmpeg_command,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg 抽取音频失败：{completed.stderr.strip()}")
        return audio_path

    def transcribe(
        self,
        audio_path: Path,
        *,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ) -> list[SubtitleCue]:
        attempts = self._build_attempts(primary_model=primary_model, fallback_model=fallback_model)
        last_error: Exception | None = None

        for index, (model_name, device, compute_type) in enumerate(attempts, start=1):
            logger.info(
                "Starting transcription attempt %s/%s with model=%s device=%s compute_type=%s audio=%s",
                index,
                len(attempts),
                model_name,
                device,
                compute_type,
                audio_path,
            )
            try:
                cues = self._transcribe_with_model(audio_path, model_name, device=device, compute_type=compute_type)
                logger.info(
                    "Transcription succeeded with model=%s device=%s compute_type=%s cues=%s",
                    model_name,
                    device,
                    compute_type,
                    len(cues),
                )
                return cues
            except Exception as exc:
                wrapped = exc if isinstance(exc, RuntimeError) else RuntimeError(str(exc))
                last_error = wrapped
                logger.warning(
                    "Transcription attempt failed with model=%s device=%s compute_type=%s: %s",
                    model_name,
                    device,
                    compute_type,
                    wrapped,
                    exc_info=True,
                )
                if not self._should_retry(wrapped, device):
                    raise wrapped

        if last_error is not None:
            raise last_error
        raise RuntimeError("转写失败，未获得任何模型输出。")

    def _build_attempts(
        self,
        *,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ) -> list[tuple[str, str, str]]:
        attempts: list[tuple[str, str, str]] = []
        primary_model = (primary_model or self.settings.whisper_model).strip() or self.settings.whisper_model
        fallback_model = (fallback_model or self.settings.whisper_fallback_model).strip() or self.settings.whisper_fallback_model
        configure_windows_dll_search_path()
        try:
            import ctranslate2

            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            has_cuda = False

        if has_cuda:
            attempts.extend(
                [
                    (primary_model, "cuda", "float16"),
                    (fallback_model, "cuda", "float16"),
                ]
            )

        attempts.extend(
            [
                (fallback_model, "cpu", "int8"),
                (primary_model, "cpu", "int8"),
            ]
        )

        deduped: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for attempt in attempts:
            if attempt in seen:
                continue
            seen.add(attempt)
            deduped.append(attempt)
        return deduped

    def _transcribe_with_model(
        self,
        audio_path: Path,
        model_name: str,
        *,
        device: str,
        compute_type: str,
        timeout_seconds: float | None = None,
    ) -> list[SubtitleCue]:
        result: list[list[SubtitleCue]] = []
        error: list[BaseException] = []

        def _run() -> None:
            try:
                configure_windows_dll_search_path()
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise RuntimeError(
                        "缺少本地转写依赖，请先执行 `pip install -r requirements.txt` 安装 faster-whisper。"
                    ) from exc

                model = WhisperModel(model_name, device=device, compute_type=compute_type)
                with audio_path.open("rb") as audio_file:
                    segments, _ = model.transcribe(
                        audio_file,
                        language="en",
                        vad_filter=True,
                        beam_size=5,
                        word_timestamps=True,
                        condition_on_previous_text=False,
                    )
                    cues: list[SubtitleCue] = []
                    cue_id = 1
                    for segment in segments:
                        if segment.words:
                            cues.extend(self._split_segment_with_words(segment.words, cue_id))
                            cue_id = len(cues) + 1
                        else:
                            text = normalize_english_text(segment.text or "")
                            if text:
                                cues.append(
                                    SubtitleCue(
                                        id=cue_id,
                                        start=float(segment.start),
                                        end=float(segment.end),
                                        source_text=text,
                                    )
                                )
                                cue_id += 1
                result.append(cues)
            except Exception as exc:
                error.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            raise RuntimeError(f"转写超时（已超过 {timeout_seconds} 秒）")
        if error:
            raise error[0]
        cues = result[0] if result else []
        if not cues:
            raise RuntimeError("转写结果为空。")
        return cues

    @staticmethod
    def _should_retry(exc: Exception, device: str) -> bool:
        if device == "cuda":
            return True
        return False

    def _split_segment_with_words(self, words: list, start_id: int) -> list[SubtitleCue]:
        cues: list[SubtitleCue] = []
        current_words: list = []
        cue_id = start_id

        def flush() -> None:
            nonlocal cue_id, current_words
            if not current_words:
                return
            text = normalize_english_text(" ".join(word.word.strip() for word in current_words))
            if text:
                cues.append(
                    SubtitleCue(
                        id=cue_id,
                        start=float(current_words[0].start),
                        end=float(current_words[-1].end),
                        source_text=text,
                    )
                )
                cue_id += 1
            current_words = []

        for index, word in enumerate(words):
            current_words.append(word)
            next_word = words[index + 1] if index + 1 < len(words) else None
            current_text = normalize_english_text(" ".join(item.word.strip() for item in current_words))
            gap = 0.0 if next_word is None else float(next_word.start - word.end)
            punctuation_break = any(current_text.endswith(marker) for marker in [".", "?", "!", ";", ":"])
            length_break = len(current_text) >= 84
            pause_break = gap >= 0.65
            if punctuation_break or length_break or pause_break:
                flush()

        flush()
        return cues
