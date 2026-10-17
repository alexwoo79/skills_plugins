#!/usr/bin/env python3
"""image_to_calls.py 的真值测试。

做法：拿 `assets/example-two-bedroom/plan.json` 当**真值**，把它渲染成图（只画墙 /
画全套 / 再加噪声与 0.5° 旋转），跑矢量化，再逐面墙、逐个洞口比对。
这样"识别效果如何"不是嘴上说的，而是每次跑测试都能量出来的数字。

依赖：opencv-python-headless、Pillow、ezdxf（缺任一个则跳过对应用例）。
运行：python3 tests/test_image_to_calls.py
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
SCRIPTS = os.path.join(_SKILL, "scripts")
ASSET_PLAN = os.path.join(_SKILL, "assets", "example-two-bedroom", "plan.json")
FAKE_COM = os.path.join(_HERE, "fake_com")
for _path in (FAKE_COM, os.path.join(_SKILL, "vendor"), SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import wall_plan  # noqa: E402
import image_to_calls as itc  # noqa: E402

try:
    import cv2  # noqa: F401
except ImportError:
    cv2 = None
try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
try:
    import ezdxf
except ImportError:
    ezdxf = None
try:
    import numpy as np
except ImportError:
    np = None

LAYERS = {"wall": "A-WALL", "door": "A-DOOR", "window": "A-WINDOW"}
PPM = 0.2          # 像素/毫米：12000mm → 2400px
MARGIN = 40        # 图上留的边距，用来验证"必须给原点"
ORIGIN = "40,1640"  # 对应 CAD (0,0) 的像素（左下角）


def plan_calls(layers=None):
    with open(ASSET_PLAN, encoding="utf-8") as handle:
        spec = json.load(handle)
    built = wall_plan.build_plan(spec, dict(LAYERS))
    calls = built["calls"]
    if layers:
        calls = [c for c in calls if c["args"].get("layer") in layers]
    return calls, built, spec


def render(calls, path: str, noise: float = 0.0, rotate: float = 0.0) -> str:
    """把调用序列渲染成图（坐标 mm → 像素），可加噪点与旋转"""
    size = (int(12000 * PPM) + 2 * MARGIN, int(8000 * PPM) + 2 * MARGIN)
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    to_px = lambda p: (MARGIN + p[0] * PPM, MARGIN + (8000 - p[1]) * PPM)
    for call in calls:
        tool, args = call["tool"], call["args"]
        if tool == "draw_polyline":
            points = [to_px(p) for p in args["points"]]
            draw.line(points + [points[0]], fill=0, width=2)
        elif tool == "draw_line":
            draw.line([to_px(args["start_point"]), to_px(args["end_point"])], fill=0, width=2)
        elif tool == "draw_arc":
            cx, cy, r = args["center"][0], args["center"][1], args["radius"]
            box = [MARGIN + (cx - r) * PPM, MARGIN + (8000 - (cy + r)) * PPM,
                   MARGIN + (cx + r) * PPM, MARGIN + (8000 - (cy - r)) * PPM]
            # 注意：end_angle=0 是合法角度，不能用 `or 360` 兜底（那会画成 270°）
            end_angle = args["end_angle"]
            if abs(float(end_angle)) < 1e-9:
                end_angle = 0.0
            draw.arc(box, start=-float(end_angle), end=-args["start_angle"], fill=0, width=2)
    if noise:
        array = np.array(image).astype(np.int16)
        rng = np.random.default_rng(7)
        flipped = rng.random(array.shape) < noise
        array = np.where((array < 128) ^ flipped, 0, 255).astype("uint8")
        image = Image.fromarray(array)
    if rotate:
        image = image.rotate(rotate, resample=Image.BILINEAR, fillcolor=255)
    image.save(path)
    return path


def run_cli(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = itc.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def normalize(plan) -> list:
    """统一成 {o, offset, s, e, t, openings[(kind,width,abs_pos)]}，坐标取绝对值"""
    walls = plan["walls"] if isinstance(plan, dict) else plan
    result = []
    for wall in walls:
        (x1, y1), (x2, y2) = wall["from"], wall["to"]
        orient = "H" if abs(y2 - y1) < 1e-9 else "V"
        first, second = (x1, x2) if orient == "H" else (y1, y2)
        direction = 1 if second >= first else -1
        result.append({
            "id": wall.get("id"), "o": orient,
            "offset": y1 if orient == "H" else x1,
            "s": min(first, second), "e": max(first, second),
            "t": wall["thickness"],
            "openings": [(op["kind"], op["width"], first + direction * op["center"])
                         for op in wall["openings"]],
        })
    return result


def door_geometry_mm(wall, opening):
    """门的物理几何：合页点 + 开启方向（与墙的书写方向无关，便于比对）。"""
    (x1, y1), (x2, y2) = wall["from"], wall["to"]
    length = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    nx, ny = -uy, ux
    width = opening["width"]
    center = opening.get("center", 0)
    hinge_at = center - width / 2 if opening.get("hinge", "start") == "start" else center + width / 2
    hinge = (x1 + ux * hinge_at, y1 + uy * hinge_at)
    sign = -1.0 if str(opening.get("swing", "positive")).startswith("neg") else 1.0
    return hinge, (nx * sign, ny * sign)


def compare(truth: list, found: list, axis_tol: float = 60) -> dict:
    """真值 vs 识别结果：墙召回、轴/厚度误差、洞口召回与误差、类型命中"""
    metrics = {"walls_matched": 0, "walls_extra": 0, "axis": [], "thickness": [],
               "openings_hit": 0, "openings_total": 0, "openings_found": 0,
               "types_ok": 0,
               "width_err": [], "pos_err": []}
    metrics["openings_total"] = sum(len(wall["openings"]) for wall in truth)
    metrics["openings_found"] = sum(len(wall["openings"]) for wall in found)

    def match_candidates(candidates: list[list[tuple[float, int]]]) -> dict[int, int]:
        matched_found: dict[int, int] = {}

        def assign(gt_index: int, seen: set[int]) -> bool:
            for _, found_index in candidates[gt_index]:
                if found_index in seen:
                    continue
                seen.add(found_index)
                previous = matched_found.get(found_index)
                if previous is None or assign(previous, seen):
                    matched_found[found_index] = gt_index
                    return True
            return False

        for gt_index, matches in enumerate(candidates):
            matches.sort(key=lambda match: match[0], reverse=True)
            assign(gt_index, set())
        return matched_found

    candidates = []
    for gt_wall in truth:
        matches = []
        for found_index, wall in enumerate(found):
            if wall["o"] != gt_wall["o"] or abs(wall["offset"] - gt_wall["offset"]) > axis_tol:
                continue
            overlap = min(wall["e"], gt_wall["e"]) - max(wall["s"], gt_wall["s"])
            if overlap <= 0:
                continue
            truth_coverage = overlap / (gt_wall["e"] - gt_wall["s"])
            found_coverage = overlap / (wall["e"] - wall["s"])
            if truth_coverage < 0.5 or found_coverage < 0.5:
                continue
            score = min(truth_coverage, found_coverage)
            matches.append((score, found_index))
        candidates.append(matches)

    matched_walls = match_candidates(candidates)

    for gt_index, gt_wall in enumerate(truth):
        found_index = next((index for index, matched_gt in matched_walls.items()
                            if matched_gt == gt_index), -1)
        if found_index < 0:
            continue
        wall = found[found_index]
        metrics["walls_matched"] += 1
        metrics["axis"].append(abs(wall["offset"] - gt_wall["offset"]))
        metrics["thickness"].append(abs(wall["t"] - gt_wall["t"]))
        truth_openings = gt_wall["openings"]
        found_openings = wall["openings"]
        opening_candidates = [
            [(-abs(found_opening[2] - truth_opening[2]), found_index)
             for found_index, found_opening in enumerate(found_openings)
             if abs(found_opening[2] - truth_opening[2]) < 250]
            for truth_opening in truth_openings
        ]
        matched_openings = match_candidates(opening_candidates)
        for found_index, gt_index in matched_openings.items():
            kind, width, position = truth_openings[gt_index]
            found_kind, found_width, found_position = found_openings[found_index]
            metrics["openings_hit"] += 1
            metrics["types_ok"] += (found_kind == kind)
            metrics["width_err"].append(abs(found_width - width))
            metrics["pos_err"].append(abs(found_position - position))
    metrics["walls_extra"] = max(0, len(found) - metrics["walls_matched"])
    for key in ("axis", "thickness", "width_err", "pos_err"):
        metrics[key] = max(metrics[key]) if metrics[key] else 0.0
    return metrics


class CompareMatchingTests(unittest.TestCase):
    def test_predicted_wall_matches_at_most_one_truth_wall(self) -> None:
        wall = {"o": "H", "offset": 0, "s": 0, "e": 100, "t": 200,
                "openings": []}

        metrics = compare([dict(wall), dict(wall)], [dict(wall)])

        self.assertEqual(metrics["walls_matched"], 1)
        self.assertEqual(metrics["walls_extra"], 0)

    def test_tiny_fragment_does_not_count_as_a_wall_match(self) -> None:
        truth = {"o": "H", "offset": 0, "s": 0, "e": 1000, "t": 200,
                 "openings": []}
        fragment = dict(truth, e=1)

        metrics = compare([truth], [fragment])

        self.assertEqual(metrics["walls_matched"], 0)
        self.assertEqual(metrics["walls_extra"], 1)

    def test_predicted_opening_matches_at_most_one_truth_opening(self) -> None:
        wall = {"o": "H", "offset": 0, "s": 0, "e": 100, "t": 200,
                "openings": [("door", 900, 50), ("door", 900, 50)]}
        found = dict(wall, openings=[("door", 900, 50)])

        metrics = compare([wall], [found])

        self.assertEqual(metrics["openings_total"], 2)
        self.assertEqual(metrics["openings_hit"], 1)

    def test_openings_on_unmatched_wall_count_as_missed(self) -> None:
        wall = {"o": "H", "offset": 0, "s": 0, "e": 100, "t": 200,
                "openings": [("door", 900, 50)]}

        metrics = compare([wall], [])

        self.assertEqual(metrics["openings_total"], 1)
        self.assertEqual(metrics["openings_hit"], 0)


@unittest.skipIf(cv2 is None or Image is None or np is None,
                 "需要 opencv-python-headless 与 Pillow")
class VectorizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cad-vectorize-")
        calls, built, spec = plan_calls()
        wall_calls = [c for c in calls if c["args"].get("layer") == "A-WALL"]
        cls.truth = normalize(spec)
        cls.walls_only = render(wall_calls, os.path.join(cls.tmp, "walls.png"))
        cls.full = render(calls, os.path.join(cls.tmp, "full.png"))
        cls.scan = render(calls, os.path.join(cls.tmp, "scan.png"), noise=0.03, rotate=0.5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def vectorize(self, image, **kwargs):
        plan_path = os.path.join(self.tmp, os.path.basename(image) + ".plan.json")
        argv = [image, "--mode", "walls", "--px-per-mm", str(PPM), "--origin", ORIGIN,
                "--out-plan", plan_path]
        for key, value in kwargs.items():
            argv += ["--" + key.replace("_", "-")] + ([value] if value is not True else [])
        code, payload = run_cli(argv)
        self.assertEqual(code, 0, payload)
        with open(plan_path, encoding="utf-8") as handle:
            return payload, normalize(json.load(handle))

    def test_clean_walls_recovered_almost_exactly(self) -> None:
        """干净线稿：墙全中、洞口全中、尺寸误差在一个线宽量级内。"""
        payload, found = self.vectorize(self.walls_only)
        metrics = compare(self.truth, found)
        self.assertEqual(metrics["walls_matched"], 8, metrics)
        self.assertEqual(metrics["walls_extra"], 0, metrics)
        self.assertLessEqual(metrics["axis"], 15)          # 实测 0
        self.assertLessEqual(metrics["thickness"], 20)     # 实测 10（= 一个线宽）
        self.assertEqual(metrics["openings_hit"], 10, metrics)
        self.assertLessEqual(metrics["width_err"], 40)     # 实测 10
        self.assertLessEqual(metrics["pos_err"], 80)       # 实测 5
        self.assertEqual(metrics["types_ok"], 10, metrics)

    def test_full_drawing_keeps_windows(self) -> None:
        """完整图（含门窗表达）：窗的三线会填平墙身缺口，仍要能认出窗。"""
        payload, found = self.vectorize(self.full)
        metrics = compare(self.truth, found)
        self.assertGreaterEqual(metrics["walls_matched"], 8, metrics)
        self.assertLessEqual(metrics["walls_extra"], 1, metrics)
        self.assertGreaterEqual(metrics["openings_hit"], 9, metrics)
        self.assertGreaterEqual(metrics["types_ok"], 8, metrics)
        self.assertLessEqual(metrics["axis"], 20)

    def test_scanned_like_image_degrades_gracefully(self) -> None:
        """噪声 + 0.5° 旋转：召回下降但仍在可人工修补的范围（这是描图的常态）。"""
        payload, found = self.vectorize(self.scan, deskew=True, adaptive=True, despeckle=True)
        metrics = compare(self.truth, found)
        self.assertGreaterEqual(metrics["walls_matched"], 6, metrics)
        self.assertGreaterEqual(metrics["openings_hit"], 7, metrics)
        self.assertLessEqual(metrics["axis"], 40)
        self.assertLessEqual(metrics["thickness"], 30)
        # 噪声/旋转下仍能从弧反推开向（实测 4 扇全部确认），但允许掉 1 扇
        self.assertGreaterEqual(payload["door_swings_confirmed"], 3, payload)

    def test_calibration_is_required_for_walls_mode(self) -> None:
        code, payload = run_cli([self.full, "--mode", "walls"])
        self.assertEqual(code, 2)
        self.assertIn("标定", payload["error"])

    def test_lines_mode_works_without_calibration(self) -> None:
        code, payload = run_cli([self.full, "--mode", "lines"])
        self.assertEqual(code, 0, payload)
        self.assertIsNone(payload["px_per_mm"])      # 未标定：输出像素，不冒充毫米
        self.assertIn("working_size", payload)

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_default_mode_is_faithful_trace(self) -> None:
        """不给模式时默认 hybrid 忠实描线：无需标定，DXF 只含描线结果。"""
        dxf_path = os.path.join(self.tmp, "default.dxf")
        code, payload = run_cli([self.full, "--out-dxf", dxf_path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["mode"], "lines")
        self.assertEqual(payload["tracer"], "hybrid")
        self.assertEqual(payload["dxf_mode"], "trace")
        entities = list(ezdxf.readfile(dxf_path).modelspace())
        self.assertEqual(len(entities), payload["entities"])
        self.assertTrue({entity.dxf.layer for entity in entities}
                        <= {"A-SKETCH", "A-SOLID", "A-HATCH", "A-TEXT"})

    def test_calls_output_feeds_cad_direct(self) -> None:
        """图 → plan.json → calls.json → CAD：整条链路跑通（假 COM）"""
        calls_path = os.path.join(self.tmp, "calls.json")
        code, payload = run_cli([self.full, "--mode", "walls", "--px-per-mm", str(PPM),
                                 "--origin", ORIGIN, "--out-calls", calls_path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["warnings"], [], payload)   # 识别结果要能过 wall_plan 校验

        from fake_doc import reset_active_app
        import cad_direct
        reset_active_app()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cad_direct.main(["draw", "--calls", calls_path])
        drawn = json.loads(buffer.getvalue())
        self.assertEqual(code, 0, drawn)
        self.assertTrue(drawn["ok"], drawn)
        self.assertEqual(drawn["drawn"], payload["calls_count"])
        self.assertGreaterEqual(drawn["drawn"], 60)

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_dxf_output(self) -> None:
        """lines 模式默认走 hybrid：细线骨架 + 实心轮廓，不替换成墙体/门窗。"""
        dxf_path = os.path.join(self.tmp, "trace.dxf")
        code, payload = run_cli([self.full, "--mode", "lines", "--px-per-mm", str(PPM),
                                 "--origin", ORIGIN, "--out-dxf", dxf_path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["dxf_mode"], "trace")
        self.assertEqual(payload["tracer"], "hybrid")
        doc = ezdxf.readfile(dxf_path)
        entities = list(doc.modelspace())
        self.assertTrue({entity.dxf.layer for entity in entities}
                        <= {"A-SKETCH", "A-SOLID", "A-HATCH", "A-TEXT"})
        self.assertEqual(len(entities), payload["entities"])
        self.assertFalse(any(entity.dxftype() == "ARC" for entity in entities))

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_hough_tracer_still_available(self) -> None:
        dxf_path = os.path.join(self.tmp, "hough.dxf")
        code, payload = run_cli([self.full, "--mode", "lines", "--tracer", "hough",
                                 "--px-per-mm", str(PPM), "--origin", ORIGIN,
                                 "--out-dxf", dxf_path])
        self.assertEqual(code, 0, payload)
        entities = list(ezdxf.readfile(dxf_path).modelspace())
        self.assertEqual({entity.dxf.layer for entity in entities}, {"A-TRACE"})
        self.assertEqual(len(entities), payload["merged_segments"])

    def test_hough_clean_geometry_reports_preprocess(self) -> None:
        """Hough 后端也要走同一套网格/吸附统计，不能只有 hybrid 生效。"""
        code, payload = run_cli([self.full, "--tracer", "hough", "--clean-geometry",
                                 "--px-per-mm", str(PPM), "--origin", ORIGIN])
        self.assertEqual(code, 0, payload)
        self.assertIsNotNone(payload["preprocess"]["grid"], payload)
        self.assertTrue(payload["preprocess"]["snap"]["applied"], payload)

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_structured_dxf_matches_calls_in_walls_mode(self) -> None:
        dxf_path = os.path.join(self.tmp, "structured.dxf")
        calls_path = os.path.join(self.tmp, "structured.calls.json")
        code, payload = run_cli([self.full, "--mode", "walls", "--px-per-mm", str(PPM),
                                 "--origin", ORIGIN, "--out-dxf", dxf_path,
                                 "--out-calls", calls_path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["dxf_mode"], "structured")
        doc = ezdxf.readfile(dxf_path)
        layers = {entity.dxf.layer for entity in doc.modelspace()}
        self.assertTrue({"A-WALL", "A-TRACE"}.issubset(layers))
        calls = json.load(open(calls_path, encoding="utf-8"))["calls"]
        self.assertEqual(len(list(doc.modelspace())),
                         len(calls) + payload.get("dxf_trace_lines", 0), payload)

    def test_door_swing_inferred_from_arc(self) -> None:
        """四扇门开向各不相同：从开启弧反推的合页点与开向要和原图一致。"""
        spec = {"walls": [
            {"id": "a", "from": [0, 6000], "to": [8000, 6000], "thickness": 240,
             "openings": [{"kind": "door", "width": 900, "center": 4000,
                           "hinge": "start", "swing": "positive"}]},
            {"id": "b", "from": [0, 3000], "to": [8000, 3000], "thickness": 240,
             "openings": [{"kind": "door", "width": 900, "center": 4000,
                           "hinge": "end", "swing": "positive"}]},
            {"id": "c", "from": [0, 1000], "to": [8000, 1000], "thickness": 240,
             "openings": [{"kind": "door", "width": 900, "center": 4000,
                           "hinge": "start", "swing": "negative"}]},
            {"id": "d", "from": [9500, 0], "to": [9500, 7000], "thickness": 240,
             "openings": [{"kind": "door", "width": 900, "center": 3500,
                           "hinge": "end", "swing": "negative"}]},
        ]}
        calls = wall_plan.build_plan(spec, dict(LAYERS))["calls"]
        image = render(calls, os.path.join(self.tmp, "four-doors.png"))
        plan_path = os.path.join(self.tmp, "four-doors.json")
        code, payload = run_cli([image, "--mode", "walls", "--px-per-mm", str(PPM),
                                 "--origin", ORIGIN, "--out-plan", plan_path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["door_swings_confirmed"], 4, payload)
        self.assertEqual(payload["door_swings_unconfirmed"], 0, payload)
        found = json.load(open(plan_path, encoding="utf-8"))
        truth = [door_geometry_mm(wall, op)
                 for wall in spec["walls"] for op in wall["openings"]]
        detected = [door_geometry_mm(wall, op)
                    for wall in found["walls"] for op in wall["openings"]
                    if op["kind"] == "door"]
        self.assertEqual(len(detected), 4)
        for hinge, direction in detected:
            match = min(truth, key=lambda item: math.dist(item[0], hinge))
            self.assertLess(math.dist(match[0], hinge), 120.0,
                            "合页点偏差过大: %r vs %r" % (match[0], hinge))
            self.assertAlmostEqual(match[1][0], direction[0], delta=0.2)
            self.assertAlmostEqual(match[1][1], direction[1], delta=0.2)

    def test_stairs_detected_from_parallel_treads(self) -> None:
        """等间距平行踏步线 → stairs 规格（而不是一堆假墙）。"""
        spec = {
            "walls": [{"id": "wall", "from": [0, 6000], "to": [8000, 6000],
                       "thickness": 240}],
            "stairs": [{"origin": [2000, 2000], "angle": 90, "steps": 8,
                        "tread": 260, "width": 1100}],
        }
        calls = wall_plan.build_plan(spec, dict(LAYERS))["calls"]
        image = render(calls, os.path.join(self.tmp, "stairs.png"))
        plan_path = os.path.join(self.tmp, "stairs.json")
        code, payload = run_cli([image, "--mode", "walls", "--px-per-mm", str(PPM),
                                 "--origin", ORIGIN, "--stairs", "--out-plan", plan_path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["stairs_detected"], 1, payload)
        found = json.load(open(plan_path, encoding="utf-8"))
        self.assertEqual(len(found["stairs"]), 1)
        stair = found["stairs"][0]
        self.assertEqual(stair["steps"], 8)
        self.assertAlmostEqual(stair["tread"], 260, delta=15)
        self.assertAlmostEqual(stair["width"], 1100, delta=30)
        # 楼梯可以从任一端起画；比对"梯段中点"更稳
        angle = math.radians(stair["angle"])
        middle = (stair["origin"][0] + math.cos(angle) * stair["steps"] * stair["tread"] / 2,
                  stair["origin"][1] + math.sin(angle) * stair["steps"] * stair["tread"] / 2)
        self.assertLess(math.dist(middle, [2000, 3040]), 60)


class HelperTests(unittest.TestCase):
    """不依赖图像库的纯函数测试"""

    def test_merge_collinear_joins_small_gaps_only(self) -> None:
        segments = [itc._seg(0, 0, 100, 0), itc._seg(120, 0, 300, 0), itc._seg(900, 0, 1000, 0)]
        merged = itc.merge_collinear(segments, tol=1, join_gap=50)
        self.assertEqual([(round(s["start"]), round(s["end"])) for s in merged],
                         [(0, 300), (900, 1000)])

    def test_snap_to_grid_respects_max_shift(self) -> None:
        self.assertEqual(itc.snap_to_grid(103, 10, 5), 100)
        self.assertEqual(itc.snap_to_grid(132, 10, 2), 130)   # 差 2 ≤ 2，吸附
        self.assertEqual(itc.snap_to_grid(133, 10, 2), 133)   # 差 3 > 2，保持原值

    def test_wall_pairing_prefers_long_overlap(self) -> None:
        """墙面 + 窗中线共存时，要选跨整段墙的那一对，而不是半墙厚的中线对。"""
        face_top = itc._seg(0, 1000, 5000, 1000)     # 上墙面
        face_bottom = itc._seg(0, 1240, 5000, 1240)  # 下墙面（间距 240）
        window_line = itc._seg(2000, 1120, 2600, 1120)  # 窗中线，只跨洞口
        walls, leftovers = itc.pair_walls([face_top, face_bottom, window_line],
                                          min_thickness=80, max_thickness=500, min_overlap=0.6)
        self.assertEqual(len(walls), 1, walls)
        self.assertAlmostEqual(walls[0]["thickness"], 240)
        self.assertEqual((walls[0]["start"], walls[0]["end"]), (0, 5000))
        self.assertIn(window_line, leftovers)


@unittest.skipIf(cv2 is None or Image is None or np is None, "需要 OpenCV 与 Pillow")
class OpeningEvidenceTests(unittest.TestCase):
    def test_band_detects_gap_in_filled_wall(self) -> None:
        image = np.full((300, 600), 255, dtype=np.uint8)
        image[100:121, 50:451] = 200          # 灰色墙带
        image[100:102, 50:451] = 60
        image[119:121, 50:451] = 60
        image[100:121, 250:321] = 255         # 70px 洞口
        wall = {"orient": "H", "center": 110.0, "start": 50.0, "end": 450.0,
                "thickness": 20.0, "openings": []}
        found = itc.detect_band_openings([wall], image, min_width=40, max_width=120)
        self.assertEqual(found, 1)
        opening = wall["openings"][0]
        self.assertAlmostEqual(opening["center"], 285, delta=4)
        self.assertAlmostEqual(opening["width"], 70, delta=6)

    def test_band_skips_line_drawing(self) -> None:
        image = np.full((300, 600), 255, dtype=np.uint8)
        image[100:102, 50:451] = 0
        image[119:121, 50:451] = 0
        wall = {"orient": "H", "center": 110.0, "start": 50.0, "end": 450.0,
                "thickness": 20.0, "openings": []}
        self.assertEqual(itc.detect_band_openings([wall], image, 40, 120), 0)
        self.assertEqual(wall["openings"], [])

    def test_swing_inference_tolerates_center_offset(self) -> None:
        """墙中心线被家具/线宽带偏时，仍应从弧确认开向。"""
        spec = {"walls": [{"from": [0, 3000], "to": [8000, 3000], "thickness": 240,
                           "openings": [{"kind": "door", "width": 900, "center": 4000,
                                         "hinge": "start", "swing": "positive"}]}]}
        calls = wall_plan.build_plan(spec, dict(LAYERS))["calls"]
        path = os.path.join(tempfile.mkdtemp(prefix="cad-offset-"), "door.png")
        render(calls, path)
        binary, _ = itc.load_binary(path, False, False, False)
        wall = {"orient": "H", "center": 1040.0 + 8.0,   # 故意偏 8px
                "start": 40.0, "end": 1640.0, "thickness": 48.0,
                "openings": [{"kind": "door", "width": 180.0, "center": 840.0,
                              "hinge": "start", "swing": "positive"}]}
        stats = itc.infer_door_swings(binary, [wall], max_door_px=250)
        self.assertEqual(stats["confirmed"], 1, stats)
        self.assertEqual(wall["openings"][0]["swing"], "positive")


if __name__ == "__main__":
    unittest.main(verbosity=2)
