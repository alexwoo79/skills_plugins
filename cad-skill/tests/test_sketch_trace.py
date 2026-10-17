#!/usr/bin/env python3
"""混合描线器测试：骨架中心线、实心轮廓、2048 画布、OCR 文本层与计时。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import sketch_trace  # noqa: E402

try:
    import ezdxf
except ImportError:
    ezdxf = None


@unittest.skipIf(cv2 is None, "需要 opencv")
class HybridTracerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="cad-trace-")
        self.image = os.path.join(self.tmp, "plan.png")
        canvas = np.full((300, 400), 255, dtype=np.uint8)
        cv2.rectangle(canvas, (40, 40), (200, 200), 0, 2)     # 细线矩形
        cv2.line(canvas, (40, 260), (350, 260), 0, 2)          # 细线
        cv2.rectangle(canvas, (260, 60), (330, 130), 0, -1)    # 实心块
        cv2.imwrite(self.image, canvas)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_line_skeleton_by_default(self) -> None:
        """默认所有墨迹走骨架：一条笔画一条多段线，不产生轮廓双线。"""
        dxf_path = os.path.join(self.tmp, "out.dxf")
        stats = sketch_trace.trace(self.image, out_dxf=dxf_path)
        self.assertGreaterEqual(stats["sketch"], 2, stats)
        self.assertEqual(stats["solid"], 0, stats)
        self.assertIn("trace_ms", stats)
        self.assertIn("ms", stats)
        if ezdxf is not None:
            doc = ezdxf.readfile(dxf_path)
            layers = {entity.dxf.layer for entity in doc.modelspace()}
            self.assertEqual(layers, {"A-SKETCH"})
            self.assertEqual(doc.header.get("$INSUNITS"), 0)   # 未标定 → 像素

    def test_solid_contours_are_opt_in(self) -> None:
        stats = sketch_trace.trace(self.image, solid_contours=True, solid_px=16,
                                   out_dxf=os.path.join(self.tmp, "solid.dxf"))
        self.assertGreaterEqual(stats["solid"], 1, stats)
        if ezdxf is not None:
            layers = {entity.dxf.layer for entity in
                      ezdxf.readfile(os.path.join(self.tmp, "solid.dxf")).modelspace()}
            self.assertTrue({"A-SKETCH", "A-SOLID"}.issubset(layers))

    def test_max_side_caps_working_canvas(self) -> None:
        big = os.path.join(self.tmp, "big.png")
        canvas = np.full((500, 1000), 255, dtype=np.uint8)
        cv2.line(canvas, (50, 250), (900, 250), 0, 3)
        cv2.imwrite(big, canvas)
        stats = sketch_trace.trace(big, max_side=300)
        self.assertLessEqual(max(stats["working_size"]), 300, stats)
        self.assertEqual(stats["src_size"], [1000, 500])

    def test_ocr_text_layer_and_stroke_removal(self) -> None:
        original = sketch_trace.ocr_texts
        sketch_trace.ocr_texts = lambda *args, **kwargs: (
            [{"text": "ROOM", "confidence": 0.95, "x": 0.1, "y": 0.4,
              "w": 0.2, "h": 0.1}], "fake")
        try:
            stats = sketch_trace.trace(self.image, ocr=True,
                                       out_dxf=os.path.join(self.tmp, "ocr.dxf"))
        finally:
            sketch_trace.ocr_texts = original
        self.assertEqual(stats["texts"], 1, stats)
        self.assertTrue(stats["text_strokes_removed"])
        self.assertEqual(stats["ocr_backend"], "fake")
        if ezdxf is not None:
            doc = ezdxf.readfile(os.path.join(self.tmp, "ocr.dxf"))
            texts = [(e.dxf.text, e.dxf.layer) for e in doc.modelspace()
                     if e.dxftype() == "TEXT"]
            self.assertEqual(texts, [("ROOM", "A-TEXT")])

    def test_millimetre_units_when_calibrated(self) -> None:
        if ezdxf is None:
            self.skipTest("需要 ezdxf")
        dxf_path = os.path.join(self.tmp, "mm.dxf")
        stats = sketch_trace.trace(self.image, px_per_mm=0.1, out_dxf=dxf_path)
        self.assertAlmostEqual(stats["px_per_mm"], 0.1)
        doc = ezdxf.readfile(dxf_path)
        self.assertEqual(doc.header.get("$INSUNITS"), 4)

    def test_windows_ocr_hint_lists_install_steps(self) -> None:
        hint = sketch_trace.ocr_install_hint("win32")
        joined = " ".join(hint["commands"])
        self.assertEqual(hint["platform"], "windows")
        self.assertIn("winget", joined)
        self.assertIn("pytesseract", joined)
        self.assertIn("chi_sim", joined)
        self.assertIn("setup-windows.md#ocr", hint["doc"])

    def test_calibrated_text_uses_paper_height(self) -> None:
        original = sketch_trace.ocr_texts
        sketch_trace.ocr_texts = lambda *args, **kwargs: (
            [{"text": "A", "confidence": 0.95, "x": 0.1, "y": 0.6,
              "w": 0.2, "h": 0.1},
             {"text": "B", "confidence": 0.95, "x": 0.1, "y": 0.2,
              "w": 0.2, "h": 0.1}], "fake")
        try:
            dxf_path = os.path.join(self.tmp, "paper.dxf")
            stats = sketch_trace.trace(self.image, ocr=True, px_per_mm=0.1,
                                       out_dxf=dxf_path)
        finally:
            sketch_trace.ocr_texts = original
        self.assertAlmostEqual(stats["text_height_mm"], 350.0)
        if ezdxf is not None:
            doc = ezdxf.readfile(dxf_path)
            texts = [e for e in doc.modelspace() if e.dxftype() == "TEXT"]
            self.assertEqual([e.dxf.height for e in texts], [350.0, 350.0])
            # 两段文字都要落在画布内，且不因字高变量覆盖而整体偏移
            positions = sorted(round(e.dxf.insert.y, 1) for e in texts)
            self.assertEqual(len(set(positions)), 2)
            self.assertTrue(all(0 < value < 3000 for value in positions), positions)
            self.assertEqual(doc.styles.get("TZ_HZ").dxf.bigfont, "gbcbig.shx")

    def test_ocr_requested_without_backend_raises_hint(self) -> None:
        original = sketch_trace.ocr_texts
        sketch_trace.ocr_texts = lambda *args, **kwargs: ([], None)
        try:
            with self.assertRaises(RuntimeError) as caught:
                sketch_trace.trace(self.image, ocr=True)
        finally:
            sketch_trace.ocr_texts = original
        message = str(caught.exception)
        self.assertIn("OCR 不可用", message)
        self.assertIn("安装", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
