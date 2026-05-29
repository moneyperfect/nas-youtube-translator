from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import re
import threading
from collections.abc import Generator
from dataclasses import replace
from typing import ClassVar

from ytsubviewer.config import Settings
from ytsubviewer.models import (
    SubtitleCue,
    TranslationControlConfig,
    TranslationGlossaryEntry,
    TranslationStylePreset,
)
from ytsubviewer.utils import extract_json_payload, normalize_chinese_text, normalize_english_text


TARGET_LANGUAGES = {
    "zh-CN": {
        "name": "简体中文",
        "name_en": "Simplified Chinese",
        "instructions": ("Translate into concise, natural Simplified Chinese subtitles.",),
        "repair_instructions": ("Rewrite low-quality subtitle lines into natural Simplified Chinese.",),
    },
    "ja": {
        "name": "日语",
        "name_en": "Japanese",
        "instructions": ("Translate into concise, natural Japanese subtitles.",),
        "repair_instructions": ("Rewrite low-quality subtitle lines into natural Japanese.",),
    },
    "ko": {
        "name": "韩语",
        "name_en": "Korean",
        "instructions": ("Translate into concise, natural Korean subtitles.",),
        "repair_instructions": ("Rewrite low-quality subtitle lines into natural Korean.",),
    },
}

BASE_TRANSLATION_RULES = (
    "Return JSON only.",
    'Each item must contain exactly "id" and "translation".',
    "Keep the same number of items, order, and ids as the input.",
    "Do not add notes, explanations, numbering, or bilingual output.",
    "Preserve product names, brands, acronyms, and protected terms exactly when required.",
)

REPAIR_TRANSLATION_RULES = (
    "Fix leftover English and overly literal phrasing.",
    "Keep terminology consistent with the source and glossary.",
)

ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+/#_-]*")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

DEFAULT_ALLOWED_ENGLISH_TERMS = {
    "ai",
    "api",
    "blackwell",
    "cuda",
    "deepseek",
    "dgx",
    "gpt",
    "gpu",
    "gr00t",
    "gtc",
    "h100",
    "h200",
    "isaac",
    "jetson",
    "llm",
    "nvidia",
    "nvlink",
    "omniverse",
    "openai",
    "rtx",
    "tensor",
}


