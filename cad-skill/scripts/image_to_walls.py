#!/usr/bin/env python3
"""image_to_walls.py — 实心厚墙的平面图 → DXF（墙体 + 其余线work）。

三种图的三种走法，别拿错：
  * 双线线稿（CAD 导出）      → `image_to_calls.py`（配墙、出 plan.json）
  * 实心厚墙（家装平面图常见）→ **本脚本**（按"深色 + 厚度"分出墙，骨架化取中心线并量厚度）
  * logo / 图标 / 实心图形   → `image_to_vector.py`（轮廓描摹出 SVG/DXF）

输出两张图层：
  A-WALL   墙体（默认画成闭合矩形，与 CAD 侧墙身约定一致；也可选中心线/轮廓）
  A-TRACE  其余线work（家具、洁具、门窗开启线、文字笔画）

例：
  python3 scripts/image_to_walls.py plan.png --out-dxf plan.dxf \
      --min-wall-px 8 --walls rectangles --out-plan walls.json --pretty
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Tuple

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None
try:
    from PIL import Image
except ImportError:
    Image = None

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


# ---------------------------------------------------------------- 掩膜

def load_gray(path: str) -> np.ndarray:
    if Image is None:
        raise RuntimeError("需要 Pillow")
    return cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2GRAY)


def auto_ink_threshold(gray: np.ndarray) -> float:
    """Otsu 分层后再取两类均值的中间值（硬边图上 Otsu 会落在类别灰度值上）"""
    cut = float(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
    lower, upper = gray[gray <= cut], gray[gray > cut]
    if lower.size and upper.size:
        cut = (float(lower.mean()) + float(upper.mean())) / 2.0
    return cut


def wall_mask(gray: np.ndarray, threshold: float, min_wall_px: float):
    """墙体掩膜：先按灰度取深色墨迹，再用开运算滤掉比 min_wall_px 更细的线（家具、文字）"""
    ink = (gray < threshold).astype(np.uint8) * 255
    radius = max(1, int(round(min_wall_px / 2)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    walls = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    return walls, ink


# ---------------------------------------------------------------- 骨架化

def thin(binary: np.ndarray, max_iterations: int = 60) -> np.ndarray:
    """Zhang-Suen 细化：把厚墙压成 1px 中心线（纯 numpy，无需 opencv-contrib）"""
    image = (binary > 0).astype(np.uint8)
    for _ in range(max_iterations):
        changed = False
        for step in (0, 1):
            padded = np.pad(image, 1)
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]
            neighbours = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            sequence = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            transitions = sum(((sequence[i] == 0) & (sequence[i + 1] == 1)).astype(np.uint8)
                              for i in range(8))
            keep = (neighbours >= 2) & (neighbours <= 6) & (transitions == 1)
            if step == 0:
                keep &= ((p2 * p4 * p6) == 0) & ((p4 * p6 * p8) == 0)
            else:
                keep &= ((p2 * p4 * p8) == 0) & ((p2 * p6 * p8) == 0)
            keep &= image == 1
            if keep.any():
                image[keep] = 0
                changed = True
        if not changed:
            break
    return image


def skeleton_segments(skeleton: np.ndarray, distance=None, min_length: float = 8.0,
                      join_gap: float = 3.0) -> List[Dict[str, Any]]:
    """把骨架拆成正交线段（按行 / 按列取连续像素段），并顺带量厚度"""
    segments: List[Dict[str, Any]] = []
    height, width = skeleton.shape
    for orient in ("H", "V"):
        outer = height if orient == "H" else width
        inner = width if orient == "H" else height
        lines = skeleton if orient == "H" else skeleton.T
        dist_lines = None if distance is None else (distance if orient == "H" else distance.T)
        for line_index in range(outer):
            row = lines[line_index] > 0
            if not row.any():
                continue
            start = None
            runs: List[List[int]] = []
            for position in range(inner + 1):
                on = position < inner and bool(row[position])
                if on and start is None:
                    start = position
                elif not on and start is not None:
                    if runs and start - runs[-1][1] <= join_gap:
                        runs[-1][1] = position - 1
                    else:
                        runs.append([start, position - 1])
                    start = None
            for begin, end in runs:
                if end - begin + 1 < min_length:
                    continue
                thickness = None
                if dist_lines is not None:
                    values = dist_lines[line_index, begin:end + 1]
                    values = values[values > 0]
                    if values.size:
                        thickness = float(np.median(values)) * 2.0
                segment = {"orient": orient, "offset": float(line_index),
                           "start": float(begin), "end": float(end),
                           "length": float(end - begin + 1), "thickness": thickness}
                segments.append(segment)
    return segments


def reconstruct(segments: List[Dict[str, Any]], shape, use_thickness: bool = True,
                extend: bool = True) -> np.ndarray:
    """按线段（可选按厚度）重建栅格，用于算 IoU：这是"描得全不全"的量化"""
    canvas = np.zeros(shape, np.uint8)
    for segment in segments:
        half = int(round(((segment.get("thickness") or 1) if use_thickness else 1) / 2))
        half = max(0, half)
        # 两端各延伸半墙厚：骨架在 T 型接头会错位，不延伸就会在每个转角留下方块空洞
        start, end = extend_ends(segment, half if extend else 0.0)
        if segment["orient"] == "H":
            y0, y1 = int(segment["offset"]) - half, int(segment["offset"]) + half
            x0, x1 = int(start), int(end)
        else:
            x0, x1 = int(segment["offset"]) - half, int(segment["offset"]) + half
            y0, y1 = int(start), int(end)
        canvas[max(0, y0):y1 + 1, max(0, x0):x1 + 1] = 255
    return canvas


def extend_ends(segment: Dict[str, Any], extend: float) -> Tuple[float, float]:
    """墙段两端各外延 extend（默认半墙厚），补上转角处缺的方块

    本脚本实测过三种策略（同一张 1012×1252 平面图）：
      两端都延伸        覆盖 0.9871  精度 0.9010  ← 采用
      完全不延伸        覆盖 0.9199  精度 0.9062
      只延伸接头端      覆盖 0.9325  精度 0.9058（容差放大到 20px 也只有 0.9612）
    也就是说"多画"的部分其实主要是转角方块，本来就在墙身约定之内（wall_plan 同样延伸）。
    """
    if extend <= 0:
        return segment["start"], segment["end"]
    return segment["start"] - extend, segment["end"] + extend


def merge_thickness(segments: List[Dict[str, Any]], tolerance: float = 0.35
                    ) -> Dict[str, Any]:
    """厚度分布统计（把相近厚度归并成几档，便于判断内外墙）"""
    values = sorted(s["thickness"] for s in segments if s.get("thickness"))
    groups: List[Dict[str, float]] = []
    for value in values:
        if groups and abs(value - groups[-1]["value"]) <= max(1.0, groups[-1]["value"] * tolerance):
            group = groups[-1]
            group["value"] = (group["value"] * group["count"] + value) / (group["count"] + 1)
            group["count"] += 1
        else:
            groups.append({"value": value, "count": 1.0})
    return {"groups": [{"thickness_px": round(g["value"], 1), "segments": int(g["count"])}
                       for g in sorted(groups, key=lambda g: -g["count"])],
            "total_segments": len(values)}


# ---------------------------------------------------------------- 输出

def to_mm(value: float, scale: float, flip: bool, size: int, origin: float) -> float:
    delta = (value - origin) * scale
    return -delta if flip else delta


def write_dxf(path: str, walls: List[Dict[str, Any]], thin: List[Dict[str, Any]],
              scale: float, shape, origin, wall_style: str, wall_layer: str,
              trace_layer: str, centerline_layer: str, extend: bool = True) -> Dict[str, int]:
    import ezdxf
    height, width = shape
    converted: List[Tuple[float, float]] = []

    def point(x: float, y: float) -> Tuple[float, float]:
        return (to_mm(x, scale, False, width, origin[0]),
                to_mm(y, scale, True, height, origin[1]))

    doc = ezdxf.new("R2010")
    model = doc.modelspace()
    counts = {"wall_rectangles": 0, "wall_centerlines": 0, "trace_lines": 0}

    for wall in walls:
        thickness = max(1.0, wall.get("thickness") or 1.0)
        start, end = extend_ends(wall, thickness / 2 if extend else 0.0)
        if wall["orient"] == "H":
            corners = [(start, wall["offset"] - thickness / 2),
                       (end, wall["offset"] - thickness / 2),
                       (end, wall["offset"] + thickness / 2),
                       (start, wall["offset"] + thickness / 2)]
            ends = ((start, wall["offset"]), (end, wall["offset"]))
        else:
            corners = [(wall["offset"] - thickness / 2, start),
                       (wall["offset"] - thickness / 2, end),
                       (wall["offset"] + thickness / 2, end),
                       (wall["offset"] + thickness / 2, start)]
            ends = ((wall["offset"], start), (wall["offset"], end))
        if wall_style in ("rectangles", "both"):
            model.add_lwpolyline([point(*c) for c in corners], close=True,
                                 dxfattribs={"layer": wall_layer})
            counts["wall_rectangles"] += 1
        if wall_style in ("centerlines", "both"):
            model.add_line(point(*ends[0]), point(*ends[1]),
                           dxfattribs={"layer": centerline_layer})
            counts["wall_centerlines"] += 1

    for segment in thin:
        if segment["orient"] == "H":
            first = (segment["start"], segment["offset"])
            second = (segment["end"], segment["offset"])
        else:
            first = (segment["offset"], segment["start"])
            second = (segment["offset"], segment["end"])
        model.add_line(point(*first), point(*second), dxfattribs={"layer": trace_layer})
        counts["trace_lines"] += 1
    doc.saveas(path)
    return counts


def write_plan_json(path: str, walls: List[Dict[str, Any]], scale: float, origin,
                    shape) -> Dict[str, Any]:
    """输出 wall_plan.py 认的 plan.json（只有墙，洞口需要人工补）"""
    height, width = shape

    def xy(x: float, y: float) -> List[float]:
        return [round(to_mm(x, scale, False, width, origin[0]), 1),
                round(to_mm(y, scale, True, height, origin[1]), 1)]

    entries = []
    for index, wall in enumerate(walls, start=1):
        if wall["orient"] == "H":
            start, end = xy(wall["start"], wall["offset"]), xy(wall["end"], wall["offset"])
        else:
            start, end = xy(wall["offset"], wall["start"]), xy(wall["offset"], wall["end"])
        entries.append({"id": f"W{index}-{wall['orient']}", "from": start, "to": end,
                        "thickness": round((wall.get("thickness") or 0) * scale, 1),
                        "openings": []})
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"walls": entries}, handle, ensure_ascii=False, indent=2)
    return {"walls": len(entries)}


# ---------------------------------------------------------------- CLI

def resolve_scale(args) -> float:
    if args.px_per_mm:
        return float(args.px_per_mm)
    if args.calibrate:
        values = [float(v) for v in args.calibrate.replace(" ", ",").split(",") if v.strip()]
        if len(values) != 4 or not args.real:
            raise ValueError('--calibrate 需要 "x1,y1 x2,y2" 且配 --real 毫米')
        pixels = math.hypot(values[2] - values[0], values[3] - values[1])
        if pixels <= 0 or args.real <= 0:
            raise ValueError("标定无效：两点距离与真实距离都必须大于 0")
        return pixels / float(args.real)
    return 1.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="实心厚墙平面图 → DXF（墙体 + 线work）")
    parser.add_argument("image")
    parser.add_argument("--out-dxf")
    parser.add_argument("--out-plan", help="额外输出 wall_plan.py 认的 plan.json（仅墙）")
    parser.add_argument("--wall-threshold", type=float,
                        help="墙体灰度阈值（默认 Otsu 自动，取两类均值中间值）")
    parser.add_argument("--line-threshold", type=float, default=235.0,
                        help="线work灰度阈值（默认 235，含家具/洁具/文字）")
    parser.add_argument("--min-wall-px", type=float, default=8.0,
                        help="最小墙厚（像素，默认 8）：比它细的线（家具、文字）会被开运算滤掉")
    parser.add_argument("--walls", choices=("rectangles", "centerlines", "both"),
                        default="rectangles", help="墙体画法：闭合矩形 / 中心线 / 两者都要")
    parser.add_argument("--min-segment-px", type=float, default=10.0,
                        help="最短线段（像素，默认 10），过滤骨架毛刺")
    parser.add_argument("--join-gap", type=float, default=5.0,
                        help="同一行/列骨架断口的连接容差（像素，默认 5）：T 型接头处骨架会错位")
    parser.add_argument("--no-extend-ends", dest="extend_ends", action="store_false",
                        help="墙段两端不外延半墙厚（默认外延，补转角方块）")
    parser.add_argument("--no-trace", dest="trace", action="store_false",
                        help="不输出家具等线work")
    parser.add_argument("--px-per-mm", type=float, help="像素/毫米（可选，缺省则 1px=1单位）")
    parser.add_argument("--calibrate", help='两个像素点 "x1,y1 x2,y2"')
    parser.add_argument("--real", type=float, help="上述两点的真实距离（毫米）")
    parser.add_argument("--wall-layer", default="A-WALL")
    parser.add_argument("--centerline-layer", default="A-WALL-CL")
    parser.add_argument("--trace-layer", default="A-TRACE")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if cv2 is None:
        print(json.dumps({"ok": False, "error": "需要 opencv-python-headless"}))
        return EXIT_FAILURE
    try:
        scale = resolve_scale(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return EXIT_USAGE
    try:
        gray = load_gray(args.image)
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return EXIT_FAILURE

    threshold = args.wall_threshold or auto_ink_threshold(gray)
    walls_mask, ink_mask = wall_mask(gray, threshold, args.min_wall_px)
    if not walls_mask.any():
        print(json.dumps({"ok": False,
                          "error": "没有识别到墙体：调小 --min-wall-px 或调大 --wall-threshold"},
                         ensure_ascii=False))
        return EXIT_FAILURE

    distance = cv2.distanceTransform((walls_mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    skeleton = thin(walls_mask)
    walls = [s for s in skeleton_segments(skeleton, distance, args.min_segment_px, args.join_gap)
             if (s.get("thickness") or 0) >= args.min_wall_px * 0.8]
    rebuilt = reconstruct(walls, gray.shape, extend=args.extend_ends)
    truth = walls_mask > 0
    intersection = float(((rebuilt > 0) & truth).sum())
    union = float(((rebuilt > 0) | truth).sum())

    thin_segments: List[Dict[str, Any]] = []
    if args.trace:
        # 线work = 浅色细线（家具/洁具/开启线/文字），排除已经算作墙体的部分
        trace_mask = ((gray < args.line_threshold) & ~(walls_mask > 0)).astype(np.uint8) * 255
        thin_segments = skeleton_segments(thin(trace_mask, 30), None,
                                          args.min_segment_px, 3.0)

    height, width = gray.shape
    min_x = min((s["start"] if s["orient"] == "H" else s["offset"]) for s in walls)
    max_y = max((s["offset"] if s["orient"] == "H" else s["end"]) for s in walls)
    origin = (min_x, max_y)

    payload: Dict[str, Any] = {
        "ok": True,
        "image": args.image,
        "image_size": [width, height],
        "wall_threshold": round(threshold, 1),
        "min_wall_px": args.min_wall_px,
        "px_per_unit": scale,
        "wall_segments": len(walls),
        "wall_total_length_px": round(sum(s["length"] for s in walls), 1),
        "wall_total_length_units": round(sum(s["length"] for s in walls) * scale, 1),
        "thickness": merge_thickness(walls),
        "wall_mask_iou": round(intersection / union, 4) if union else 0.0,
        "wall_mask_coverage": round(intersection / float(truth.sum()), 4) if truth.sum() else 0.0,
        "trace_segments": len(thin_segments),
        "ink_pixels": int((ink_mask > 0).sum()),
        "wall_pixels": int((walls_mask > 0).sum()),
    }
    if scale == 1.0:
        payload["scale_note"] = ("未标定：几何单位是像素。给 --px-per-mm 或 "
                                 "--calibrate \"x1,y1 x2,y2\" --real 毫米 才是真实尺寸；"
                                 "未标定时 plan.json 的墙厚（约 19/10px）会被 wall_plan.py 判为异常")
    if args.out_dxf:
        counts = write_dxf(args.out_dxf, walls, thin_segments, scale, gray.shape, origin,
                           args.walls, args.wall_layer, args.trace_layer, args.centerline_layer,
                           extend=args.extend_ends)
        payload.update(counts)
        payload["dxf"] = args.out_dxf
    if args.out_plan:
        payload.update(write_plan_json(args.out_plan, walls, scale, origin, gray.shape))
        payload["plan"] = args.out_plan
        payload["plan_note"] = "plan.json 只含墙中心线与厚度，洞口需要人工补（本脚本不做洞口识别）"

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
