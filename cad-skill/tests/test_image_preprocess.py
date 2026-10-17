#!/usr/bin/env python3
"""预处理测试：网格去除、共线合并、端点吸附。"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import image_preprocess as pre  # noqa: E402


class GridRemovalTests(unittest.TestCase):
    def _grid_image(self) -> np.ndarray:
        height, width = 300, 400
        ink = np.zeros((height, width), dtype=np.uint8)
        for x in range(0, width, 25):
            ink[:, x] = 255
        for y in range(0, height, 25):
            ink[y, :] = 255
        ink[135:137, 20:380] = 255
        ink[20:280, 210:212] = 255
        return ink

    def test_removes_regular_grid_but_keeps_drawing(self) -> None:
        ink = self._grid_image()
        out, stats = pre.remove_background_grid(ink)
        self.assertTrue(stats["applied"], stats)
        self.assertGreaterEqual(stats["grid_lines_h"], 8, stats)
        self.assertGreaterEqual(stats["grid_lines_v"], 12, stats)
        self.assertGreater(stats["removed_pixels"], 0)
        # 水平/垂直墙线不在网格线上，必须完整保留。
        self.assertEqual(int((out[135:137, 20:380] > 0).sum()), 2 * 360)
        self.assertEqual(int((out[20:280, 210:212] > 0).sum()), 2 * 260)
        # 网格线本身应基本清空（交叉点可能保留少量像素）。
        self.assertLess(int((out[50, 30:180] > 0).sum()), 10)

    def test_irregular_long_lines_are_not_treated_as_grid(self) -> None:
        ink = np.zeros((300, 400), dtype=np.uint8)
        for y in (40, 120, 251):
            ink[y:y + 2, :] = 255
        out, stats = pre.remove_background_grid(ink)
        self.assertFalse(stats["applied"], stats)
        self.assertEqual(int((ink != out).sum()), 0)

    def test_grid_min_lines_can_be_raised(self) -> None:
        ink = self._grid_image()
        out, stats = pre.remove_background_grid(ink, min_lines=40)
        self.assertFalse(stats["applied"], stats)
        self.assertEqual(int((ink != out).sum()), 0)


class SnapTests(unittest.TestCase):
    def test_merge_collinear_paths_and_join_corner(self) -> None:
        paths = [
            [(10, 10), (50, 10.4)],
            [(50.7, 10.1), (100, 10.0)],
            [(100.2, 10.2), (100.1, 60)],
        ]
        result, stats = pre.snap_paths(paths)
        self.assertEqual(stats["paths_before"], 3)
        self.assertEqual(stats["paths_after"], 2)
        self.assertGreaterEqual(stats["merged_paths"], 1)
        horizontal = min(result, key=lambda path: abs(path[0][1] - path[-1][1]))
        self.assertLess(abs(horizontal[0][1] - horizontal[-1][1]), 0.01)
        vertical = next(path for path in result if path is not horizontal)
        self.assertLess(abs(vertical[0][0] - vertical[-1][0]), 0.01)
        # 两条线在转角处应共享同一个点。
        self.assertLess(min(abs(horizontal[0][0] - vertical[0][0])
                            + abs(horizontal[0][1] - vertical[0][1]),
                            abs(horizontal[-1][0] - vertical[0][0])
                            + abs(horizontal[-1][1] - vertical[0][1])), 0.01)

    def test_snap_segments_rebuilds_endpoints(self) -> None:
        segments = [
            pre._segment(10, 10, 50, 10.4),
            pre._segment(50.7, 10.1, 100, 10.0),
            pre._segment(100.2, 10.2, 100.1, 60),
        ]
        result, stats = pre.snap_segments(segments)
        self.assertEqual(stats["segments_before"], 3)
        self.assertLessEqual(stats["segments_after"], 2)
        self.assertLess(abs(result[0]["y1"] - result[0]["y2"]), 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
