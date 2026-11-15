#!/usr/bin/env python3
"""扫描件 contour 后端的离线回归测试。

重点验证：
  * `px_per_mm` 的语义是“像素 / 毫米”，写入 DXF 时除它，而不是乘它；
  * 未标定时 `$INSUNITS=0`，标定后为 4；
  * `--scan-*` 参数、预览、REFERENCE 注释和 A-TRACE 图层可用；
  * `--scan-max-side` 缩小画布时仍能保持真实尺寸。
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
_SKILL = os.path.dirname(_HERE)
SCRIPTS = os.path.join(_SKILL, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import image_to_calls as itc  # noqa: E402

try:
    import cv2
except ImportError:
    cv2 = None
try:
    import numpy as np
except ImportError:
    np = None
try:
    import ezdxf
except ImportError:
    ezdxf = None


def run_cli(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = itc.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def make_scan(path: str) -> str:
    """1660px 长线 + 小方框 + 一个小字，模拟扫描线稿。"""
    image = np.full((240, 1700), 255, dtype=np.uint8)
    cv2.line(image, (20, 120), (1680, 120), 0, 2)
    cv2.rectangle(image, (100, 40), (160, 90), 0, 2)
    cv2.putText(image, "A", (220, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
    cv2.imwrite(path, image)
    return path


def dxf_bbox(path: str):
    doc = ezdxf.readfile(path)
    xs, ys = [], []
    for entity in doc.modelspace():
        if entity.dxftype() != "LWPOLYLINE":
            continue
        for x, y in entity.get_points("xy"):
            xs.append(float(x))
            ys.append(float(y))
    if not xs:
        raise AssertionError("DXF 里没有 LWPOLYLINE")
    return min(xs), min(ys), max(xs), max(ys), doc


@unittest.skipIf(cv2 is None or np is None or ezdxf is None,
                 "需要 opencv-python-headless、numpy 与 ezdxf")
class ContourTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cad-contour-")
        cls.image = make_scan(os.path.join(cls.tmp, "scan.png"))
        cls.ppm = 1660.0 / 16200.0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_calibrated_span_is_16200_mm(self) -> None:
        """1660px 的尺寸链在 DXF 里必须是 16200mm，而不是 170mm。"""
        dxf = os.path.join(self.tmp, "calibrated.dxf")
        code, payload = run_cli([
            self.image, "--tracer", "contour",
            "--px-per-mm", str(self.ppm), "--out-dxf", dxf,
        ])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["tracer"], "contour")
        self.assertAlmostEqual(payload["px_per_mm"], self.ppm, places=6)
        min_x, _, max_x, _, doc = dxf_bbox(dxf)
        self.assertAlmostEqual(max_x - min_x, 16200.0, delta=162.0)
        self.assertEqual(doc.header.get("$INSUNITS"), 4)

    def test_uncalibrated_is_unitless(self) -> None:
        dxf = os.path.join(self.tmp, "unitless.dxf")
        code, payload = run_cli([
            self.image, "--tracer", "contour", "--out-dxf", dxf,
        ])
        self.assertEqual(code, 0, payload)
        self.assertIsNone(payload["px_per_mm"])
        min_x, _, max_x, _, doc = dxf_bbox(dxf)
        self.assertAlmostEqual(max_x - min_x, 1660.0, delta=5.0)
        self.assertEqual(doc.header.get("$INSUNITS"), 0)

    def test_scan_options_preview_and_note(self) -> None:
        dxf = os.path.join(self.tmp, "options.dxf")
        preview = os.path.join(self.tmp, "options.png")
        code, payload = run_cli([
            self.image, "--tracer", "contour",
            "--scan-threshold", "128", "--scan-blur", "gaussian",
            "--scan-epsilon", "1.0", "--scan-min-area", "4",
            "--note", "SCAN TEST", "--preview", preview,
            "--out-dxf", dxf,
        ])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["threshold"], 128)
        self.assertEqual(payload["blur"], "gaussian")
        self.assertEqual(payload["epsilon"], 1.0)
        self.assertEqual(payload["min_area"], 4.0)
        self.assertTrue(os.path.getsize(preview) > 0)
        self.assertEqual(payload["audit_errors"], 0)

        doc = ezdxf.readfile(dxf)
        layers = {entity.dxf.layer for entity in doc.modelspace()}
        self.assertIn("A-TRACE", layers)
        self.assertIn("REFERENCE", layers)

    def test_scan_max_side_keeps_real_scale(self) -> None:
        dxf = os.path.join(self.tmp, "half.dxf")
        code, payload = run_cli([
            self.image, "--tracer", "contour",
            "--scan-max-side", "850",
            "--px-per-mm", str(self.ppm), "--out-dxf", dxf,
        ])
        self.assertEqual(code, 0, payload)
        self.assertAlmostEqual(payload["source_scale"], 0.5, places=3)
        self.assertAlmostEqual(payload["px_per_mm"], self.ppm * 0.5, places=6)
        min_x, _, max_x, _, _ = dxf_bbox(dxf)
        self.assertAlmostEqual(max_x - min_x, 16200.0, delta=200.0)

    def test_origin_maps_start_to_zero(self) -> None:
        dxf = os.path.join(self.tmp, "origin.dxf")
        code, payload = run_cli([
            self.image, "--tracer", "contour",
            "--px-per-mm", str(self.ppm),
            "--origin", "20,120", "--out-dxf", dxf,
        ])
        self.assertEqual(code, 0, payload)
        min_x, _, max_x, _, _ = dxf_bbox(dxf)
        self.assertAlmostEqual(min_x, 0.0, delta=25.0)
        self.assertAlmostEqual(max_x - min_x, 16200.0, delta=162.0)

    def test_contour_rejects_plan_outputs(self) -> None:
        plan = os.path.join(self.tmp, "should-not-exist.json")
        code, payload = run_cli([
            self.image, "--tracer", "contour", "--out-plan", plan,
        ])
        self.assertEqual(code, 2)
        self.assertIn("contour", payload["error"])
        self.assertFalse(os.path.exists(plan))

    def test_default_tracer_is_still_hybrid(self) -> None:
        code, payload = run_cli([self.image])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["tracer"], "hybrid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
