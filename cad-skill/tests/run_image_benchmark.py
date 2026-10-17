#!/usr/bin/env python3
"""Run a manifest of labeled image-to-walls benchmark cases."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parent
for _path in (_TESTS, _ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import test_image_to_calls as evaluation  # noqa: E402

_RESERVED_ARGS = {
    "--mode", "--out-plan", "--out-calls", "--out-dxf", "--preview",
    "--px-per-mm", "--origin", "--calibrate", "--real",
}
_THRESHOLDS = {
    "wall_recall_min": ("wall_recall", "min"),
    "wall_precision_min": ("wall_precision", "min"),
    "opening_recall_min": ("opening_recall", "min"),
    "opening_precision_min": ("opening_precision", "min"),
    "opening_type_accuracy_min": ("opening_type_accuracy", "min"),
    "max_axis_error_mm": ("axis_error_max_mm", "max"),
    "max_wall_thickness_error_mm": ("wall_thickness_error_max_mm", "max"),
    "max_opening_width_error_mm": ("opening_width_error_max_mm", "max"),
    "max_opening_position_error_mm": ("opening_position_error_max_mm", "max"),
}
_RATE_METRICS = (
    "wall_recall", "wall_precision", "opening_recall", "opening_precision",
    "opening_type_accuracy",
)
_ERROR_METRICS = (
    "axis_error_max_mm", "wall_thickness_error_max_mm",
    "opening_width_error_max_mm", "opening_position_error_max_mm",
)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _resolve_case_path(base: Path, value: str, field: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise ValueError(f"{field} 文件不存在: {path}")
    return str(path)


def _case_argv(case: dict[str, Any], base: Path, output_plan: str,
               preview_path: str | None = None) -> list[str]:
    image = _resolve_case_path(base, case["image"], "image")
    calibration = case["calibration"]
    if not isinstance(calibration, dict):
        raise ValueError(f"样例 {case['id']} 的 calibration 必须是 JSON 对象")
    px_per_mm = float(calibration["px_per_mm"])
    if not math.isfinite(px_per_mm) or px_per_mm <= 0:
        raise ValueError(f"样例 {case['id']} 的 px_per_mm 必须是正数")
    origin = calibration.get("origin", [0, 0])
    if (not isinstance(origin, list) or len(origin) != 2
            or any(not math.isfinite(float(value)) for value in origin)):
        raise ValueError(f"样例 {case['id']} 的 calibration.origin 必须是 [x, y]")
    arguments = case.get("arguments", [])
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise ValueError(f"样例 {case['id']} 的 arguments 必须是字符串数组")
    for argument in arguments:
        option = argument.split("=", 1)[0]
        if option in _RESERVED_ARGS:
            raise ValueError(f"样例 {case['id']} 不允许覆盖保留参数: {argument}")
    argv = [
        image, "--mode", "walls", "--px-per-mm", str(px_per_mm),
        "--origin", f"{origin[0]},{origin[1]}", "--out-plan", output_plan,
        *arguments,
    ]
    if preview_path:
        argv += ["--preview", preview_path]
    return argv


def _score_case(case: dict[str, Any], base: Path, output_plan: str,
                preview_path: str | None = None) -> dict[str, Any]:
    truth_path = _resolve_case_path(base, case["truth_plan"], "truth_plan")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code, payload = evaluation.run_cli(
                _case_argv(case, base, output_plan, preview_path))
    except SystemExit as exc:
        return {"id": case["id"], "category": case["category"], "ok": False,
                "error": f"image_to_calls 参数解析失败 (exit={exc.code})"}
    if code != 0 or not isinstance(payload, dict) or not payload.get("ok", True):
        return {"id": case["id"], "category": case["category"], "ok": False,
                "error": (payload or {}).get("error", f"image_to_calls exit={code}")}

    with open(truth_path, encoding="utf-8") as handle:
        truth = evaluation.normalize(json.load(handle))
    with open(output_plan, encoding="utf-8") as handle:
        found = evaluation.normalize(json.load(handle))
    metrics = evaluation.compare(truth, found)
    scores = {
        "walls_matched": metrics["walls_matched"],
        "walls_truth": len(truth),
        "walls_found": len(found),
        "wall_recall": _rate(metrics["walls_matched"], len(truth)),
        "wall_precision": _rate(metrics["walls_matched"], len(found)),
        "openings_hit": metrics["openings_hit"],
        "openings_truth": metrics["openings_total"],
        "openings_found": metrics["openings_found"],
        "opening_recall": _rate(metrics["openings_hit"], metrics["openings_total"]),
        "opening_precision": _rate(metrics["openings_hit"], metrics["openings_found"]),
        "opening_types_correct": metrics["types_ok"],
        "opening_type_accuracy": _rate(metrics["types_ok"], metrics["openings_hit"]),
        "axis_error_max_mm": metrics["axis"],
        "wall_thickness_error_max_mm": metrics["thickness"],
        "opening_width_error_max_mm": metrics["width_err"],
        "opening_position_error_max_mm": metrics["pos_err"],
    }
    failures = []
    for threshold, (metric, comparison) in _THRESHOLDS.items():
        if threshold not in case.get("thresholds", {}):
            continue
        limit = float(case["thresholds"][threshold])
        value = scores[metric]
        if (comparison == "min" and value < limit) or (comparison == "max" and value > limit):
            failures.append({"metric": metric, "actual": value, "threshold": limit})
    result = {
        "id": case["id"],
        "category": case["category"],
        "ok": not failures,
        "scores": scores,
        "threshold_failures": failures,
        "recognizer": {key: payload.get(key) for key in (
            "walls", "openings", "door_swings_confirmed", "door_swings_unconfirmed",
            "warnings") if key in payload},
    }
    if preview_path:
        result["artifacts"] = {"plan": output_plan, "preview": preview_path}
    return result


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        groups.setdefault(result["category"], []).append(result)

    def summarize_group(group: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [result["scores"] for result in group if "scores" in result]
        summary: dict[str, Any] = {
            "cases": len(group),
            "passed": sum(result["ok"] for result in group),
            "evaluated": len(scored),
        }
        if scored:
            summary["rate_mean"] = {
                key: round(statistics.fmean(scores[key] for scores in scored), 6)
                for key in _RATE_METRICS
            }
            summary["rate_micro"] = {
                "wall_recall": _rate(
                    sum(scores["walls_matched"] for scores in scored),
                    sum(scores["walls_truth"] for scores in scored)),
                "wall_precision": _rate(
                    sum(scores["walls_matched"] for scores in scored),
                    sum(scores["walls_found"] for scores in scored)),
                "opening_recall": _rate(
                    sum(scores["openings_hit"] for scores in scored),
                    sum(scores["openings_truth"] for scores in scored)),
                "opening_precision": _rate(
                    sum(scores["openings_hit"] for scores in scored),
                    sum(scores["openings_found"] for scores in scored)),
                "opening_type_accuracy": _rate(
                    sum(scores["opening_types_correct"] for scores in scored),
                    sum(scores["openings_hit"] for scores in scored)),
            }
            summary["geometry_error_mean_mm"] = {
                key: round(statistics.fmean(scores[key] for scores in scored), 3)
                for key in _ERROR_METRICS
            }
            summary["geometry_error_worst_case_mm"] = {
                key: max(scores[key] for scores in scored) for key in _ERROR_METRICS
            }
        return summary

    return {
        "overall": summarize_group(results),
        "categories": {
            category: summarize_group(group)
            for category, group in sorted(groups.items())
        },
    }


def run_manifest(manifest_path: str, artifacts_dir: str | None = None) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("manifest 顶层必须是 JSON 对象")
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest.schema_version 必须为 1")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest.cases 必须是非空数组；空集不能作为通过的基准")
    if not all(isinstance(case, dict) for case in cases):
        raise ValueError("manifest.cases 中的每一项都必须是 JSON 对象")
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
        raise ValueError("每个样例都必须有非空 id")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("样例 id 必须唯一")
    for case in cases:
        if not isinstance(case.get("category"), str) or not case["category"].strip():
            raise ValueError(f"样例 {case['id']} 的 category 必须是非空字符串")
        thresholds = case.get("thresholds", {})
        if not isinstance(thresholds, dict):
            raise ValueError(f"样例 {case['id']} 的 thresholds 必须是 JSON 对象")
        unknown = set(thresholds) - _THRESHOLDS.keys()
        if unknown:
            raise ValueError(f"样例 {case['id']} 有未知阈值: {', '.join(sorted(unknown))}")
        for key, value in thresholds.items():
            limit = float(value)
            _, comparison = _THRESHOLDS[key]
            if not math.isfinite(limit):
                raise ValueError(f"样例 {case['id']} 的阈值 {key} 必须是有限数值")
            if comparison == "min" and not 0 <= limit <= 1:
                raise ValueError(f"样例 {case['id']} 的比率阈值 {key} 必须在 0..1")
            if comparison == "max" and limit < 0:
                raise ValueError(f"样例 {case['id']} 的误差阈值 {key} 不能小于 0")

    def run_cases(output_dir: Path, retain_artifacts: bool) -> list[dict[str, Any]]:
        results = []
        for index, case in enumerate(cases):
            safe_id = "".join(
                char if char.isalnum() or char in "._-" else "_"
                for char in case["id"]
            )
            prefix = f"{index:03d}-{safe_id}"
            output_plan = str(output_dir / f"{prefix}.plan.json")
            preview_path = str(output_dir / f"{prefix}.preview.png") if retain_artifacts else None
            try:
                results.append(_score_case(case, path.parent, output_plan, preview_path))
            except (KeyError, TypeError, ValueError, OSError) as exc:
                results.append({"id": case["id"], "category": case["category"],
                                "ok": False, "error": str(exc)})
        return results

    if artifacts_dir:
        output_dir = Path(artifacts_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        results = run_cases(output_dir, retain_artifacts=True)
    else:
        with tempfile.TemporaryDirectory(prefix="cad-image-benchmark-") as temp_dir:
            results = run_cases(Path(temp_dir), retain_artifacts=False)
    return {
        "ok": all(result["ok"] for result in results),
        "case_count": len(results),
        "passed": sum(result["ok"] for result in results),
        "failed": sum(not result["ok"] for result in results),
        "summary": summarize_results(results),
        "cases": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行标注图纸的墙体/洞口识别基准")
    parser.add_argument("manifest", help="基准 manifest JSON 路径")
    parser.add_argument("--artifacts-dir", help="保留每个案例的识别 plan 与 PNG 预览")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_manifest(args.manifest, args.artifacts_dir)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    print()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())