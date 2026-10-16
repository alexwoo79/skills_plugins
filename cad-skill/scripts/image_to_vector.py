#!/usr/bin/env python3
"""image_to_vector.py — 实心图形 / logo / 文字位图 → SVG 或 DXF（轮廓描摹）。

和 `image_to_calls.py` 的分工：
  * `image_to_calls.py` 面向**线稿**（建筑平面）：检测直线、配墙、出 plan.json。
  * 本脚本面向**实心图形**（logo、图标、笔画文字）：取填充区域的外轮廓与内孔，
    简化成多边形，输出可缩放的 SVG 或多边形/填充的 DXF。

对 logo 这类图，用直线检测会得到一堆碎线；轮廓描摹才是对的做法。

例：
  python3 scripts/image_to_vector.py logo.png --out-svg logo.svg --out-dxf logo.dxf \
      --px-per-mm 0.25 --epsilon 0.8 --min-area 20 --pretty
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
    from PIL import Image, ImageDraw
except ImportError:
    Image = None

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


# ---------------------------------------------------------------- 掩膜与轮廓

def load_source(path: str, threshold: int | None, invert: bool) -> Dict[str, Any]:
    """读图并生成"墨迹"掩膜（前景=255）

    同时保留连续通道（灰度或 alpha）与阈值，供超采样时在更高分辨率上重新判定边界：
    直接放大二值掩膜会让轮廓整体外扩（实测 IoU 从 0.98 掉到 0.95），
    放大连续通道再卡同一个阈值才是亚像素边界。
    """
    if cv2 is None or Image is None:
        raise RuntimeError("需要 opencv-python-headless 与 Pillow")
    image = Image.open(path)
    rgba = np.array(image.convert("RGBA"))
    rgb, alpha = rgba[:, :, :3], rgba[:, :, 3]

    if alpha.min() < 250:
        # 有透明通道时透明度就是最可靠的前景定义
        mask = (alpha > 127).astype(np.uint8) * 255
        note = "前景取 alpha>127"
        source, channel, cut = alpha, "alpha", 128
    else:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if threshold is None:
            # logo 图上"少数派"就是前景，比固定阈值稳
            cut = float(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
            # Otsu 在硬边图上会把阈值落在某一类的灰度值上（实测：暗部 20，阈值也是 20），
            # 直接拿来当切分点会让超采样插值出的中间灰度全被判到背景侧（轮廓整体缩小）。
            # 取两类均值的中间值才是真正的分界。
            lower, upper = gray[gray <= cut], gray[gray > cut]
            if lower.size and upper.size:
                cut = (float(lower.mean()) + float(upper.mean())) / 2.0
            # 注意用 <=：Otsu 在双峰图（纯色 logo）上会把阈值落在暗部像素值上，
            # 用 < 会把前景整个丢掉（实测过：阈值 20 而暗部恰好是 20）
            ink = gray <= cut
            if ink.mean() > 0.5:
                ink = ~ink
                channel = "light"
            else:
                channel = "dark"
            mask = ink.astype(np.uint8) * 255
            note = "Otsu 自动阈值，取占比较小的一侧为前景"
        else:
            mask = (gray <= threshold).astype(np.uint8) * 255
            note = f"固定阈值 gray<{threshold}"
            channel, cut = "dark", float(threshold)
        source = gray
    if invert:
        mask = 255 - mask
        channel = "light" if channel == "dark" else "dark"
        note += "（已反色）"

    if mask.max() == 0:
        raise RuntimeError("没有识别到前景：确认 --threshold / --invert")
    foreground = rgb[mask > 0]
    color = tuple(int(v) for v in foreground.mean(axis=0))
    return {"mask": mask, "color": color, "note": note, "source": source,
            "channel": channel, "cut": cut}


def _binarize(source, channel: str, cut: float) -> np.ndarray:
    if channel == "alpha":
        return (source >= cut).astype(np.uint8) * 255
    if channel == "dark":
        return (source <= cut).astype(np.uint8) * 255
    return (source > cut).astype(np.uint8) * 255


def trace_shapes(data: Dict[str, Any], supersample: int, epsilon: float,
                 min_area: float):
    """按超采样倍率描轮廓；返回原图像素坐标下的 (外轮廓, 孔, 丢弃数)"""
    if supersample <= 1:
        outers, holes, dropped = trace_contours(data["mask"], epsilon, min_area)
        return outers, holes, dropped
    big = cv2.resize(data["source"], None, fx=supersample, fy=supersample,
                     interpolation=cv2.INTER_LINEAR)
    binary = _binarize(big, data["channel"], data["cut"])
    outers, holes, dropped = trace_contours(binary, epsilon * supersample,
                                           min_area * supersample * supersample)
    return ([c / supersample for c in outers],
            [[c / supersample for c in group] for group in holes], dropped)


def trace_contours(mask, epsilon: float, min_area: float):
    """外轮廓 + 内孔（RETR_CCOMP 两级层级），做多边形简化并按面积过滤"""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_TC89_KCOS)
    hierarchy = hierarchy[0] if hierarchy is not None else []
    outers: List[np.ndarray] = []
    holes: List[List[np.ndarray]] = []
    dropped = 0
    index_of_outer: Dict[int, int] = {}

    for index, contour in enumerate(contours):
        parent = hierarchy[index][3] if len(hierarchy) else -1
        if cv2.contourArea(contour) < min_area:
            dropped += 1
            continue
        approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(float)
        if len(approx) < 3:
            dropped += 1
            continue
        if parent == -1:
            index_of_outer[index] = len(outers)
            outers.append(approx)
            holes.append([])
        else:
            owner = index_of_outer.get(parent)
            if owner is not None:
                holes[owner].append(approx)
            else:  # 父轮廓因面积过小被丢弃：孔也无意义
                dropped += 1
    return outers, holes, dropped


def rasterize(outers: List[np.ndarray], holes: List[List[np.ndarray]], shape) -> np.ndarray:
    """把多边形按"外轮廓填充、孔挖空"栅格化，用于算保真度（IoU）"""
    canvas = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(canvas)
    for contour in outers:
        draw.polygon([tuple(p) for p in contour], fill=255)
    for group in holes:
        for contour in group:
            draw.polygon([tuple(p) for p in contour], fill=0)
    return np.array(canvas)


def fidelity(mask, outers, holes) -> Dict[str, Any]:
    """与原始掩膜比对：IoU 与面积比（这就是"描得像不像"的量化）"""
    rendered = rasterize(outers, holes, mask.shape) > 0
    truth = mask > 0
    intersection = float((rendered & truth).sum())
    union = float((rendered | truth).sum())
    return {
        "iou": round(intersection / union, 4) if union else 0.0,
        "area_ratio": round(float(rendered.sum()) / float(truth.sum()), 4) if truth.sum() else 0.0,
        "vertices": int(sum(len(c) for c in outers) + sum(len(c) for g in holes for c in g)),
    }


# ---------------------------------------------------------------- 输出

def to_mm(points: np.ndarray, scale: float, height: int, origin: Tuple[float, float]):
    """像素 → 毫米：y 轴翻转（图像向下为正，CAD/SVG 常用坐标向上为正）

    scale 是「每毫米多少像素」（px/mm），所以这里要除——与 `image_to_calls.py`
    的语义保持一致；写成乘法在 scale=1 时看不出，给了比例就会差一个平方级的量纲。
    """
    return [((x - origin[0]) / scale, (origin[1] - y) / scale) for x, y in points]


def write_svg(path: str, outers, holes, scale: float, height: int, width: int,
              origin, color: Tuple[int, int, int]) -> None:
    """写出 even-odd 填充的 SVG：外轮廓与孔在同一个 path 里，孔自动挖空"""
    bbox_w = (max(p[0] for c in outers for p in c) - min(p[0] for c in outers for p in c)) / scale
    bbox_h = (max(p[1] for c in outers for p in c) - min(p[1] for c in outers for p in c)) / scale
    subpaths = []
    for outer, group in zip(outers, holes):
        rings = [outer] + list(group)
        for ring in rings:
            points = to_mm(ring, scale, height, origin)
            subpaths.append("M " + " L ".join("%.3f %.3f" % (x, y) for x, y in points) + " Z")
    fill = "#%02x%02x%02x" % color
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        'width="%.3fmm" height="%.3fmm" viewBox="0 0 %.3f %.3f">\n'
        '  <path fill="%s" fill-rule="evenodd" d="%s"/>\n'
        '</svg>\n' % (bbox_w, bbox_h, bbox_w, bbox_h, fill, " ".join(subpaths))
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg)


def write_dxf(path: str, outers, holes, scale: float, height: int, origin,
              outer_layer: str, hole_layer: str, fill_layer: str, hatch: bool,
              color: Tuple[int, int, int]) -> Dict[str, int]:
    """写出 DXF：外轮廓/孔各成闭合多段线；可选带岛检测的填充（logo 在 CAD 里才是实心的）"""
    import ezdxf
    from ezdxf.lldxf import const

    doc = ezdxf.new("R2010")
    model = doc.modelspace()
    entities = {"polylines": 0, "hatches": 0}

    for outer, group in zip(outers, holes):
        outer_points = to_mm(outer, scale, height, origin)
        model.add_lwpolyline(outer_points, close=True, dxfattribs={"layer": outer_layer})
        entities["polylines"] += 1
        for contour in group:
            model.add_lwpolyline(to_mm(contour, scale, height, origin), close=True,
                                 dxfattribs={"layer": hole_layer})
            entities["polylines"] += 1
        if hatch:
            patch = model.add_hatch(color=256, dxfattribs={"layer": fill_layer})
            patch.set_solid_fill(color=256)
            patch.paths.add_polyline_path(outer_points, is_closed=True,
                                         flags=const.BOUNDARY_PATH_EXTERNAL)
            for contour in group:
                patch.paths.add_polyline_path(to_mm(contour, scale, height, origin),
                                              is_closed=True)
            entities["hatches"] += 1
    doc.saveas(path)
    return entities


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="实心图形/logo → SVG / DXF（轮廓描摹）")
    parser.add_argument("image")
    parser.add_argument("--out-svg")
    parser.add_argument("--out-dxf")
    parser.add_argument("--threshold", type=int, help="固定灰度阈值（默认 Otsu 自动）")
    parser.add_argument("--invert", action="store_true", help="前景/背景反相")
    parser.add_argument("--epsilon", type=float, default=0.8,
                        help="多边形简化容差（像素，默认 0.8；越大越简洁、越走形）")
    parser.add_argument("--min-area", type=float, default=20.0,
                        help="丢弃小于该面积（像素²）的轮廓，默认 20")
    parser.add_argument("--supersample", type=int, default=4,
                        help="超采样倍率（默认 4，1=关闭）：放大连续通道后再卡阈值，得到亚像素边界，"
                             "避免轮廓整体缩小")
    parser.add_argument("--px-per-mm", type=float, default=1.0,
                        help="每毫米多少像素（默认 1，即 1px = 1 单位）。"
                             "想让 400px 的图形出成 100mm 宽，就填 4.0")
    parser.add_argument("--outer-layer", default="A-TRACE")
    parser.add_argument("--hole-layer", default="A-TRACE")
    parser.add_argument("--fill-layer", default="A-HATCH")
    parser.add_argument("--no-hatch", dest="hatch", action="store_false",
                        help="DXF 只出轮廓，不做填充")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_source(args.image, args.threshold, args.invert)
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return EXIT_FAILURE

    mask, color, note = data["mask"], data["color"], data["note"]
    outers, holes, dropped = trace_shapes(data, max(1, args.supersample),
                                          args.epsilon, args.min_area)
    if not outers:
        print(json.dumps({"ok": False, "error": "没有描出任何轮廓（试试 --min-area 调小）"},
                         ensure_ascii=False))
        return EXIT_FAILURE

    height, width = mask.shape
    min_x = min(p[0] for c in outers for p in c)
    max_y = max(p[1] for c in outers for p in c)
    origin = (min_x, max_y)                      # 外接框左下角作为图纸原点
    metrics = fidelity(mask, outers, holes)
    payload = {
        "ok": True,
        "image": args.image,
        "image_size": [width, height],
        "foreground": note,
        "supersample": max(1, args.supersample),
        "fill_color": "#%02x%02x%02x" % color,
        "px_per_mm": args.px_per_mm,
        "bbox_px": [round(max(p[0] for c in outers for p in c) - min(p[0] for c in outers for p in c), 1),
                    round(max(p[1] for c in outers for p in c) - min(p[1] for c in outers for p in c), 1)],
        "outer_contours": len(outers),
        "holes": sum(len(group) for group in holes),
        "dropped_contours": dropped,
        **metrics,
    }
    if not args.out_svg and not args.out_dxf:
        payload["note"] = "未指定输出，仅报告描摹指标（--out-svg / --out-dxf）"
    if args.out_svg:
        write_svg(args.out_svg, outers, holes, args.px_per_mm, height, width, origin, color)
        payload["svg"] = args.out_svg
    if args.out_dxf:
        try:
            payload["dxf"] = args.out_dxf
            payload.update(write_dxf(args.out_dxf, outers, holes, args.px_per_mm, height, origin,
                                     args.outer_layer, args.hole_layer, args.fill_layer,
                                     args.hatch, color))
        except ImportError:
            payload["dxf_error"] = "需要 ezdxf：pip install ezdxf"

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
