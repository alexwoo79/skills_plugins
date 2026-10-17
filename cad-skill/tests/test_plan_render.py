#!/usr/bin/env python3
"""plan_render.py 的后端一致性测试：calls / scr / DXF / SVG 必须是同一份几何。

运行：python3 tests/test_plan_render.py
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
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
SCRIPTS = os.path.join(_SKILL, "scripts")
ASSET_PLAN = os.path.join(_SKILL, "assets", "example-two-bedroom", "plan.json")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import plan_render  # noqa: E402
import wall_plan  # noqa: E402

try:
    import ezdxf
except ImportError:
    ezdxf = None


def demo_spec() -> dict:
    with open(ASSET_PLAN, encoding="utf-8") as handle:
        spec = json.load(handle)
    spec["axis"] = {"auto": True}
    spec["dims"] = {"offset": 1200, "textheight": 250}
    spec["stairs"] = [{"origin": [2200, 4400], "angle": 90, "steps": 10,
                       "tread": 260, "width": 1100}]
    return spec


class BackendParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="cad-render-")
        self.spec = demo_spec()
        self.plan = plan_render.build(self.spec)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name: str) -> str:
        return os.path.join(self.tmp, name)

    def test_calls_and_scr_match(self) -> None:
        calls = plan_render.write_calls(self.plan, self.path("calls.json"))
        self.assertEqual(calls["calls"], len(self.plan["calls"]))
        scr = plan_render.write_scr(self.plan, self.path("plan.scr"))
        self.assertEqual(scr["entities"], len(self.plan["calls"]))
        self.assertEqual(scr["unsupported_skipped"], 0)
        self.assertEqual(set(scr["layers"]),
                         {"A-WALL", "A-DOOR", "A-WINDOW", "A-GRID", "A-DIM", "A-STAR"})
        text = open(self.path("plan.scr"), encoding="utf-8-sig").read()
        self.assertIn("_.PLINE", text)
        self.assertIn("_.DIMLINEAR", text)
        self.assertIn("DIMTXT", text)

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_dxf_has_every_call(self) -> None:
        stats = plan_render.write_dxf(self.spec, self.plan, self.path("plan.dxf"))
        self.assertEqual(stats["entities"], len(self.plan["calls"]))
        doc = ezdxf.readfile(self.path("plan.dxf"))
        entities = list(doc.modelspace())
        self.assertEqual(len(entities), len(self.plan["calls"]))
        counts = {}
        for entity in entities:
            counts[entity.dxftype()] = counts.get(entity.dxftype(), 0) + 1
        self.assertEqual(counts["ARC"], 5)        # 5 扇门都有开启弧
        self.assertEqual(counts["DIMENSION"], 8)  # 自动尺寸：两向各 2 段 + 2 道总尺寸
        self.assertGreaterEqual(counts["TEXT"], 9)
        layers = {entity.dxf.layer for entity in entities}
        self.assertTrue({"A-WALL", "A-DOOR", "A-WINDOW", "A-GRID", "A-DIM",
                         "A-STAR"}.issubset(layers))
        dimstyle = doc.dimstyles.get("CADSKILL")
        self.assertEqual(dimstyle.dxf.dimtxt, 350.0)      # 3.5mm × 1:100
        self.assertEqual(dimstyle.dxf.dimtsz, 150.0)      # 建筑斜线
        self.assertEqual(dimstyle.dxf.dimtxsty, "TZ_EN")
        style = doc.styles.get("TZ_HZ")
        self.assertEqual(style.dxf.font, "txt.shx")
        self.assertEqual(style.dxf.bigfont, "gbcbig.shx")
        self.assertAlmostEqual(style.dxf.width, 0.7)
        texts = [entity for entity in entities if entity.dxftype() == "TEXT"]
        self.assertTrue(texts)
        self.assertTrue(all(entity.dxf.style == "TZ_HZ" for entity in texts))

    @unittest.skipIf(ezdxf is None, "需要 ezdxf")
    def test_blocks_replace_door_and_window_primitives(self) -> None:
        stats = plan_render.write_dxf(self.spec, self.plan, self.path("blocks.dxf"),
                                      blocks=True)
        self.assertEqual(stats["door_blocks"], 5)
        self.assertEqual(stats["window_blocks"], 5)
        doc = ezdxf.readfile(self.path("blocks.dxf"))
        entities = list(doc.modelspace())
        inserts = [entity for entity in entities if entity.dxftype() == "INSERT"]
        self.assertEqual(len(inserts), 10)
        names = {entity.dxf.name for entity in inserts}
        self.assertIn("CADSKILL_WINDOW", names)
        self.assertTrue(any(name.startswith("CADSKILL_DOOR_") for name in names))
        self.assertFalse(any(entity.dxftype() == "ARC" for entity in entities))
        door_layers = {entity.dxf.layer for entity in entities
                       if entity.dxftype() in ("LINE", "ARC")}
        self.assertNotIn("A-DOOR", door_layers)
        # 10 个门扇/弧 + 15 条窗线被 10 个 INSERT 替换
        self.assertEqual(len(entities), len(self.plan["calls"]) - 25 + 10)

    def test_svg_is_well_formed_and_carries_geometry(self) -> None:
        stats = plan_render.write_svg(self.spec, self.plan, self.path("plan.svg"))
        self.assertGreater(stats["width"], 0)
        tree = ET.parse(self.path("plan.svg"))
        root = tree.getroot()
        namespace = "{http://www.w3.org/2000/svg}"
        self.assertEqual(root.tag, namespace + "svg")
        self.assertGreater(len(root.findall(".//" + namespace + "line")), 40)
        self.assertGreaterEqual(len(root.findall(".//" + namespace + "path")), 5)
        texts = [element.text for element in root.findall(".//" + namespace + "text")]
        self.assertIn("楼梯", texts)
        self.assertIn("12000", texts)  # 总尺寸

    def test_svg_marks_doors_that_need_review(self) -> None:
        frames = wall_plan.opening_frames(self.spec)
        door = next(frame for frame in frames if frame["kind"] == "door")
        wall = next(wall for wall in self.spec["walls"] if wall["id"] == door["wall"])
        opening = wall["openings"][door["index"]]
        opening["needs_review"] = True
        opening["review_reason"] = "测试用：开启弧不明确"
        plan = plan_render.build(self.spec)
        stats = plan_render.write_svg(self.spec, plan, self.path("review.svg"))
        self.assertEqual(stats["review_markers"], 1)
        text = open(self.path("review.svg"), encoding="utf-8").read()
        self.assertIn('class="review"', text)
        self.assertEqual(plan["review"][0]["reason"], "测试用：开启弧不明确")

    def test_cli_writes_all_backends(self) -> None:
        spec_path = self.path("plan.json")
        with open(spec_path, "w", encoding="utf-8") as handle:
            json.dump(self.spec, handle, ensure_ascii=False)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = plan_render.main([
                spec_path,
                "--out-calls", self.path("c.json"),
                "--out-scr", self.path("p.scr"),
                "--out-dxf", self.path("p.dxf"),
                "--out-svg", self.path("p.svg"),
            ])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        for name in ("c.json", "p.scr", "p.dxf", "p.svg"):
            self.assertTrue(os.path.exists(self.path(name)), name)
        self.assertEqual(payload["summary"]["calls"], len(self.plan["calls"]))

    def test_cli_rejects_bad_spec(self) -> None:
        spec_path = self.path("bad.json")
        with open(spec_path, "w", encoding="utf-8") as handle:
            json.dump({"walls": [{"from": [0, 0], "to": [0, 0], "thickness": 240}]}, handle)
        with contextlib.redirect_stderr(io.StringIO()):
            code = plan_render.main([spec_path, "--out-dxf", self.path("bad.dxf")])
        self.assertEqual(code, 2)

    def test_cli_meter_unit(self) -> None:
        spec = {
            "walls": [{"from": [0, 0], "to": [6, 0], "thickness": 0.24,
                       "openings": [{"kind": "door", "width": 0.9, "center": 3}]}],
            "axis": {"x": [0, 6], "y": [0]},
            "dims": {"offset": 1.2},
        }
        spec_path = self.path("meters.json")
        with open(spec_path, "w", encoding="utf-8") as handle:
            json.dump(spec, handle)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = plan_render.main([spec_path, "--unit", "m",
                                     "--out-calls", self.path("m.calls.json"),
                                     "--out-svg", self.path("m.svg")])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["unit"], "mm")
        calls = json.load(open(self.path("m.calls.json"), encoding="utf-8"))
        self.assertEqual(calls["unit"], "mm")
        points = [point for call in calls["calls"] if call["tool"] == "draw_polyline"
                  for point in call["args"]["points"]]
        self.assertEqual(max(point[0] for point in points), 6120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
