#!/usr/bin/env python3
"""DXF 前后叠加对比测试。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

try:
    import ezdxf
except ImportError:
    ezdxf = None

import dxf_overlay_compare as compare_module  # noqa: E402


@unittest.skipIf(ezdxf is None, "需要 ezdxf")
class OverlayCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="cad-overlay-")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dxf(self, name: str, lines) -> str:
        path = os.path.join(self.tmp, name)
        doc = ezdxf.new("R2010")
        model = doc.modelspace()
        for first, second in lines:
            model.add_line(first, second)
        doc.saveas(path)
        return path

    def test_overlay_marks_before_after_and_overlap(self) -> None:
        image_path = os.path.join(self.tmp, "plan.png")
        cv2.imwrite(image_path, np.full((150, 200, 3), 255, dtype=np.uint8))
        before = self._dxf("before.dxf", [
            ((10, 10), (100, 10)),
            ((10, 30), (100, 30)),
        ])
        after = self._dxf("after.dxf", [
            ((10, 20), (100, 20)),
            ((10, 30), (100, 30)),
        ])
        out = os.path.join(self.tmp, "compare.png")
        result = compare_module.compare(image_path, before, after, out)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(result["before"]["entity_count"], 2, result)
        self.assertEqual(result["after"]["entity_count"], 2, result)
        self.assertEqual(result["entity_delta"], 0)
        canvas = cv2.imread(out)
        self.assertEqual(canvas.shape[:2], (150, 200))
        # 红/绿/黄三种颜色都应出现。
        red = np.logical_and(canvas[:, :, 2] >= 200, canvas[:, :, 1] < 80)
        green = np.logical_and(canvas[:, :, 1] >= 150, canvas[:, :, 2] < 80)
        yellow = np.logical_and(canvas[:, :, 1] >= 180, canvas[:, :, 2] >= 180)
        self.assertTrue(red.any(), "缺少 before 红色线")
        self.assertTrue(green.any(), "缺少 after 绿色线")
        self.assertTrue(yellow.any(), "缺少 overlap 黄色线")

    def test_calibrated_mapping_uses_origin(self) -> None:
        image_path = os.path.join(self.tmp, "plan.png")
        cv2.imwrite(image_path, np.full((100, 100, 3), 255, dtype=np.uint8))
        dxf_path = self._dxf("one.dxf", [((10, 10), (20, 10))])
        result = compare_module.compare(image_path, dxf_path, dxf_path,
                                        os.path.join(self.tmp, "mm.png"),
                                        px_per_mm=2.0, origin=(0.0, 100.0))
        self.assertEqual(result["transform"]["mode"], "calibrated")
        self.assertEqual(result["transform"]["px_per_mm"], 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
