import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ytsubviewer.models import SubtitleCue
from ytsubviewer.quality import QualityIssue, generate_quality_report


class QualityReportTests(unittest.TestCase):
    def test_generate_quality_report_flags_common_problems(self) -> None:
        cues = [
            SubtitleCue(id=1, start=0.0, end=2.0, source_text="Hello", target_text="Hello there this is still English"),
            SubtitleCue(id=2, start=1.8, end=3.0, source_text="CUDA helps", target_text="CUDA helps"),
            SubtitleCue(id=3, start=3.0, end=4.0, source_text="Empty", target_text=""),
            SubtitleCue(
                id=4,
                start=4.0,
                end=6.0,
                source_text="Long",
                target_text="这是一条非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的字幕，需要被标记为过长。",
            ),
        ]

        report = generate_quality_report(cues, expected_duration=10.0, max_line_length=20)

        self.assertGreaterEqual(report.error_count, 1)
        self.assertGreaterEqual(report.warning_count, 2)
        self.assertGreaterEqual(report.leftover_english_count, 1)
        self.assertGreaterEqual(report.empty_translation_count, 1)
        self.assertGreaterEqual(report.long_line_count, 1)
        self.assertGreaterEqual(report.overlap_count, 1)
        self.assertIn("总字幕 4 条", report.summary())

    def test_generate_quality_report_flags_terminology_mixed_usage(self) -> None:
        cues = [
            SubtitleCue(id=1, start=0.0, end=1.0, source_text="CUDA is important.", target_text="CUDA 很重要。"),
            SubtitleCue(id=2, start=1.0, end=2.0, source_text="CUDA is fast.", target_text="英伟达计算平台很快。"),
            SubtitleCue(id=3, start=2.0, end=3.0, source_text="CUDA appears again.", target_text="CUDA 再次出现。"),
        ]

        report = generate_quality_report(cues)
        terminology_issues = [issue for issue in report.issues if issue.code == "terminology_inconsistent"]

        self.assertTrue(terminology_issues)
        self.assertTrue(any("CUDA" in issue.message for issue in terminology_issues))

    def test_generate_quality_report_uses_glossary(self) -> None:
        cues = [
            SubtitleCue(id=1, start=0.0, end=1.0, source_text="Blackwell changes everything.", target_text="Blackwell 改变了一切。"),
            SubtitleCue(id=2, start=1.0, end=2.0, source_text="Blackwell changes everything.", target_text="英伟达新架构改变了一切。"),
        ]

        report = generate_quality_report(cues, glossary={"Blackwell": ["Blackwell", "Blackwell 架构"]})
        terminology_issues = [issue for issue in report.issues if issue.code == "terminology_inconsistent"]

        self.assertTrue(terminology_issues)
        self.assertEqual(terminology_issues[0].severity, "warning")

    def test_report_to_dict_includes_issue_payload(self) -> None:
        issue = QualityIssue(code="empty_translation", severity="error", message="字幕为空")
        cues = [SubtitleCue(id=1, start=0.0, end=1.0, source_text="Hi", target_text="")]
        report = generate_quality_report(cues)
        report.add_issue(issue)

        payload = report.to_dict()

        self.assertEqual(payload["issue_count"], len(report.issues))
        self.assertEqual(payload["issues"][-1]["code"], "empty_translation")


if __name__ == "__main__":
    unittest.main()
