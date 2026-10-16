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
              labels: bool = True, blob: bool = False, coloured: bool = False,
              hatch: bool = False) -> str:
    """600×520 的小平面：一圈 18px 厚墙 + 一道隔墙 + 3px 家具线 + 6px 房间名"""
    if coloured:
        image = Image.new("RGB", (600, 520), (255, 255, 255))
        floor = ImageDraw.Draw(image)
        floor.rectangle((112, 72, 288, 448), fill=(250, 222, 140))   # 木地板：有饱和度
        floor.rectangle((312, 72, 488, 448), fill=(232, 240, 200))
    else:
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
    if blob:
        # 深色实心家具（沙发/床垫那种色块）：两个方向都远大于墙厚，应被块状过滤剔除
        draw.rectangle((150, 330, 250, 430), fill=(40, 40, 40))
    if labels:
        # 房间名那种小笔画：够深但很细，必须被厚度过滤掉
        for x in range(200, 260, 12):
            draw.rectangle((x, 250, x + 4, 250 + TEXT_PX), fill=(WALL_INK,) * 3)
    if hatch:
        # 地板/瓷砖那种密集排线：短线（约 667mm）+ 密间距（20mm），应被清理掉
        colour = (LINE_INK,) * 3
        for index in range(40):
            y = 80 + 3 * index
            draw.line([(120, y), (220, y)], fill=colour, width=1)
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
        code, payload = run_cli([self.plan, "--out-dxf", dxf_path, "--no-trace", "--no-rooms"])
        self.assertEqual(code, 0, payload)
        doc = ezdxf.readfile(dxf_path)
        layers = {entity.dxf.layer for entity in doc.modelspace()}
        self.assertEqual(layers, {"A-WALL"})
        self.assertEqual(payload["trace_segments"], 0)

    def test_calibration_sets_units(self) -> None:
        plan_path = os.path.join(self.tmp, "walls.json")
        # 0.15 px/mm：18px 的墙 = 120mm（真实内隔墙量级），用有物理意义的数字断言，
        # 这样"把除法写成乘法"这种单位错误会被抓住（曾真的漏过一次）
        code, payload = run_cli([self.plan, "--px-per-mm", "0.15", "--out-plan", plan_path])
        self.assertEqual(code, 0, payload)
        self.assertNotIn("scale_note", payload)
        self.assertAlmostEqual(payload["thickness"]["groups"][0]["thickness_px"], WALL_PX, delta=2)
        self.assertAlmostEqual(payload["thickness"]["groups"][0]["thickness_px"] / 0.15,
                               120.0, delta=15.0)
        self.assertAlmostEqual(payload["wall_total_length_mm"],
                               payload["wall_total_length_px"] / 0.15, delta=1.0)
        with open(plan_path, encoding="utf-8") as handle:
            spec = json.load(handle)
        self.assertEqual(len(spec["walls"]), payload["wall_segments"])
        for wall in spec["walls"]:
            self.assertAlmostEqual(wall["thickness"], WALL_PX / 0.15, delta=20.0)

    def test_uncalibrated_run_warns_about_scale(self) -> None:
        code, payload = run_cli([self.plan])
        self.assertIn("未标定", payload["scale_note"])

    def test_extension_improves_coverage(self) -> None:
        """端部延伸半墙厚（CAD 墙身约定）能补上转角方块，覆盖率明显更高。"""
        _, extended = run_cli([self.plan])
        _, plain = run_cli([self.plan, "--no-extend-ends"])
        self.assertGreater(extended["wall_mask_coverage"], plain["wall_mask_coverage"])

    def test_max_wall_px_trades_coverage(self) -> None:
        """T 型交汇处会出个别虚厚段；过滤它们会掉覆盖率（实测 0.968 → 0.86），
        所以默认不过滤，只在 DXF 里真出现虚厚块时才用。"""
        _, plain = run_cli([self.plan])
        _, limited = run_cli([self.plan, "--max-wall-px", "24"])
        self.assertLessEqual(limited["wall_segments"], plain["wall_segments"])
        self.assertLessEqual(limited["wall_mask_coverage"], plain["wall_mask_coverage"])
        self.assertGreaterEqual(limited["dropped_thick_segments"], 0)

    def test_thickness_classes_report_snapped_and_unsnapped(self) -> None:
        code, payload = run_cli([self.plan, "--px-per-mm", "0.15",
                                 "--snap-thickness", "120,240"])
        self.assertEqual(code, 0, payload)
        self.assertIn("thickness_raw", payload)
        classes = {c["thickness_mm"]: c["segments"] for c in payload["thickness_classes"]}
        self.assertIn(120.0, classes)
        self.assertIn(None, classes)          # 没吸附上的也要如实报出来
        self.assertGreater(classes[120.0], 0)  # 18px / 0.15 = 120mm

    def test_rooms_are_detected(self) -> None:
        """隔墙把平面分成两个房间，面积应接近 (400-18)/0.15 × (400-18)/0.15"""
        code, payload = run_cli([self.plan, "--px-per-mm", "0.15"])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["rooms"], 2, payload["room_sizes_m2"])
        for area in payload["room_sizes_m2"]:
            self.assertGreater(area, 2.0)
            self.assertLess(area, 5.0)

    def test_rooms_can_be_disabled_and_layered(self) -> None:
        dxf_path = os.path.join(self.tmp, "rooms.dxf")
        code, payload = run_cli([self.plan, "--out-dxf", dxf_path, "--px-per-mm", "0.15"])
        self.assertEqual(code, 0, payload)
        if ezdxf is not None:
            doc = ezdxf.readfile(dxf_path)
            layers = {}
            for entity in doc.modelspace():
                layers[entity.dxf.layer] = layers.get(entity.dxf.layer, 0) + 1
            self.assertEqual(layers.get("A-ROOM"), payload["rooms"])

        code, payload = run_cli([self.plan, "--no-rooms"])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["rooms"], 0)

    def test_dark_furniture_block_is_not_a_wall(self) -> None:
        """深色实心家具块不能被当成墙（墙面是细长的，色块不是）"""
        path = draw_plan(os.path.join(self.tmp, "with_blob.png"), blob=True)
        code, payload = run_cli([path, "--px-per-mm", "0.15"])
        self.assertEqual(code, 0, payload)
        self.assertGreaterEqual(payload["dropped_blob_components"], 1)
        for group in payload["thickness"]["groups"]:
            # 家具块 100×100px，若误判成墙会出现远大于墙厚的"墙段"
            self.assertLess(group["thickness_px"], 5 * WALL_PX, group)

    def test_coloured_floor_is_not_wall(self) -> None:
        """彩色地板有饱和度，不属于墨线：加了饱和过滤后墙体结果应基本不变"""
        plain = draw_plan(os.path.join(self.tmp, "plain.png"))
        coloured = draw_plan(os.path.join(self.tmp, "coloured.png"), coloured=True)
        _, base = run_cli([plain, "--px-per-mm", "0.15"])
        _, tinted = run_cli([coloured, "--px-per-mm", "0.15"])
        self.assertEqual(tinted["wall_segments"], base["wall_segments"])
        self.assertGreaterEqual(tinted["wall_mask_coverage"], 0.9)

    def test_trace_cleaning_reduces_clutter(self) -> None:
        """清理后线work 条数应明显下降，而墙/房间不受影响"""
        path = draw_plan(os.path.join(self.tmp, "hatched.png"), hatch=True)
        _, raw = run_cli([path, "--px-per-mm", "0.15", "--no-trace-clean"])
        _, cleaned = run_cli([path, "--px-per-mm", "0.15"])
        self.assertLess(cleaned["trace_segments"], raw["trace_segments"])
        self.assertGreater(cleaned["trace_removed_short"] + cleaned["trace_removed_dense"], 0)
        self.assertEqual(cleaned["trace_clean_basis"], "mm")
        self.assertEqual(cleaned["wall_segments"], raw["wall_segments"])
        self.assertEqual(cleaned["rooms"], raw["rooms"])


