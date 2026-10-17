#!/usr/bin/env python3
"""把前后两份 DXF 叠到同一张原图上，做可复核的 A/B 对比。

典型用法：
  python3 scripts/image_to_calls.py plan.png --out-dxf before.dxf
  python3 scripts/image_to_calls.py plan.png --clean-geometry --out-dxf after.dxf
  python3 scripts/dxf_overlay_compare.py plan.png before.dxf after.dxf --out compare.png

未给 --px-per-mm 时，按两份 DXF 的联合包围盒自动适配到图片；有标定时用
--px-per-mm（工作图像的像素/毫米）和 --origin（CAD 原点对应的像素）做精确映射。
红色 = 处理前独有的线，绿色 = 处理后独有的线，黄色 = 两者重合。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:
    import ezdxf
except ImportError:  # pragma: no cover - CLI 会给出安装提示
    ezdxf = None

Point = Tuple[float, float]
Polyline = List[Point]


def _circle_points(center: Point, radius: float) -> Polyline:
    return [(center[0] + radius * math.cos(step * math.pi / 24.0),
             center[1] + radius * math.sin(step * math.pi / 24.0))
            for step in range(49)]


def _arc_points(entity) -> Polyline:
    center = entity.dxf.center
    radius = float(entity.dxf.radius)
    start = float(entity.dxf.start_angle)
    end = float(entity.dxf.end_angle)
    if end < start:
        end += 360.0
    count = max(8, int(abs(end - start) / 5.0) + 1)
    return [(center[0] + radius * math.cos(math.radians(start + (end - start) * i / (count - 1))),
             center[1] + radius * math.sin(math.radians(start + (end - start) * i / (count - 1))))
            for i in range(count)]


def _entity_polylines(entity) -> Iterable[Tuple[Polyline, bool]]:
    kind = entity.dxftype()
    if kind == "LINE":
        start, end = entity.dxf.start, entity.dxf.end
        yield [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))], False
    elif kind == "LWPOLYLINE":
        points = [(float(x), float(y)) for x, y in entity.get_points("xy")]
        if len(points) >= 2:
            yield points, bool(entity.closed)
    elif kind == "POLYLINE":
        points = [(float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                  for vertex in entity.vertices]
        if len(points) >= 2:
            yield points, bool(entity.is_closed)
    elif kind == "ARC":
        yield _arc_points(entity), False
    elif kind == "CIRCLE":
        center = entity.dxf.center
        yield _circle_points((float(center[0]), float(center[1])),
                             float(entity.dxf.radius)), True
    elif kind == "ELLIPSE":
        try:
            points = [(float(point.x), float(point.y))
                      for point in entity.flattening(1.0)]
        except Exception:
            points = []
        if len(points) >= 2:
            yield points, bool(getattr(entity, "closed", False))
    elif kind == "SPLINE":
        try:
            points = [(float(point.x), float(point.y))
                      for point in entity.flattening(1.0)]
        except Exception:
            points = []
        if len(points) >= 2:
            yield points, False
    elif kind == "INSERT":
        for child in entity.virtual_entities():
            yield from _entity_polylines(child)


def _closed(points: Polyline, closed: bool) -> Polyline:
    if closed and len(points) >= 2 and math.dist(points[0], points[-1]) > 1e-6:
        return points + [points[0]]
    return points


def load_dxf(path: str, endpoint_tol: float = 1.0) -> Dict[str, Any]:
    if ezdxf is None:
        raise RuntimeError("需要 ezdxf：pip install ezdxf")
    if not os.path.exists(path):
        raise RuntimeError("读不到 DXF: %s" % path)
    doc = ezdxf.readfile(path)
    polylines: List[Polyline] = []
    types: Dict[str, int] = {}
    text_count = 0
    for entity in doc.modelspace():
        kind = entity.dxftype()
        types[kind] = types.get(kind, 0) + 1
        if kind in ("TEXT", "MTEXT"):
            text_count += 1
            continue
        for points, closed in _entity_polylines(entity):
            if len(points) >= 2:
                polylines.append(_closed(points, closed))

    length = 0.0
    endpoints: List[Point] = []
    for points in polylines:
        length += sum(math.dist(first, second)
                      for first, second in zip(points, points[1:]))
        if math.dist(points[0], points[-1]) > 1e-6:
            endpoints.extend((points[0], points[-1]))

    dangling = 0
    if endpoints:
        cell = max(endpoint_tol, 1e-6)
        buckets: Dict[Tuple[int, int], List[int]] = {}
        for index, point in enumerate(endpoints):
            key = (int(math.floor(point[0] / cell)), int(math.floor(point[1] / cell)))
            buckets.setdefault(key, []).append(index)
        for index, point in enumerate(endpoints):
            key = (int(math.floor(point[0] / cell)), int(math.floor(point[1] / cell)))
            found = False
            for x in range(key[0] - 1, key[0] + 2):
                for y in range(key[1] - 1, key[1] + 2):
                    for other in buckets.get((x, y), ()):
                        if other != index and math.dist(point, endpoints[other]) <= endpoint_tol:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            dangling += int(not found)

    return {
        "path": os.path.abspath(path),
        "entity_count": sum(types.values()),
        "text_count": text_count,
        "polyline_count": len(polylines),
        "polyline_length": round(length, 3),
        "open_endpoints": len(endpoints),
        "dangling_endpoints": dangling,
        "types": types,
        "polylines": polylines,
    }


def _bounds(polylines: Sequence[Polyline]) -> Tuple[float, float, float, float]:
    xs = [point[0] for polyline in polylines for point in polyline]
    ys = [point[1] for polyline in polylines for point in polyline]
    if not xs:
        raise RuntimeError("DXF 里没有可绘制的几何")
    return min(xs), min(ys), max(xs), max(ys)


def _transform(before: dict, after: dict, width: int, height: int,
               px_per_mm: Optional[float], origin: Optional[Point]) -> Dict[str, Any]:
    if px_per_mm:
        ox, oy = origin if origin is not None else (0.0, float(height))
        return {
            "mode": "calibrated",
            "sx": float(px_per_mm), "sy": float(px_per_mm),
            "tx": lambda x: float(x) * px_per_mm + ox,
            "ty": lambda y: oy - float(y) * px_per_mm,
            "info": {"mode": "calibrated", "px_per_mm": px_per_mm,
                     "origin": [ox, oy]},
        }
    min_x, min_y, max_x, max_y = _bounds(before["polylines"] + after["polylines"])
    margin = max(4.0, min(width, height) * 0.02)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    left = (width - span_x * scale) / 2.0
    bottom = (height - span_y * scale) / 2.0
    return {
        "mode": "fit",
        "sx": scale, "sy": scale,
        "tx": lambda x: left + (float(x) - min_x) * scale,
        "ty": lambda y: height - bottom - (float(y) - min_y) * scale,
        "info": {"mode": "fit", "bounds": [min_x, min_y, max_x, max_y],
                 "scale": round(scale, 6)},
    }


def _draw_mask(shape: Tuple[int, int], polylines: Sequence[Polyline],
               transform: dict, thickness: int) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    for points in polylines:
        array = np.array([[transform["tx"](x), transform["ty"](y)] for x, y in points],
                         dtype=np.float32)
        array = np.round(array).astype(np.int32)
        cv2.polylines(mask, [array], False, 255, thickness, cv2.LINE_AA)
    return mask


def compare(image_path: str, before_path: str, after_path: str, out_path: str,
            px_per_mm: Optional[float] = None, origin: Optional[Point] = None,
            max_side: int = 0, endpoint_tol: float = 1.0,
            thickness: int = 2) -> Dict[str, Any]:
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("读不到图像: %s" % image_path)
    if max_side and max(image.shape[:2]) > max_side:
        scale = max_side / float(max(image.shape[:2]))
        image = cv2.resize(
            image, (max(1, int(round(image.shape[1] * scale))),
                    max(1, int(round(image.shape[0] * scale)))),
            interpolation=cv2.INTER_AREA)
    height, width = image.shape[:2]
    before = load_dxf(before_path, endpoint_tol)
    after = load_dxf(after_path, endpoint_tol)
    transform = _transform(before, after, width, height, px_per_mm, origin)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    canvas = cv2.addWeighted(canvas, 0.55, np.full_like(canvas, 255), 0.45, 0)
    before_mask = _draw_mask((height, width), before["polylines"], transform, thickness)
    after_mask = _draw_mask((height, width), after["polylines"], transform, thickness)
    red = before_mask > 0
    green = after_mask > 0
    canvas[red] = (0, 0, 230)
    canvas[green] = (0, 190, 0)
    canvas[red & green] = (0, 230, 230)

    legend = np.zeros((74, 300, 3), dtype=np.uint8)
    legend[:] = (32, 32, 32)
    cv2.line(legend, (10, 18), (40, 18), (0, 0, 230), thickness + 1)
    cv2.putText(legend, "BEFORE", (48, 24), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.line(legend, (10, 40), (40, 40), (0, 190, 0), thickness + 1)
    cv2.putText(legend, "AFTER", (48, 46), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.line(legend, (10, 62), (40, 62), (0, 230, 230), thickness + 1)
    cv2.putText(legend, "OVERLAP", (48, 68), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, (235, 235, 235), 1, cv2.LINE_AA)
    visible = legend[:min(legend.shape[0], height), :min(legend.shape[1], width)]
    canvas[:visible.shape[0], :visible.shape[1]] = visible

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    if not cv2.imwrite(out_path, canvas):
        raise RuntimeError("写不了叠加图: %s" % out_path)
    result = {
        "ok": True,
        "image": os.path.abspath(image_path),
        "overlay": os.path.abspath(out_path),
        "image_size": [width, height],
        "transform": transform["info"],
        "before": {key: value for key, value in before.items()
                   if key not in ("polylines", "path")},
        "after": {key: value for key, value in after.items()
                  if key not in ("polylines", "path")},
    }
    result["entity_delta"] = result["after"]["entity_count"] - result["before"]["entity_count"]
    result["length_delta"] = round(
        result["after"]["polyline_length"] - result["before"]["polyline_length"], 3)
    result["dangling_delta"] = (
        result["after"]["dangling_endpoints"] - result["before"]["dangling_endpoints"])
    return result


def parse_origin(text: str) -> Point:
    values = [float(part) for part in text.replace(";", " ").replace(",", " ").split()]
    if len(values) != 2:
        raise argparse.ArgumentTypeError('需要 "x,y"')
    return values[0], values[1]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 before/after 两份 DXF 叠到同一张原图上")
    parser.add_argument("image")
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--out", required=True, help="叠加图 PNG")
    parser.add_argument("--px-per-mm", type=float,
                        help="工作图像的像素/毫米；不给了自动适配包围盒")
    parser.add_argument("--origin", type=parse_origin,
                        help='CAD (0,0) 对应的像素 "x,y"；默认左下角')
    parser.add_argument("--max-side", type=int, default=0,
                        help="叠加前把原图长边缩到该值（与描图工作画布一致）")
    parser.add_argument("--endpoint-tol", type=float, default=1.0,
                        help="统计断头端点的容差（DXF 单位，默认 1）")
    parser.add_argument("--thickness", type=int, default=2,
                        help="叠加线宽（像素，默认 2）")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = compare(args.image, args.before, args.after, args.out,
                         px_per_mm=args.px_per_mm, origin=args.origin,
                         max_side=args.max_side, endpoint_tol=args.endpoint_tol,
                         thickness=args.thickness)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False,
                     indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