class DeepSeekTranslator:
    STYLE_PRESETS: ClassVar[dict[str, TranslationStylePreset]] = {
        "default": TranslationStylePreset(
            name="default",
            label="Default",
            description="Balanced subtitle style for general long-form English videos.",
            instructions=(
                "Use neutral and natural Simplified Chinese.",
                "Keep the wording concise and easy to read on screen.",
                "Prefer subtitle phrasing over word-for-word translation.",
            ),
            temperature=0.3,
        ),
        "creator": TranslationStylePreset(
            name="creator",
            label="Creator",
            description="Audience-friendly style for creator videos, podcasts, and interviews.",
            instructions=(
                "Sound smooth, polished, and conversational.",
                "Preserve speaker intent and rhythm, but remove awkward literal phrasing.",
                "Use Chinese phrasing that feels publishable for creators.",
            ),
            temperature=0.25,
        ),
        "conference": TranslationStylePreset(
            name="conference",
            label="Conference",
            description="Best for keynote talks and technical presentations.",
            instructions=(
                "Prioritize terminology accuracy and consistency.",
                "Keep the tone professional and concise.",
                "Do not over-localize product names or technical nouns.",
            ),
            temperature=0.2,
        ),
        "technical": TranslationStylePreset(
            name="technical",
            label="Technical",
            description="Best for dense technical content, product demos, and engineering talks.",
            instructions=(
                "Preserve technical meaning precisely.",
                "Keep code, APIs, product names, and model names stable.",
                "Prefer clarity over stylistic embellishment.",
            ),
            temperature=0.2,
        ),
    }

    def __init__(self, settings: Settings, controls: TranslationControlConfig | None = None) -> None:
        self.settings = settings
        self.controls = controls or settings.translation_controls()
        self._local = threading.local()
        self.style_preset = self.get_style_preset(self.controls.style_preset)
        self._protected_terms = self._normalize_terms(
            list(DEFAULT_ALLOWED_ENGLISH_TERMS) + list(self.controls.protected_terms)
        )

    @classmethod
    def available_style_presets(cls) -> dict[str, TranslationStylePreset]:
        return dict(cls.STYLE_PRESETS)

    @classmethod
    def get_style_preset(cls, name: str) -> TranslationStylePreset:
        return cls.STYLE_PRESETS.get((name or "").strip().lower(), cls.STYLE_PRESETS["default"])

    def build_system_prompt(self, *, repair: bool = False) -> str:
        preset = self.style_preset
        target_lang = getattr(self.settings, "target_language", "zh-CN") or "zh-CN"
        lang_config = TARGET_LANGUAGES.get(target_lang, TARGET_LANGUAGES["zh-CN"])
        lines = [
            "You are a professional subtitle localizer for long English talks and interviews.",
            "Follow the output rules strictly:",
            *BASE_TRANSLATION_RULES,
            *lang_config["instructions"],
        ]
        if repair:
            lines.extend(REPAIR_TRANSLATION_RULES)
            lines.extend(lang_config.get("repair_instructions", ()))
        lines.extend(
            [
                f"Style preset: {preset.label} ({preset.name})",
                preset.description,
                "Style instructions:",
            ]
        )
        lines.extend(f"- {instruction}" for instruction in preset.instructions)
        if self.controls.glossary:
            lines.append("Glossary:")
            lines.extend(f"- {self._format_glossary_entry(entry)}" for entry in self.controls.glossary)
        if self._protected_terms:
            lines.append("Protected terms:")
            lines.extend(f"- {term}" for term in sorted(self._protected_terms))
        return "\n".join(lines)

    def build_batches(
        self,
        cues: list[SubtitleCue],
        *,
        batch_size: int | None = None,
        max_chars: int | None = None,
    ) -> list[list[SubtitleCue]]:
        batch_size = batch_size or self.settings.translation_batch_size
        max_chars = max_chars or self.settings.translation_max_chars

        batches: list[list[SubtitleCue]] = []
        current_batch: list[SubtitleCue] = []
        current_chars = 0

        for cue in cues:
            cue_size = len(cue.source_text)
            if current_batch and (len(current_batch) >= batch_size or current_chars + cue_size > max_chars):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append(cue)
            current_chars += cue_size

        if current_batch:
            batches.append(current_batch)
        return batches

    def translate_cues(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        translated: list[SubtitleCue] = []
        for _, _, batch_result in self.translate_cues_stream(cues):
            translated.extend(batch_result)
        return translated

    def translate_cues_stream(
        self,
        cues: list[SubtitleCue],
    ) -> Generator[tuple[int, int, list[SubtitleCue]], None, None]:
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法翻译字幕。")

        batches = self.build_batches(cues)
        total_batches = len(batches)
        worker_count = max(1, min(self.settings.translation_parallel_workers, total_batches))

        if worker_count == 1:
            for batch_index, batch in enumerate(batches, start=1):
                try:
                    translated_batch = self._translate_and_repair_batch(batch)
                except Exception as exc:
                    raise RuntimeError(f"第 {batch_index}/{total_batches} 批翻译失败：{exc}") from exc
                yield batch_index, total_batches, translated_batch
            return

        pending: dict = {}
        buffered: dict[int, list[SubtitleCue]] = {}
        next_batch_to_submit = 1
        next_batch_to_yield = 1

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="translate-batch") as executor:
            while next_batch_to_submit <= total_batches and len(pending) < worker_count:
                batch = batches[next_batch_to_submit - 1]
                future = executor.submit(self._translate_and_repair_batch, batch)
                pending[future] = next_batch_to_submit
                next_batch_to_submit += 1

            while pending:
                completed, _ = wait(set(pending), return_when=FIRST_COMPLETED)
                for future in completed:
                    batch_index = pending.pop(future)
                    try:
                        buffered[batch_index] = future.result()
                    except Exception as exc:
                        raise RuntimeError(f"第 {batch_index}/{total_batches} 批翻译失败：{exc}") from exc

                    while next_batch_to_submit <= total_batches and len(pending) < worker_count:
                        batch = batches[next_batch_to_submit - 1]
                        next_future = executor.submit(self._translate_and_repair_batch, batch)
                        pending[next_future] = next_batch_to_submit
                        next_batch_to_submit += 1

                while next_batch_to_yield in buffered:
                    yield next_batch_to_yield, total_batches, buffered.pop(next_batch_to_yield)
                    next_batch_to_yield += 1

    def repair_low_quality_translations(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        suspicious = [cue for cue in cues if self.translation_needs_repair(cue)]
        if not suspicious:
            return cues

        repaired: dict[int, SubtitleCue] = {}
        repair_batches = self.build_batches(
            suspicious,
            batch_size=max(1, min(8, self.settings.translation_batch_size // 2 or 1)),
            max_chars=max(600, min(1200, self.settings.translation_max_chars // 2 or 600)),
        )
        for batch in repair_batches:
            for cue in self._translate_batch(batch, repair=True):
                repaired[cue.id] = cue

        return [repaired.get(cue.id, cue) for cue in cues]

    def suspicious_translation_ids(self, cues: list[SubtitleCue]) -> list[int]:
        return [cue.id for cue in cues if self.translation_needs_repair(cue)]

    def translation_needs_repair(self, cue: SubtitleCue) -> bool:
        target_text = normalize_chinese_text(cue.target_text)
        if not target_text:
            return True

        source_text = normalize_english_text(cue.source_text)
        scrubbed_target = self._strip_protected_terms(target_text)
        scrubbed_source = self._strip_protected_terms(source_text)

        if target_text == source_text and source_text.lower() not in self._protected_terms:
            return True

        if not CJK_RE.search(scrubbed_target):
            return not self._is_protected_only(scrubbed_target)

        ascii_tokens = self._meaningful_ascii_tokens(scrubbed_target)
        if not ascii_tokens:
            return False

        source_tokens = self._meaningful_ascii_tokens(scrubbed_source)
        copied_tokens = [token for token in ascii_tokens if token in source_tokens and token not in self._protected_terms]
        if copied_tokens and len(copied_tokens) >= max(2, len(ascii_tokens) // 2):
            return True

        ascii_ratio = self._ascii_ratio(scrubbed_target)
        return ascii_ratio >= 0.35

    def _translate_batch(self, batch: list[SubtitleCue], *, repair: bool = False, _retry_counter: list[int] | None = None) -> list[SubtitleCue]:
        MAX_RETRIES = 10

        if _retry_counter is None:
            _retry_counter = [0]

        if _retry_counter[0] >= MAX_RETRIES:
            raise RuntimeError(f"DeepSeek 翻译失败：已达到最大重试次数 ({MAX_RETRIES})")

        last_error: Exception | None = None
        for _ in range(3):
            try:
                rows = self._request_repair_translation_rows(batch) if repair else self._request_translation_rows(batch)
                return self._merge_batch(batch, rows)
            except Exception as exc:
                last_error = exc
                _retry_counter[0] += 1
                if _retry_counter[0] >= MAX_RETRIES:
                    break

        if len(batch) > 1:
            midpoint = len(batch) // 2
            left = self._translate_batch(batch[:midpoint], repair=repair, _retry_counter=_retry_counter)
            right = self._translate_batch(batch[midpoint:], repair=repair, _retry_counter=_retry_counter)
            return left + right

        raise RuntimeError(f"DeepSeek 翻译失败：{last_error}")

    def _translate_and_repair_batch(self, batch: list[SubtitleCue]) -> list[SubtitleCue]:
        translated_batch = self._translate_batch(batch)
        return self.repair_low_quality_translations(translated_batch)

    def _request_translation_rows(self, batch: list[SubtitleCue]) -> list[dict]:
        return self._request_rows(batch, repair=False)

    def _request_repair_translation_rows(self, batch: list[SubtitleCue]) -> list[dict]:
        return self._request_rows(batch, repair=True)

    def _request_rows(self, batch: list[SubtitleCue], *, repair: bool) -> list[dict]:
        client = self._get_client()
        payload = [self._build_payload_row(cue, repair=repair) for cue in batch]
        response = client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=self.style_preset.temperature if not repair else min(self.style_preset.temperature, 0.15),
            messages=[
                {"role": "system", "content": self.build_system_prompt(repair=repair)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        content = response.choices[0].message.content or ""
        return extract_json_payload(content)

    def _build_payload_row(self, cue: SubtitleCue, *, repair: bool) -> dict[str, str | int]:
        row: dict[str, str | int] = {"id": cue.id, "text": cue.source_text}
        if repair:
            row["current_translation"] = cue.target_text
        return row

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is not None:
            return client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "缺少 DeepSeek 调用依赖，请先执行 `pip install -r requirements.txt` 安装 openai。"
            ) from exc
        client = OpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            timeout=120,
            max_retries=2,
        )
        self._local.client = client
        return client

    @staticmethod
    def _merge_batch(batch: list[SubtitleCue], rows: list[dict]) -> list[SubtitleCue]:
        if len(rows) != len(batch):
            raise ValueError("翻译结果数量和输入字幕数量不一致。")

        translated: list[SubtitleCue] = []
        for cue, row in zip(batch, rows):
            if row.get("id") != cue.id:
                raise ValueError("翻译结果 id 顺序与输入不一致。")
            text = normalize_chinese_text(str(row.get("translation", "")).strip())
            if not text:
                raise ValueError("翻译结果存在空白字幕。")
            translated.append(replace(cue, target_text=text))
        return translated

    def _strip_protected_terms(self, text: str) -> str:
        result = text
        for term in sorted(self._protected_terms, key=len, reverse=True):
            if not term:
                continue
            result = re.sub(re.escape(term), " ", result, flags=re.IGNORECASE)
        return normalize_english_text(result)

    @staticmethod
    def _normalize_terms(terms: list[str]) -> set[str]:
        normalized: set[str] = set()
        for term in terms:
            value = normalize_english_text(str(term)).lower()
            if value:
                normalized.add(value)
        return normalized

    @staticmethod
    def _meaningful_ascii_tokens(text: str) -> set[str]:
        return {
            token.lower()
            for token in ASCII_WORD_RE.findall(normalize_english_text(text))
            if len(token) >= 2
        }

    @staticmethod
    def _ascii_ratio(text: str) -> float:
        if not text:
            return 0.0
        ascii_chars = sum(1 for char in text if ord(char) < 128 and not char.isspace())
        total_chars = sum(1 for char in text if not char.isspace())
        if total_chars == 0:
            return 0.0
        return ascii_chars / total_chars

    def _is_protected_only(self, text: str) -> bool:
        stripped = self._strip_protected_terms(text)
        if not stripped:
            return True
        return not any(char.isalpha() or char.isdigit() for char in stripped)

    @staticmethod
    def _format_glossary_entry(entry: TranslationGlossaryEntry) -> str:
        if entry.note:
            return f"{entry.source} -> {entry.target} ({entry.note})"
        return f"{entry.source} -> {entry.target}"
