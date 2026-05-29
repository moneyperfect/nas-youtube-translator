import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.config import Settings
from ytsubviewer.models import SubtitleCue, TranslationGlossaryEntry
from ytsubviewer.services.translate import DeepSeekTranslator


class TranslationControlTests(unittest.TestCase):
    def test_settings_parse_translation_controls(self) -> None:
        settings = Settings(
            translation_style_preset="creator",
            translation_glossary_json='[{"source":"CUDA","target":"CUDA","note":"keep exact"},{"source":"Blackwell","target":"Blackwell"}]',
            translation_protected_terms_json="CUDA,Blackwell,Omniverse",
        )

        controls = settings.translation_controls()

        self.assertEqual(controls.style_preset, "creator")
        self.assertEqual(
            controls.glossary,
            (
                TranslationGlossaryEntry(source="CUDA", target="CUDA", note="keep exact"),
                TranslationGlossaryEntry(source="Blackwell", target="Blackwell", note=""),
            ),
        )
        self.assertEqual(controls.protected_terms, ("CUDA", "Blackwell", "Omniverse"))

    def test_style_preset_registry_exposes_productized_options(self) -> None:
        presets = DeepSeekTranslator.available_style_presets()
        self.assertIn("default", presets)
        self.assertIn("creator", presets)
        self.assertIn("conference", presets)
        self.assertIn("technical", presets)
        self.assertEqual(DeepSeekTranslator.get_style_preset("creator").name, "creator")

    def test_system_prompt_includes_glossary_and_protected_terms(self) -> None:
        settings = Settings(
            translation_style_preset="conference",
            translation_glossary_json='[{"source":"CUDA","target":"CUDA","note":"keep exact"}]',
            translation_protected_terms_json='["CUDA","Omniverse"]',
        )
        translator = DeepSeekTranslator(settings)

        prompt = translator.build_system_prompt()

        self.assertIn("Style preset: Conference", prompt)
        self.assertIn("CUDA -> CUDA (keep exact)", prompt)
        self.assertIn("Protected terms:", prompt)
        self.assertIn("omniverse", prompt.lower())

    def test_protected_terms_are_not_flagged_for_repair(self) -> None:
        settings = Settings(
            translation_protected_terms_json='["CUDA","Omniverse"]',
        )
        translator = DeepSeekTranslator(settings)

        protected_cue = SubtitleCue(
            id=1,
            start=0.0,
            end=1.0,
            source_text="CUDA is the core platform.",
            target_text="CUDA",
        )
        regular_cue = SubtitleCue(
            id=2,
            start=1.0,
            end=2.0,
            source_text="This architecture expands the platform.",
            target_text="This architecture expands the platform.",
        )

        self.assertFalse(translator.translation_needs_repair(protected_cue))
        self.assertTrue(translator.translation_needs_repair(regular_cue))

    def test_prompt_can_be_built_without_network_or_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                project_root=Path(temp_dir),
                workspace_dir=Path(temp_dir) / "workspace",
                jobs_dir=Path(temp_dir) / "workspace" / "jobs",
                translation_style_preset="technical",
            )
            translator = DeepSeekTranslator(settings)
            prompt = translator.build_system_prompt(repair=True)

        self.assertIn("Style preset: Technical", prompt)
        self.assertIn("Rewrite low-quality subtitle lines", prompt)


if __name__ == "__main__":
    unittest.main()
