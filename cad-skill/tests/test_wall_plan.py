#!/usr/bin/env python3
"""wall_plan.py 的离线测试：示例资产 + 几何不变量 + 跨墙检查。

锁定 `assets/example-two-bedroom/plan.json` 的输出数字（改了示例或改了换算逻辑
都会在这里暴露），并保证"洞口贴墙角"这类制图错误一定会告警。

运行：python3 tests/test_wall_plan.py
"""

from __future__ import annotations

import collections
import contextlib
import copy
import io
import json
import math
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
SCRIPTS = os.path.join(_SKILL, "scripts")
ASSET = os.path.join(_SKILL, "assets", "example-two-bedroom", "plan.json")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import wall_plan  # noqa: E402

LAYERS = {"wall": "A-WALL", "door": "A-DOOR", "window": "A-WINDOW"}


def build(spec, scale: float = 1.0):
    return wall_plan.build_plan(spec, dict(LAYERS), scale=scale)


def load_example():
    with open(ASSET, encoding="utf-8") as handle:
        return json.load(handle)


class ExampleAssetTests(unittest.TestCase):
    """示例资产是给人照抄的，因此它的数字和清洁度都要锁住。"""

    def setUp(self) -> None:
        self.plan = build(load_example())

    def test_summary_numbers(self) -> None:
        self.assertEqual(self.plan["summary"]["walls"], 8)
        self.assertEqual(self.plan["summary"]["openings"], 10)
        self.assertEqual(self.plan["summary"]["wall_segments"], 18)  # 每面墙被洞口切段
        self.assertEqual(self.plan["summary"]["calls"], 63)

    def test_layer_split(self) -> None:
        by_layer = collections.Counter(call["args"].get("layer") for call in self.plan["calls"])
        # A-WALL = 18 段墙身 + 10 洞口×2 封边；A-DOOR = 5 门×(门扇+弧)；
        # A-WINDOW = 5 窗×3 线（洞口内外墙面 + 中位窗线）
        self.assertEqual(by_layer["A-WALL"], 38)
        self.assertEqual(by_layer["A-DOOR"], 10)
        self.assertEqual(by_layer["A-WINDOW"], 15)

    def test_example_has_no_warnings(self) -> None:
        """示例必须是干净方案：洞口不越界、不重叠、不贴墙角。"""
        self.assertEqual(self.plan["warnings"], [])

    def test_geometry_invariants(self) -> None:
        door_widths = {op["width"] for wall in load_example()["walls"]
                       for op in wall["openings"] if op["kind"] == "door"}
        for call in self.plan["calls"]:
            tool, args = call["tool"], call["args"]
            if tool == "draw_polyline":
                points = args["points"]
                self.assertTrue(args["closed"], "墙身必须是闭合多段线")
                self.assertEqual(len(points), 4, "墙身矩形应有 4 个角点")
                lengths = [math.dist(points[i], points[(i + 1) % 4]) for i in range(4)]
                self.assertAlmostEqual(lengths[0], lengths[2], delta=0.01, msg=str(call))
                self.assertAlmostEqual(lengths[1], lengths[3], delta=0.01, msg=str(call))
            elif tool == "draw_arc":  # 门弧半径必须等于某个门宽
                self.assertIn(round(args["radius"], 3), {round(w, 3) for w in door_widths})
                sweep = (args["end_angle"] - args["start_angle"]) % 360
                self.assertAlmostEqual(sweep, 90, delta=1e-6)


class CrossingWarningTests(unittest.TestCase):
    """洞口贴着交叉墙：几何合法但制图错误，必须告警。"""

    def _plan(self, door_center: float):
        return {"walls": [
            {"id": "H", "from": [0, 0], "to": [12000, 0], "thickness": 200,
             "openings": [{"kind": "door", "width": 900, "center": door_center}]},
            {"id": "V", "from": [6600, 0], "to": [6600, 4000], "thickness": 200},
        ]}

    def test_too_close_is_reported(self) -> None:
        plan = build(self._plan(6700))  # 洞口 6250~7150，交叉墙在 6600
        self.assertTrue(plan["warnings"], "贴墙角的洞口必须告警")
        self.assertIn("距交叉墙", plan["warnings"][0])
        self.assertIn("V", plan["warnings"][0])

    def test_clear_distance_is_quiet(self) -> None:
        plan = build(self._plan(7600))  # 洞口 7150~8050，距 6600 有 550
        self.assertEqual(plan["warnings"], [])


