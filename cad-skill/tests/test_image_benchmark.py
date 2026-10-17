#!/usr/bin/env python3
"""Manifest runner regression test using a generated, labeled floor plan."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_TESTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

import run_image_benchmark  # noqa: E402
import test_image_to_calls as fixtures  # noqa: E402


class ImageBenchmarkTests(unittest.TestCase):
    def test_summary_reports_macro_and_object_weighted_micro_rates(self) -> None:
        def make_result(case_id: str, wall_hits: int, wall_truth: int) -> dict:
            return {
                "id": case_id, "category": "clean", "ok": True,
                "scores": {
                    "walls_matched": wall_hits,
                    "walls_truth": wall_truth,
                    "walls_found": wall_hits,
                    "wall_recall": wall_hits / wall_truth,
                    "wall_precision": 1.0,
                    "openings_hit": 0,
                    "openings_truth": 0,
                    "openings_found": 0,
                    "opening_recall": 0.0,
                    "opening_precision": 0.0,
                    "opening_types_correct": 0,
                    "opening_type_accuracy": 0.0,
                    "axis_error_max_mm": 0.0,
                    "wall_thickness_error_max_mm": 0.0,
                    "opening_width_error_max_mm": 0.0,
                    "opening_position_error_max_mm": 0.0,
                },
            }

        summary = run_image_benchmark.summarize_results([
            make_result("tiny", 1, 1),
            make_result("large", 50, 99),
        ])
        clean = summary["categories"]["clean"]

        self.assertAlmostEqual(clean["rate_mean"]["wall_recall"],
                       (1 + 50 / 99) / 2, places=6)
        self.assertAlmostEqual(clean["rate_micro"]["wall_recall"], 51 / 100)

    def test_empty_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-benchmark-test-") as temp_dir:
            manifest_path = os.path.join(temp_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "cases": []}, handle)

            with self.assertRaisesRegex(ValueError, "非空数组"):
                run_image_benchmark.run_manifest(manifest_path)

    def test_invalid_category_is_rejected_before_scoring(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-benchmark-test-") as temp_dir:
            manifest_path = os.path.join(temp_dir, "manifest.json")
            manifest = {"schema_version": 1, "cases": [{
                "id": "bad-category", "category": ["scan"],
            }]}
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)

            with self.assertRaisesRegex(ValueError, "category 必须是非空字符串"):
                run_image_benchmark.run_manifest(manifest_path)

    def test_bad_recognizer_arguments_fail_only_the_case(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-benchmark-test-") as temp_dir:
            calls, _, spec = fixtures.plan_calls(["A-WALL"])
            fixtures.render(calls, os.path.join(temp_dir, "walls.png"))
            truth_path = os.path.join(temp_dir, "truth.json")
            with open(truth_path, "w", encoding="utf-8") as handle:
                json.dump(spec, handle)
            case = {
                "category": "malformed-cli",
                "image": "walls.png",
                "truth_plan": "truth.json",
                "calibration": {"px_per_mm": fixtures.PPM,
                                "origin": [40, 1640]},
            }
            manifest = {"schema_version": 1, "cases": [
                dict(case, id="bad-args"), dict(case, id="valid-after-error"),
            ]}
            manifest_path = os.path.join(temp_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)

            real_run_cli = run_image_benchmark.evaluation.run_cli
            calls_seen = 0

            def fail_first(argv):
                nonlocal calls_seen
                calls_seen += 1
                if calls_seen == 1:
                    raise SystemExit(2)
                return real_run_cli(argv)

            with patch.object(run_image_benchmark.evaluation, "run_cli",
                              side_effect=fail_first):
                result = run_image_benchmark.run_manifest(manifest_path)

        self.assertFalse(result["cases"][0]["ok"])
        self.assertIn("参数解析失败", result["cases"][0]["error"])
        self.assertTrue(result["cases"][1]["ok"], result["cases"][1])

    def test_missing_image_fails_only_that_case(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-benchmark-test-") as temp_dir:
            calls, _, spec = fixtures.plan_calls(["A-WALL"])
            fixtures.render(calls, os.path.join(temp_dir, "walls.png"))
            truth_path = os.path.join(temp_dir, "truth.json")
            with open(truth_path, "w", encoding="utf-8") as handle:
                json.dump(spec, handle)
            base_case = {
                "category": "synthetic-clean",
                "truth_plan": "truth.json",
                "calibration": {"px_per_mm": fixtures.PPM,
                                "origin": [40, 1640]},
            }
            manifest = {"schema_version": 1, "cases": [
                dict(base_case, id="missing-image", image="missing.png"),
                dict(base_case, id="valid-after-missing", image="walls.png"),
            ]}
            manifest_path = os.path.join(temp_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)

            result = run_image_benchmark.run_manifest(manifest_path)

        self.assertFalse(result["cases"][0]["ok"])
        self.assertIn("image 文件不存在", result["cases"][0]["error"])
        self.assertTrue(result["cases"][1]["ok"], result["cases"][1])

    def test_manifest_runs_labeled_case_and_enforces_thresholds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cad-benchmark-test-") as temp_dir:
            calls, _, spec = fixtures.plan_calls(["A-WALL"])
            fixtures.render(calls, os.path.join(temp_dir, "walls.png"))
            truth_path = os.path.join(temp_dir, "truth.json")
            manifest_path = os.path.join(temp_dir, "manifest.json")
            with open(truth_path, "w", encoding="utf-8") as handle:
                json.dump(spec, handle)
            manifest = {
                "schema_version": 1,
                "cases": [{
                    "id": "synthetic-clean-walls",
                    "category": "synthetic-clean",
                    "image": "walls.png",
                    "truth_plan": "truth.json",
                    "calibration": {"px_per_mm": fixtures.PPM,
                                    "origin": [40, 1640]},
                    "thresholds": {"wall_recall_min": 1.0,
                                   "opening_recall_min": 1.0},
                }],
            }
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)

            artifacts_dir = os.path.join(temp_dir, "artifacts")
            result = run_image_benchmark.run_manifest(manifest_path, artifacts_dir)

            artifact_paths = result["cases"][0]["artifacts"]
            self.assertTrue(os.path.isfile(artifact_paths["plan"]))
            self.assertTrue(os.path.isfile(artifact_paths["preview"]))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["cases"][0]["scores"]["wall_recall"], 1.0)
        self.assertEqual(result["cases"][0]["scores"]["opening_recall"], 1.0)
        category = result["summary"]["categories"]["synthetic-clean"]
        self.assertEqual(category["cases"], 1)
        self.assertEqual(category["rate_mean"]["wall_recall"], 1.0)
        self.assertEqual(category["geometry_error_worst_case_mm"]["axis_error_max_mm"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)