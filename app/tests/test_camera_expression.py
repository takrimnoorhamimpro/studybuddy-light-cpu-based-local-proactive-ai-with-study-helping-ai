"""Hardware-free tests for the local FER+ ONNX expression classifier."""
import os
import tempfile
import unittest
from features.camera.expression import CameraExpressionAnalyzer, CLASS_NAMES


class CameraExpressionAnalyzerTests(unittest.TestCase):
    def test_missing_asset_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            analyzer = CameraExpressionAnalyzer(os.path.join(folder, "missing.onnx"))
            self.assertFalse(analyzer.available)
            self.assertIn("missing", analyzer.last_error.lower())
            self.assertEqual(analyzer.process_bgr_frame(object()), "")
            self.assertEqual(analyzer.get_recent_summary(), "")

    def test_stale_summary_is_not_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            analyzer = CameraExpressionAnalyzer(os.path.join(folder, "missing.onnx"))
            analyzer.last_summary = "old prediction"
            analyzer.last_update = 1
            self.assertEqual(analyzer.get_recent_summary(max_age_seconds=1), "")

    def test_class_names_are_dataset_labels_not_certainty_claims(self):
        self.assertEqual(CLASS_NAMES[:4], ("neutral", "happy", "surprised", "sad"))
        self.assertIn("afraid", CLASS_NAMES)

    def test_clear_happy_prediction_is_reported_as_visible_expression(self):
        probabilities = [0.03, 0.78, 0.04, 0.04, 0.03, 0.02, 0.02, 0.04]
        result = CameraExpressionAnalyzer._format_probabilities(probabilities)
        self.assertIn("visible expression most resembles happy", result)
        self.assertIn("78%", result)
        self.assertIn("not a claim about actual feelings", result)

    def test_close_scores_are_reported_as_uncertain(self):
        probabilities = [0.29, 0.27, 0.08, 0.12, 0.10, 0.04, 0.06, 0.04]
        result = CameraExpressionAnalyzer._format_probabilities(probabilities)
        self.assertIn("uncertain/mixed", result)

    def test_low_confidence_is_not_overstated(self):
        probabilities = [0.20, 0.19, 0.15, 0.14, 0.12, 0.08, 0.07, 0.05]
        result = CameraExpressionAnalyzer._format_probabilities(probabilities)
        self.assertIn("uncertain/mixed", result)

    def test_softmax_is_normalized(self):
        result = CameraExpressionAnalyzer._softmax([1.0, 2.0, 3.0])
        self.assertAlmostEqual(sum(result), 1.0, places=6)
        self.assertGreater(result[2], result[1])


if __name__ == "__main__":
    unittest.main()
