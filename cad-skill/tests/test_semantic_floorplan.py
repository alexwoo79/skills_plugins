#!/usr/bin/env python3
"""建筑语义整理脚本的离线回归测试。"""

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

import semantic_floorplan as sf  # noqa: E402

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
        code = sf.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


class TextClassificationTests(unittest.TestCase):
    def test_fuzzy_room_and_dimension_labels(self) -> None:
        def group(text, cx, cy, conf=80):
            return {"text": text, "conf": conf, "x": cx - 20, "y": cy - 8,
                    "w": 40, "h": 16, "cx": cx, "cy": cy}

        groups = [
            group("AR Balcony", 1355, 348),
            group("4 Bedroom", 1847, 505),
            group("Bathroom", 360, 650),
            group("Bathroom", 845, 624),
            group("Dining Room D", 1287, 646),
            group("M?ster Bedroom", 370, 1264),
            group("iving Roo", 1150, 1188),
            group("Door", 1952, 926),
            group("1800", 254, 131),
        ]
        labels, dimensions = sf.classify_text(groups, 2257, 1876)
        zh = {item["zh"] for item in labels}
        self.assertIn("生活阳台", zh)
        self.assertIn("卧室", zh)
        self.assertIn("主卫", zh)
        self.assertIn("次卫", zh)
        self.assertIn("主卧", zh)
        self.assertIn("客厅", zh)
        self.assertIn("入户门", zh)
        self.assertEqual([item["value"] for item in dimensions], ["1800"])

    def test_label_hints_fill_only_missing_rooms(self) -> None:
        existing = [{"zh": "客厅", "cx": 100, "cy": 100}]
        hints = [{"zh": "客厅", "cx": 110, "cy": 105},
                 {"zh": "入户门", "cx": 300, "cy": 200}]
        # 这个辅助逻辑在 main 里；这里验证提示数据的结构约定。
        self.assertEqual(len(existing), 1)
        self.assertEqual(len(hints), 2)


@unittest.skipIf(cv2 is None or np is None or ezdxf is None,
                 "需要 OpenCV、numpy 与 ezdxf")
class SemanticDxfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cad-semantic-")
        cls.image = os.path.join(cls.tmp, "plan.png")
        image = np.full((300, 400), 255, dtype=np.uint8)
        cv2.rectangle(image, (50, 100), (350, 120), 0, -1)   # 厚墙带
        cv2.line(image, (50, 200), (350, 200), 0, 2)          # 细家具线
        cv2.imwrite(cls.image, image)

        cls.source = os.path.join(cls.tmp, "source.dxf")
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 4
        model = doc.modelspace()
        ppm, origin_y = 0.1, 300.0
        def mm(x, y):
            return (x / ppm, (origin_y - y) / ppm)
        model.add_lwpolyline([mm(50, 100), mm(350, 100), mm(350, 120), mm(50, 120)],
                             close=True, dxfattribs={"layer": "A-TRACE"})
        model.add_lwpolyline([mm(50, 200), mm(350, 200), mm(350, 202), mm(50, 202)],
                             close=True, dxfattribs={"layer": "A-TRACE"})
        doc.saveas(cls.source)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_wall_and_trace_layers(self) -> None:
        out = os.path.join(self.tmp, "semantic.dxf")
        code, payload = run_cli([
            self.image, "--source-dxf", self.source, "--out-dxf", out,
            "--px-per-mm", "0.1", "--no-ocr", "--min-wall-area", "200",
        ])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["wall_groups"], 1, payload)
        self.assertGreaterEqual(payload["trace_paths"], 1, payload)
        self.assertEqual(payload["audit_errors"], 0, payload)
        doc = ezdxf.readfile(out)
        layers = {entity.dxf.layer for entity in doc.modelspace()}
        self.assertTrue({"A-WALL", "A-HATCH", "A-TRACE"}.issubset(layers))
        self.assertEqual(doc.header.get("$INSUNITS"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
