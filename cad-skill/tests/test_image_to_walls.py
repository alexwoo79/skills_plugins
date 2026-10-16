#!/usr/bin/env python3
"""image_to_walls.py 的离线测试：合成一张"实心厚墙 + 浅色细线 + 房间名"的平面图，
验证按厚度分墙、骨架取中心线、线work 分层、标定单位与几种边界情况。

依赖：opencv-python-headless、Pillow（DXF 用例另需 ezdxf）。
运行：python3 tests/test_image_to_walls.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import image_to_walls as iw  # noqa: E402

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

WALL_INK = 113        # 与真实家装平面图一致：墙是深灰实心块
LINE_INK = 212        # 家具/洁具是浅灰细线
WALL_PX = 18          # 墙厚（像素）
FURNITURE_PX = 3      # 家具线宽
TEXT_PX = 6           # 房间名笔画宽度，应被厚度过滤掉


def run_cli(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = iw.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def draw_plan(path: str, walls: bool = True, furniture: bool = True,
              labels: bool = True) -> str:
    """600×520 的小平面：一圈 18px 厚墙 + 一道隔墙 + 3px 家具线 + 6px 房间名"""
    image = Image.new("RGB", (600, 520), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    if walls:
        half = WALL_PX // 2
        # 四面外墙（中心线 x=100/500, y=60/460）
        draw.rectangle((100 - half, 60 - half, 500 + half, 60 + half), fill=(WALL_INK,) * 3)
        draw.rectangle((100 - half, 460 - half, 500 + half, 460 + half), fill=(WALL_INK,) * 3)
        draw.rectangle((100 - half, 60 - half, 100 + half, 460 + half), fill=(WALL_INK,) * 3)
        draw.rectangle((500 - half, 60 - half, 500 + half, 460 + half), fill=(WALL_INK,) * 3)
        # 一道内隔墙（中心线 x=300）
        draw.rectangle((300 - half, 60 + half, 300 + half, 460 - half), fill=(WALL_INK,) * 3)
    if furniture:
        colour = (LINE_INK,) * 3
        for box in ((140, 100, 260, 220), (340, 100, 460, 220), (140, 300, 260, 420)):
            draw.rectangle(box, outline=colour, width=FURNITURE_PX)   # 床/沙发轮廓
    if labels:
        # 房间名那种小笔画：够深但很细，必须被厚度过滤掉
        for x in range(200, 260, 12):
            draw.rectangle((x, 250, x + 4, 250 + TEXT_PX), fill=(WALL_INK,) * 3)
    image.save(path)
    return path


@unittest.skipIf(cv2 is None or Image is None, "需要 opencv-python-headless 与 Pillow")
class WallExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cad-walls-")
        cls.plan = draw_plan(os.path.join(cls.tmp, "plan.png"))
        cls.thin_only = draw_plan(os.path.join(cls.tmp, "thin.png"), walls=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_walls_are_found_with_right_thickness(self) -> None:
        code, payload = run_cli([self.plan])
        self.assertEqual(code, 0, payload)
        self.assertGreaterEqual(payload["wall_segments"], 4)
        self.assertLessEqual(payload["wall_segments"], 8)
        dominant = payload["thickness"]["groups"][0]
        self.assertAlmostEqual(dominant["thickness_px"], WALL_PX, delta=2.0)
        self.assertGreaterEqual(payload["wall_mask_coverage"], 0.9)

    def test_furniture_and_labels_are_not_walls(self) -> None:
        """3px 家具线与 6px 房间名都不是墙：所有墙段厚度都得接近 18px。"""
        code, payload = run_cli([self.plan])
        self.assertEqual(code, 0, payload)
        for group in payload["thickness"]["groups"]:
            self.assertGreaterEqual(group["thickness_px"], WALL_PX * 0.8, group)
        self.assertGreater(payload["trace_segments"], 0)     # 家具线进了线work

    def test_thin_only_image_reports_error(self) -> None:
        code, payload = run_cli([self.thin_only])
        self.assertEqual(code, 1)
        self.assertIn("没有识别到墙体", payload["error"])

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_dxf_layers_and_counts(self) -> None:
        dxf_path = os.path.join(self.tmp, "plan.dxf")
        code, payload = run_cli([self.plan, "--out-dxf", dxf_path, "--walls", "both"])
        self.assertEqual(code, 0, payload)
        doc = ezdxf.readfile(dxf_path)
        model = doc.modelspace()
        by_layer = {}
        for entity in model:
            by_layer[entity.dxf.layer] = by_layer.get(entity.dxf.layer, 0) + 1
        self.assertEqual(by_layer.get("A-WALL"), payload["wall_rectangles"])
        self.assertEqual(by_layer.get("A-WALL-CL"), payload["wall_centerlines"])
        self.assertEqual(by_layer.get("A-TRACE"), payload["trace_lines"])
        self.assertEqual(payload["wall_rectangles"], payload["wall_segments"])

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_no_trace_option(self) -> None:
        dxf_path = os.path.join(self.tmp, "walls_only.dxf")
        code, payload = run_cli([self.plan, "--out-dxf", dxf_path, "--no-trace"])
        self.assertEqual(code, 0, payload)
        doc = ezdxf.readfile(dxf_path)
        layers = {entity.dxf.layer for entity in doc.modelspace()}
        self.assertEqual(layers, {"A-WALL"})
        self.assertEqual(payload["trace_segments"], 0)

    def test_calibration_sets_units(self) -> None:
        plan_path = os.path.join(self.tmp, "walls.json")
        code, payload = run_cli([self.plan, "--px-per-mm", "0.1", "--out-plan", plan_path])
        self.assertEqual(code, 0, payload)
        self.assertNotIn("scale_note", payload)
        self.assertAlmostEqual(payload["thickness"]["groups"][0]["thickness_px"], WALL_PX, delta=2)
        with open(plan_path, encoding="utf-8") as handle:
            spec = json.load(handle)
        self.assertEqual(len(spec["walls"]), payload["wall_segments"])
        for wall in spec["walls"]:
            self.assertAlmostEqual(wall["thickness"], WALL_PX * 0.1, delta=0.3)

    def test_uncalibrated_run_warns_about_scale(self) -> None:
        code, payload = run_cli([self.plan])
        self.assertIn("未标定", payload["scale_note"])

    def test_extension_improves_coverage(self) -> None:
        """端部延伸半墙厚（CAD 墙身约定）能补上转角方块，覆盖率明显更高。"""
        _, extended = run_cli([self.plan])
        _, plain = run_cli([self.plan, "--no-extend-ends"])
        self.assertGreater(extended["wall_mask_coverage"], plain["wall_mask_coverage"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