class TraceCleaningTests(unittest.TestCase):
    """clean_trace 的单元测试：长而稀疏的家具轮廓要留住，短笔画与密排纹理要删掉"""

    def _segment(self, orient, offset, start, end):
        return {"orient": orient, "offset": offset, "start": start, "end": end,
                "length": end - start, "thickness": None}

    def test_unit_removal_rules(self) -> None:
        scale = 0.15                      # 0.15 px/mm → 1mm 约 0.15px
        mm = lambda value: value * scale  # 毫米 → 像素
        furniture = [
            self._segment("H", mm(1000), mm(1000), mm(2000)),
            self._segment("H", mm(2000), mm(1000), mm(2000)),
            self._segment("V", mm(1000), mm(1000), mm(2000)),
            self._segment("V", mm(2000), mm(1000), mm(2000)),
        ]                                  # 家具：1000mm 边长、彼此相距 1000mm
        # 密排纹理：间距 20mm（真实地板/瓷砖纹理就是这种间距量级）
        texture = [self._segment("H", mm(3000 + 20 * i), mm(3000), mm(3400)) for i in range(30)]
        text = [self._segment("H", mm(5000), mm(5000 + 120 * i), mm(5000 + 120 * i + 100))
                for i in range(10)]        # 文字笔画：100mm 长

        kept, short_removed, dense_removed = iw.clean_trace(furniture + texture + text, scale)
        self.assertEqual(short_removed, len(text))
        self.assertGreaterEqual(dense_removed, len(texture) - 12)   # 纹理块边缘线邻居偏少
        for segment in furniture:                                   # 家具长边必须全部保留
            self.assertIn(segment, kept)
        # 纹理块最外侧几条邻居不足会留下，这是可接受的：规则只承诺清掉密集区
        self.assertLessEqual(len(kept) - len(furniture), 12)

    def test_disable_cleaning_keeps_everything(self) -> None:
        segments = [self._segment("H", 10, 0, 5), self._segment("H", 20, 0, 5)]
        kept, short_removed, dense_removed = iw.clean_trace(segments, 1.0, min_mm=0,
                                                           dense_len_mm=0)
        self.assertEqual(len(kept), 2)
        self.assertEqual((short_removed, dense_removed), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