class DrawingExtrasTests(unittest.TestCase):
    """轴网、自动尺寸、楼梯与待复核标记：都是可单独开关的附加 phase。"""

    def _spec(self, **extra):
        spec = {"walls": [
            {"id": "S", "from": [0, 0], "to": [12000, 0], "thickness": 240},
            {"id": "N", "from": [0, 8000], "to": [12000, 8000], "thickness": 240},
            {"id": "W", "from": [0, 0], "to": [0, 8000], "thickness": 240},
            {"id": "E", "from": [12000, 0], "to": [12000, 8000], "thickness": 240},
        ]}
        spec.update(extra)
        return spec

    def test_axis_auto_uses_wall_centrelines(self) -> None:
        plan = build(self._spec(axis={"auto": True}))
        grid = [call for call in plan["calls"] if call["args"].get("layer") == "A-GRID"]
        lines = [call for call in grid if call["tool"] == "draw_line"]
        labels = [call["args"]["text"] for call in grid if call["tool"] == "draw_text"]
        self.assertEqual(len(lines), 4)  # x: 0/12000，y: 0/8000
        self.assertEqual(sorted(labels), ["1", "2", "A", "B"])
        vertical = [call for call in lines
                    if call["args"]["start_point"][0] == call["args"]["end_point"][0]]
        self.assertEqual(len(vertical), 2)
        # 轴线要伸出墙外：默认 1500，墙外接框是 -120 ~ 8120
        self.assertLess(min(call["args"]["start_point"][1] for call in vertical), -1500)
        self.assertGreater(max(call["args"]["end_point"][1] for call in vertical), 9000)

    def test_explicit_axis_labels(self) -> None:
        plan = build(self._spec(axis={"x": [0, 6000, 12000], "y": [0, 8000],
                                      "labels": {"x": ["A", "B", "C"], "y": ["X", "Y"]}}))
        labels = [call["args"]["text"] for call in plan["calls"]
                  if call["args"].get("layer") == "A-GRID" and call["tool"] == "draw_text"]
        self.assertEqual(sorted(labels), ["A", "B", "C", "X", "Y"])

    def test_dimensions_chain_plus_overall(self) -> None:
        plan = build(self._spec(dims={"offset": 1200, "textheight": 250}))
        dims = [call for call in plan["calls"] if call["tool"] == "add_dimension"]
        self.assertEqual(len(dims), 4)  # 两个方向：各 1 段 + 1 道总尺寸
        self.assertTrue(all(call["args"]["layer"] == "A-DIM" for call in dims))
        self.assertTrue(all(call["args"]["textheight"] == 250 for call in dims))
        self.assertAlmostEqual(min(call["args"]["text_position"][1] for call in dims), -2670.0)
        self.assertAlmostEqual(min(call["args"]["text_position"][0] for call in dims), -2670.0)
        segments = {(call["args"]["start_point"][0] - call["args"]["end_point"][0])
                    for call in dims}
        self.assertIn(-12000.0, {round(value, 3) for value in segments})

    def test_stairs_emit_treads_arrow_and_label(self) -> None:
        plan = build(self._spec(stairs=[{"origin": [2000, 2000], "angle": 90, "steps": 6,
                                         "tread": 260, "width": 1100}]))
        stairs = [call for call in plan["calls"] if call["args"].get("layer") == "A-STAR"]
        self.assertEqual(len(stairs), 11)  # 7 条踏步线 + 走向线 + 2 条箭头 + 文字
        self.assertEqual(sum(1 for call in stairs if call["tool"] == "draw_line"), 10)
        self.assertEqual([call["args"]["text"] for call in stairs
                          if call["tool"] == "draw_text"], ["楼梯"])

    def test_review_marker_survives_to_summary(self) -> None:
        spec = self._spec()
        spec["walls"][0]["openings"] = [{"kind": "door", "width": 900, "center": 3000,
                                         "needs_review": True,
                                         "review_reason": "未找到开启弧"}]
        plan = build(spec)
        self.assertEqual(plan["summary"]["needs_review"], 1)
        self.assertEqual(plan["review"][0]["reason"], "未找到开启弧")
        self.assertEqual(plan["review"][0]["wall"], "S")

    def test_meter_spec_scales_every_phase(self) -> None:
        spec = {
            "walls": [{"from": [0, 0], "to": [6, 0], "thickness": 0.24}],
            "axis": {"x": [0, 6], "y": [0]},
            "dims": {"offset": 1.2},
            "stairs": [{"origin": [1, 1], "steps": 3, "tread": 0.26, "width": 1.1}],
        }
        plan = build(spec, scale=1000.0)
        grid = [call for call in plan["calls"] if call["args"].get("layer") == "A-GRID"]
        self.assertEqual(len(grid), 9)  # 2 条 x 轴 + 1 条 y 轴，每条 3 个图元
        treads = [call for call in plan["calls"]
                  if call["args"].get("layer") == "A-STAR" and call["tool"] == "draw_line"]
        x_positions = [call["args"]["start_point"][0] for call in treads[:4]]
        self.assertAlmostEqual(x_positions[1] - x_positions[0], 260.0, delta=0.01)


class ValidationTests(unittest.TestCase):
    def test_opening_outside_wall_is_rejected(self) -> None:
        spec = {"walls": [{"from": [0, 0], "to": [5000, 0], "thickness": 240,
                           "openings": [{"kind": "door", "width": 1000, "center": 4700}]}]}
        with self.assertRaises(wall_plan.SpecError):
            build(spec)

    def test_overlapping_openings_are_rejected(self) -> None:
        spec = {"walls": [{"from": [0, 0], "to": [5000, 0], "thickness": 240,
                           "openings": [{"kind": "door", "width": 1000, "center": 2000},
                                        {"kind": "window", "width": 2000, "center": 2500}]}]}
        with self.assertRaises(wall_plan.SpecError):
            build(spec)

    def test_main_exit_code_for_bad_spec(self) -> None:
        spec = {"walls": [{"from": [0, 0], "to": [0, 0], "thickness": 240}]}
        path = os.path.join(os.path.dirname(ASSET), "_tmp_bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(spec, handle)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = wall_plan.main([path])
            self.assertEqual(code, 2)
        finally:
            os.remove(path)

    def test_meter_input_is_scaled(self) -> None:
        spec = {"walls": [{"from": [0, 0], "to": [5, 0], "thickness": 0.24,
                           "openings": [{"kind": "door", "width": 1, "center": 2.5}]}]}
        plan = build(spec, scale=1000.0)  # --unit m
        self.assertEqual(plan["summary"]["scaled_from"], "m")
        xs = [point[0] for call in plan["calls"] if call["tool"] == "draw_polyline"
              for point in call["args"]["points"]]
        self.assertEqual(max(xs), 5120.0)  # 5000 + 半墙厚 120（墙端延伸）
        self.assertEqual(min(xs), -120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
