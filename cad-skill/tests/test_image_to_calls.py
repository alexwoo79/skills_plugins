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
            draw.arc(box, start=-(args["end_angle"] or 360), end=-args["start_angle"], fill=0, width=2)
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


def compare(truth: list, found: list, axis_tol: float = 60) -> dict:
    """真值 vs 识别结果：墙召回、轴/厚度误差、洞口召回与误差、类型命中"""
    metrics = {"walls_matched": 0, "walls_extra": 0, "axis": [], "thickness": [],
               "openings_hit": 0, "openings_total": 0, "types_ok": 0,
               "width_err": [], "pos_err": []}
    for gt_wall in truth:
        best = None
        for wall in found:
            if wall["o"] != gt_wall["o"] or abs(wall["offset"] - gt_wall["offset"]) > axis_tol:
                continue
            overlap = min(wall["e"], gt_wall["e"]) - max(wall["s"], gt_wall["s"])
            if overlap <= 0:
                continue
            score = overlap / (gt_wall["e"] - gt_wall["s"])
            if best is None or score > best[0]:
                best = (score, wall)
        if best is None:
            continue
        metrics["walls_matched"] += 1
        wall = best[1]
        metrics["axis"].append(abs(wall["offset"] - gt_wall["offset"]))
        metrics["thickness"].append(abs(wall["t"] - gt_wall["t"]))
        for kind, width, position in gt_wall["openings"]:
            metrics["openings_total"] += 1
            near = [op for op in wall["openings"] if abs(op[2] - position) < 250]
            if not near:
                continue
            metrics["openings_hit"] += 1
            metrics["types_ok"] += (near[0][0] == kind)
            metrics["width_err"].append(abs(near[0][1] - width))
            metrics["pos_err"].append(abs(near[0][2] - position))
    metrics["walls_extra"] = len(found) - metrics["walls_matched"]
    for key in ("axis", "thickness", "width_err", "pos_err"):
        metrics[key] = max(metrics[key]) if metrics[key] else 0.0
    return metrics


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

    def test_calibration_is_required_for_walls_mode(self) -> None:
        code, payload = run_cli([self.full, "--mode", "walls"])
        self.assertEqual(code, 2)
        self.assertIn("标定", payload["error"])

    def test_lines_mode_works_without_calibration(self) -> None:
        code, payload = run_cli([self.full, "--mode", "lines"])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["px_per_mm"], 1.0)

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
        dxf_path = os.path.join(self.tmp, "trace.dxf")
        code, payload = run_cli([self.full, "--mode", "lines", "--px-per-mm", str(PPM),
                                 "--origin", ORIGIN, "--out-dxf", dxf_path])
        self.assertEqual(code, 0, payload)
        doc = ezdxf.readfile(dxf_path)
        layers = {entity.dxf.layer for entity in doc.modelspace()}
        self.assertIn("A-WALL", layers)
        self.assertIn("A-TRACE", layers)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
