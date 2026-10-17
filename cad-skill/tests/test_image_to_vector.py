#!/usr/bin/env python3
"""image_to_vector.py 的离线测试：用合成图形（圆环、带孔方框、小噪点）验证
轮廓/孔识别、保真度指标、SVG 与 DXF 内容，以及超采样对"整体缩小"的修正。

依赖：opencv-python-headless、Pillow（DXF 用例另需 ezdxf）。
运行：python3 tests/test_image_to_vector.py
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

import image_to_vector as iv  # noqa: E402

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


def run_cli(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = iv.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def make_shapes(path: str, transparent: bool = False, inverted: bool = False) -> str:
    """圆环（1 外 1 孔）+ 带孔方框（1 外 1 孔）+ 实心方块（1 外）+ 2px 噪点（应被丢弃）"""
    if inverted:                      # 白字黑底
        background, ink = (0, 0, 0, 255), (255, 255, 255, 255)
    else:
        background = (0, 0, 0, 0) if transparent else (255, 255, 255, 255)
        ink = (20, 20, 20, 255)
    image = Image.new("RGBA", (461, 465), background)
    draw = ImageDraw.Draw(image)
    draw.ellipse((60, 60, 220, 220), fill=ink)          # 圆环外圈
    draw.ellipse((100, 100, 180, 180), fill=background)  # 圆环内孔
    draw.rectangle((300, 80, 420, 200), fill=ink)        # 方框
    draw.rectangle((330, 110, 390, 170), fill=background)  # 方框内孔
    draw.rectangle((300, 300, 360, 360), fill=ink)       # 实心方块
    draw.rectangle((440, 440, 442, 442), fill=ink)       # 噪点，面积 4 < min_area
    image.save(path)
    return path


@unittest.skipIf(cv2 is None or Image is None, "需要 opencv-python-headless 与 Pillow")
class ShapeTracingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cad-shapes-")
        cls.opaque = make_shapes(os.path.join(cls.tmp, "shapes.png"))
        cls.transparent = make_shapes(os.path.join(cls.tmp, "shapes_alpha.png"), transparent=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_contours_and_holes(self) -> None:
        code, payload = run_cli([self.opaque, "--min-area", "20"])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["outer_contours"], 3)
        self.assertEqual(payload["holes"], 2)
        self.assertEqual(payload["dropped_contours"], 1)   # 2px 噪点被丢弃

    def test_fidelity_metrics(self) -> None:
        code, payload = run_cli([self.opaque])
        self.assertGreaterEqual(payload["iou"], 0.95)
        self.assertLessEqual(abs(payload["area_ratio"] - 1.0), 0.03)

    def test_supersample_removes_shrinkage(self) -> None:
        """关闭超采样时轮廓整体偏小，开启后面积比更贴近 1。"""
        _, coarse = run_cli([self.opaque, "--supersample", "1"])
        _, fine = run_cli([self.opaque, "--supersample", "4"])
        self.assertLessEqual(abs(fine["area_ratio"] - 1.0),
                             abs(coarse["area_ratio"] - 1.0) + 1e-9)
        self.assertGreaterEqual(fine["area_ratio"], coarse["area_ratio"])

    def test_transparent_background(self) -> None:
        code, payload = run_cli([self.transparent])
        self.assertEqual(code, 0, payload)
        self.assertEqual((payload["outer_contours"], payload["holes"]), (3, 2))

    def test_white_on_black_is_auto_detected(self) -> None:
        """少数派法则：白字黑底不需要手工 --invert 也能认出前景"""
        path = make_shapes(os.path.join(self.tmp, "white_on_black.png"), inverted=True)
        code, payload = run_cli([path])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["outer_contours"], 3)

    def test_svg_output(self) -> None:
        svg_path = os.path.join(self.tmp, "shapes.svg")
        code, payload = run_cli([self.opaque, "--out-svg", svg_path, "--px-per-mm", "0.5"])
        self.assertEqual(code, 0, payload)
        with open(svg_path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn('fill-rule="evenodd"', content)     # 孔靠 even-odd 挖空
        self.assertEqual(content.count("M "), 5)          # 3 外轮廓 + 2 孔
        self.assertIn("mm", content)
        # 尺寸要用物理量核对：宽度(mm) = 外接框像素 / (px per mm)
        width_mm = float(content.split('width="')[1].split("mm")[0])
        self.assertAlmostEqual(width_mm, payload["bbox_px"][0] / 0.5, delta=1.0)

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_dxf_output_with_hatch_islands(self) -> None:
        dxf_path = os.path.join(self.tmp, "shapes.dxf")
        code, payload = run_cli([self.opaque, "--out-dxf", dxf_path, "--px-per-mm", "0.5"])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["polylines"], 5)
        self.assertEqual(payload["hatches"], 3)
        doc = ezdxf.readfile(dxf_path)
        self.assertEqual(doc.header.get("$INSUNITS"), 4)       # px_per_mm 给了 → 毫米
        model = doc.modelspace()
        types = {}
        for entity in model:
            types[entity.dxftype()] = types.get(entity.dxftype(), 0) + 1
        self.assertEqual(types, {"LWPOLYLINE": 5, "HATCH": 3})
        # 带孔的两个形状，其填充边界应包含洞口路径
        path_counts = sorted(len(entity.paths) for entity in model.query("HATCH"))
        self.assertEqual(path_counts, [1, 2, 2])

    def test_metrics_only_run(self) -> None:
        code, payload = run_cli([self.opaque])
        self.assertEqual(code, 0, payload)
        self.assertIn("仅报告描摹指标", payload["note"])
        self.assertGreaterEqual(payload["iou"], 0.95)

    def test_uniform_image_reports_error(self) -> None:
        blank = os.path.join(self.tmp, "blank.png")
        Image.new("RGB", (80, 80), (255, 255, 255)).save(blank)
        code, payload = run_cli([blank, "--out-svg", os.path.join(self.tmp, "blank.svg")])
        self.assertEqual(code, 1)
        self.assertIn("没有", payload["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
