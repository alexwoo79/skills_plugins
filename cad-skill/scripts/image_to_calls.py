#!/usr/bin/env python3
"""image_to_calls.py — 线稿栅格图 → 结构化墙线 / 图元调用序列 / DXF。

定位：**描图助手，不是"照片变施工图"**。它做的是栅格→矢量（阈值、去噪、直线检测、
共线合并、正交与网格吸附、平行线对合并成墙），语义仍要人判断：识别出的"门/窗"
只是按洞口宽度的猜测，必须看预览图确认。

两种模式：
  --mode walls  线稿 → plan.json（墙中心线 + 厚度 + 洞口），后续直接喂给
                wall_plan.py / plan_to_scr.py / cad_direct.py —— 推荐
  --mode lines  线稿 → 合并吸附后的线段（可选写 DXF，用 ezdxf，不需要装 CAD）

必须先标定，否则图像没有单位：
  --px-per-mm 0.5                         直接给像素/毫米
  --calibrate "x1,y1 x2,y2" --real 3600   指两个点 + 真实距离（毫米）

例：
  python3 image_to_calls.py scan.png --mode walls --calibrate "120,80 1440,80" --real 12000 \
      --out-plan plan.json --out-calls calls.json --preview check.png
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import cv2
except ImportError:  # 纯函数（合并/配对/分组）仍可测试
    cv2 = None

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


# ---------------------------------------------------------------- 基础工具

def _seg(x1: float, y1: float, x2: float, y2: float) -> Dict[str, float]:
    """统一成"沿轴区间 + 偏移"的表示：H 用 y 作偏移、x 作区间，V 反之。"""
    length = math.hypot(x2 - x1, y2 - y1)
    dx, dy = x2 - x1, y2 - y1
    if abs(dy) <= abs(dx):
        return {"orient": "H", "offset": (y1 + y2) / 2, "start": min(x1, x2),
                "end": max(x1, x2), "length": length, "len_x": abs(dx), "len_y": abs(dy)}
    return {"orient": "V", "offset": (x1 + x2) / 2, "start": min(y1, y2),
            "end": max(y1, y2), "length": length, "len_x": abs(dx), "len_y": abs(dy)}


def classify(segment: Dict[str, float], angle_tol_deg: float) -> str:
    """按与轴线的夹角判断 H / V / D（超出容差的斜线）"""
    if segment["length"] <= 0:
        return "D"
    ratio = segment["len_y"] / segment["length"]
    tol = math.sin(math.radians(angle_tol_deg))
    if ratio <= tol:
        return "H"
    if ratio >= math.cos(math.radians(angle_tol_deg)):
        return "V"
    return "D"


def merge_collinear(segments: List[Dict[str, float]], tol: float,
                    join_gap: float) -> List[Dict[str, float]]:
    """把同一轴向、偏移接近、首尾相接的线段合并成一条长线。

    join_gap 以内的小间断会被接上（默认远小于最小洞口宽，避免把门窗洞接掉）。
    合并后的偏移按长度加权，减少粗细/抗锯齿带来的偏差。
    """
    merged: List[Dict[str, float]] = []
    for orient in ("H", "V"):
        group = sorted([s for s in segments if s["orient"] == orient],
                       key=lambda s: (s["offset"], s["start"]))
        cluster: List[Dict[str, float]] = []
        for seg in group + [None]:  # 末尾哨兵，收尾同簇
            if cluster and (seg is None or abs(seg["offset"] - cluster[-1]["offset"]) > tol):
                merged.extend(_merge_cluster(cluster, join_gap))
                cluster = []
            if seg is not None:
                cluster.append(seg)
    merged.extend(s for s in segments if s["orient"] == "D")
    return merged


def _merge_cluster(cluster: List[Dict[str, float]], join_gap: float) -> List[Dict[str, float]]:
    cluster = sorted(cluster, key=lambda s: s["start"])
    result: List[Dict[str, float]] = []
    cur = dict(cluster[0])
    weight = cur["length"]
    for seg in cluster[1:]:
        if seg["start"] <= cur["end"] + join_gap:
            total = weight + seg["length"]
            cur["offset"] = (cur["offset"] * weight + seg["offset"] * seg["length"]) / total
            weight = total
            cur["end"] = max(cur["end"], seg["end"])
            cur["length"] = cur["end"] - cur["start"]
        else:
            result.append(cur)
            cur = dict(seg)
            weight = cur["length"]
    result.append(cur)
    return result


def snap_to_grid(value: float, grid: float, max_shift: float) -> float:
    """吸附到网格，但位移不得超过 max_shift（避免把真实位置拉跑）"""
    if grid <= 0:
        return value
    snapped = round(value / grid) * grid
    return snapped if abs(snapped - value) <= max_shift else value


def pair_walls(segments: List[Dict[str, float]], min_thickness: float, max_thickness: float,
               min_overlap: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, float]]]:
    """把平行且间距合理的成对线识别成墙（中心线 + 厚度），返回 (墙段, 未配对线)

    选配对时优先取重叠长度最大的组合、其次取间距更大的组合：窗的三线图里中间那条
    窗线跟墙面的距离只有半墙厚，但它只在洞口范围内存在（重叠短），因此会输给
    真正跨整段墙的两条墙面线。
    """
    used = set()
    walls: List[Dict[str, Any]] = []
    for orient in ("H", "V"):
        group = [s for s in segments if s["orient"] == orient]
        for i, first in enumerate(group):
            if id(first) in used:
                continue
            best = None
            for second in group[i + 1:]:
                if id(second) in used:
                    continue
                thickness = abs(second["offset"] - first["offset"])
                if not (min_thickness <= thickness <= max_thickness):
                    continue
                start = max(first["start"], second["start"])
                end = min(first["end"], second["end"])
                if end - start <= 0:
                    continue
                overlap = (end - start) / min(first["length"], second["length"])
                if overlap < min_overlap:
                    continue
                score = ((end - start), thickness)
                if best is None or score > best[0]:
                    best = (score, second, end - start, thickness)
            if best is not None:
                _, second, run, thickness = best
                used.add(id(first))
                used.add(id(second))
                start = max(first["start"], second["start"])
                end = min(first["end"], second["end"])
                walls.append({
                    "orient": orient,
                    "center": (first["offset"] + second["offset"]) / 2,
                    "start": start,
                    "end": end,
                    "thickness": thickness,
                })
    leftovers = [s for s in segments if s["orient"] != "D" and id(s) not in used]
    leftovers += [s for s in segments if s["orient"] == "D"]
    return walls, leftovers


def detect_jamb_openings(walls: List[Dict[str, Any]], segments: List[Dict[str, float]],
                         parallel_segments: List[Dict[str, float]],
                         min_width: float, max_width: float) -> int:
    """用"洞口两侧的封边线"定位洞口（比墙身缺口更稳）

    门窗图面上一定有垂直于墙、跨过墙厚的那两条封边线（jamb）。窗的三线图会把墙身
    缺口填平，只看缺口会漏掉窗；封边线则与画法无关。宽度直接取两封边线间距，
    不受线宽影响，比"缺口宽度"更准。返回判定为来自封边线的洞口数。

    判据是"两条封边线之间确实是洞口"，否则会把任意两条垂直线配成洞口（墙段端头、
    甚至分属两个窗的两条封边线，实测过检了一倍）。两条封边线必须各有出处：
      1) 门洞：左边正好是某段墙身的终点、右边正好是另一段的起点（墙被真正切断），或
      2) 窗洞：有一条窗线正好从左边跨到右边（三线图把墙身缺口填平了，
         只有窗线能证明这里是洞口）
    """
    from_jamb = 0
    for wall in walls:
        perpendicular = "V" if wall["orient"] == "H" else "H"
        thickness = wall["thickness"]
        tol = 0.5 * thickness

        def run_touches(position: float, at_start: bool) -> bool:
            """该位置是否正好是某段墙身的起点/终点"""
            for start, end in wall["runs"]:
                if abs((start if at_start else end) - position) <= tol:
                    return True
            return False

        def infill_spans(left: float, right: float) -> bool:
            """是否有窗线正好从 left 跨到 right"""
            for seg in parallel_segments:
                if seg["orient"] != wall["orient"]:
                    continue
                if abs(seg["offset"] - wall["center"]) > 0.35 * thickness:
                    continue
                if abs(seg["start"] - left) <= tol and abs(seg["end"] - right) <= tol:
                    return True
            return False

        jambs = []
        for seg in segments:
            if seg["orient"] != perpendicular:
                continue
            if not (0.5 * thickness <= seg["length"] <= 2.0 * thickness):
                continue
            # 注意：垂直线的 offset 是它自己的轴向坐标（H 墙时是 x），
            # 只能拿它跟墙的区间比；跟墙中心线比是量纲错误（曾因此漏掉全部洞口）
            if seg["start"] > wall["center"] + 0.3 * thickness or seg["end"] < wall["center"] - 0.3 * thickness:
                continue
            if not (wall["start"] - thickness <= seg["offset"] <= wall["end"] + thickness):
                continue
            jambs.append(seg["offset"])
        jambs.sort()
        merged: List[float] = []
        for value in jambs:
            if not merged or value - merged[-1] > 0.5 * thickness:
                merged.append(value)
            else:
                merged[-1] = (merged[-1] + value) / 2

        found: List[Dict[str, Any]] = []
        for left, right in zip(merged, merged[1:]):
            width = right - left
            if not (min_width <= width <= max_width):
                continue
            if not ((run_touches(left, False) and run_touches(right, True))
                    or infill_spans(left, right)):
                continue  # 两条线都没有"洞口出处"，不是同一个洞口
            found.append({"kind": None, "width": width, "center": (left + right) / 2,
                          "evidence": "jamb"})
        # 封边线结果覆盖同位置的缺口结果（缺口宽度受线宽影响，偏小）
        remaining = [op for op in wall["openings"]
                     if not any(abs(op["center"] - new["center"]) <= 0.5 * thickness
                                for new in found)]
        if found:
            from_jamb += len(found)
            wall["openings"] = sorted(found + remaining, key=lambda op: op["center"])
    return from_jamb


def group_wall_runs(walls: List[Dict[str, Any]], offset_tol: float, join_gap: float,
                    min_opening: float, max_opening: float, door_max: float
                    ) -> List[Dict[str, Any]]:
    """把同一条墙线上的多段合并成一条墙，并把段间空隙识别成洞口。

    洞口类型只是按宽度猜测：≤ door_max 记门，其余记窗，都要人工确认。
    """
    grouped: List[Dict[str, Any]] = []
    for orient in ("H", "V"):
        same = sorted([w for w in walls if w["orient"] == orient], key=lambda w: (w["center"], w["start"]))
        cluster: List[Dict[str, Any]] = []
        for wall in same + [None]:
            if cluster and (wall is None or abs(wall["center"] - cluster[-1]["center"]) > offset_tol):
                grouped.append(_group_cluster(cluster, join_gap, min_opening, max_opening, door_max))
                cluster = []
            if wall is not None:
                cluster.append(wall)
    return grouped


def _group_cluster(cluster: List[Dict[str, Any]], join_gap: float, min_opening: float,
                   max_opening: float, door_max: float) -> Dict[str, Any]:
    cluster = sorted(cluster, key=lambda w: w["start"])
    runs: List[List[float]] = []
    for wall in cluster:
        if runs and wall["start"] <= runs[-1][1] + join_gap:
            runs[-1][1] = max(runs[-1][1], wall["end"])
        else:
            runs.append([wall["start"], wall["end"]])
    thickness = float(np.median([w["thickness"] for w in cluster]))
    center = float(np.median([w["center"] for w in cluster]))
    openings = []
    for (_, end), (next_start, _) in zip(runs, runs[1:]):
        width = next_start - end
        if min_opening <= width <= max_opening:
            kind = "door" if width <= door_max else "window"
            openings.append({"kind": kind, "width": round(width, 1),
                             "center": round((end + next_start) / 2, 1),
                             "evidence": "width"})
    return {"orient": cluster[0]["orient"], "center": center, "thickness": thickness,
            "start": runs[0][0], "end": runs[-1][1], "runs": runs, "openings": openings}


def apply_leaf_evidence(walls: List[Dict[str, Any]], segments: List[Dict[str, float]],
                        leaf_tol: float, min_ratio: float) -> int:
    """用"门扇线"证据修正洞口类型，返回被改判成门的数量。

    宽度分不清门和窗（600mm 可能是卫生间窗，也可能是窄门），但门的图面上一定有一条
    垂直于墙、从洞口边缘伸进房间的门扇线，长度约等于门宽。找到这条线就判门；
    找不到则保留按宽度猜的结果（evidence=width），提醒人工确认。
    """
    changed = 0
    for wall in walls:
        perpendicular = "V" if wall["orient"] == "H" else "H"
        candidates = [s for s in segments if s["orient"] == perpendicular]
        for opening in wall["openings"]:
            width = opening["width"]
            hits = []
            for seg in candidates:
                if seg["length"] < width * min_ratio:
                    continue
                for edge in (opening["center"] - width / 2, opening["center"] + width / 2):
                    if abs(seg["offset"] - edge) > leaf_tol:
                        continue
                    # 门扇线应从墙中心线向房间内延伸
                    extends = max(abs(seg["start"] - wall["center"]), abs(seg["end"] - wall["center"]))
                    if extends >= width * min_ratio:
                        hits.append(seg)
                        break
            if hits:
                if opening["kind"] != "door":
                    changed += 1
                opening["kind"] = "door"
                opening["evidence"] = "leaf"
    return changed


# ---------------------------------------------------------------- 图像管线

def load_binary(path: str, adaptive: bool, deskew: bool, despeckle: bool):
    """读图 → 二值化（黑线为白）→ 可选纠偏 → 返回 (binary, 仿射矩阵)"""
    if cv2 is None:
        raise RuntimeError("需要 opencv：pip install opencv-python-headless")
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"读不到图像: {path}")
    if adaptive:
        binary = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 31, 10)
    else:
        _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if despeckle:
        binary = cv2.medianBlur(binary, 3)
    matrix = np.eye(2, 3, dtype=np.float32)
    if deskew:
        angle = estimate_skew(binary)
        if abs(angle) > 0.05:
            height, width = binary.shape[:2]
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
            binary = cv2.warpAffine(binary, matrix, (width, height), flags=cv2.INTER_NEAREST,
                                    borderValue=0)
    return binary, matrix


def estimate_skew(binary) -> float:
    """用霍夫直线估计整体倾斜角（度），正数表示需要顺时针回正"""
    lines = cv2.HoughLines(binary, 1, np.pi / 720, threshold=100)
    if lines is None:
        return 0.0
    deviations = []
    # OpenCV 4.x 返回 (N,1,2)，5.x 返回 (N,2)：统一压成 (N,2)
    for _, theta in np.asarray(lines).reshape(-1, 2)[:400]:
        theta = float(theta)
        for axis in (0.0, math.pi / 2):
            dev = math.degrees(theta - axis)
            dev = (dev + 90) % 180 - 90
            if abs(dev) <= 5:
                deviations.append(dev)
    return float(np.median(deviations)) if deviations else 0.0


def detect_segments(binary, min_length: float, max_gap: float, threshold: int,
                    angle_tol: float) -> List[Dict[str, float]]:
    """霍夫线段检测 + 轴向分类（返回统一表示）"""
    raw = cv2.HoughLinesP(binary, 1, np.pi / 720, threshold=threshold,
                          minLineLength=int(min_length), maxLineGap=int(max_gap))
    segments = []
    if raw is None:
        return segments
    # 同上：4.x 是 (N,1,4)，5.x 是 (N,4)
    for x1, y1, x2, y2 in np.asarray(raw).reshape(-1, 4):
        seg = _seg(float(x1), float(y1), float(x2), float(y2))
        seg["class"] = classify(seg, angle_tol)
        if seg["class"] == "D":
            seg["orient"] = "D"
        segments.append(seg)
    return segments


# ---------------------------------------------------------------- 坐标与输出

def to_mm(value: float, scale: float, axis: str, origin: float) -> float:
    """像素 → 毫米；y 轴翻转（图像向下为正，CAD 向上为正）"""
    return (value - origin) / scale if axis == "x" else (origin - value) / scale


def build_plan_spec(walls: List[Dict[str, Any]], scale: float, origin_px: Tuple[float, float],
                    grid: float, grid_shift: float) -> Dict[str, Any]:
    """墙段 → wall_plan.py 的 plan.json 结构（中心线两端点 + 厚度 + 洞口）"""
    spec_walls = []
    for index, wall in enumerate(walls, start=1):
        if wall["orient"] == "H":
            start_xy = (to_mm(wall["start"], scale, "x", origin_px[0]),
                        to_mm(wall["center"], scale, "y", origin_px[1]))
            end_xy = (to_mm(wall["end"], scale, "x", origin_px[0]),
                      to_mm(wall["center"], scale, "y", origin_px[1]))
        else:
            start_xy = (to_mm(wall["center"], scale, "x", origin_px[0]),
                        to_mm(wall["start"], scale, "y", origin_px[1]))
            end_xy = (to_mm(wall["center"], scale, "x", origin_px[0]),
                      to_mm(wall["end"], scale, "y", origin_px[1]))
        snap = lambda value: snap_to_grid(value, grid, grid_shift)
        entry = {
            "id": "W%d-%s" % (index, wall["orient"]),
            "from": [round(snap(start_xy[0]), 1), round(snap(start_xy[1]), 1)],
            "to": [round(snap(end_xy[0]), 1), round(snap(end_xy[1]), 1)],
            "thickness": round(snap_to_grid(wall["thickness"] / scale, grid, grid_shift), 1),
            "openings": [
                {"kind": op["kind"],
                 "width": round(op["width"] / scale, 1),
                 # center 是"距墙起点"的距离：像素绝对值要先减去墙起点，
                 # 否则会整体偏移一个原点的量（实测偏了 200mm 才发现）
                 "center": round((op["center"] - wall["start"]) / scale, 1),
                 **({"hinge": "start", "swing": "positive"} if op["kind"] == "door" else {})}
                for op in wall["openings"]
            ],
        }
        spec_walls.append(entry)
    return {"walls": spec_walls}


def write_dxf(path: str, walls: List[Dict[str, Any]], leftovers: List[Dict[str, float]],
              scale: float, origin_px: Tuple[float, float], wall_layer: str, trace_layer: str) -> int:
    """写 DXF：墙配对结果画成闭合矩形（与 CAD 侧的墙身约定一致），其余线进描图图层"""
    import ezdxf
    doc = ezdxf.new("R2010")
    model = doc.modelspace()
    half = (lambda w: w["thickness"] / scale / 2)
    for wall in walls:
        half_t = half(wall)
        if wall["orient"] == "H":
            corners = [(wall["start"], wall["center"] - half_t), (wall["end"], wall["center"] - half_t),
                       (wall["end"], wall["center"] + half_t), (wall["start"], wall["center"] + half_t)]
        else:
            corners = [(wall["center"] - half_t, wall["start"]), (wall["center"] - half_t, wall["end"]),
                       (wall["center"] + half_t, wall["end"]), (wall["center"] + half_t, wall["start"])]
        points = [(to_mm(x, scale, "x", origin_px[0]), to_mm(y, scale, "y", origin_px[1]))
                  for x, y in corners]
        model.add_lwpolyline(points, close=True, dxfattribs={"layer": wall_layer})
    for seg in leftovers:
        if seg["orient"] == "D":
            x1, y1, x2, y2 = seg.get("x1", 0), seg.get("y1", 0), seg.get("x2", 0), seg.get("y2", 0)
        elif seg["orient"] == "H":
            x1, y1, x2, y2 = seg["start"], seg["offset"], seg["end"], seg["offset"]
        else:
            x1, y1, x2, y2 = seg["offset"], seg["start"], seg["offset"], seg["end"]
        model.add_line((to_mm(x1, scale, "x", origin_px[0]), to_mm(y1, scale, "y", origin_px[1])),
                       (to_mm(x2, scale, "x", origin_px[0]), to_mm(y2, scale, "y", origin_px[1])),
                       dxfattribs={"layer": trace_layer})
    doc.saveas(path)
    return len(walls) + len(leftovers)


def render_preview(path: str, image_path: str, walls: List[Dict[str, Any]],
                   leftovers: List[Dict[str, float]], matrix) -> None:
    """原图 + 识别结果叠加，供人工确认（这一步不能省）"""
    if cv2 is None:
        return
    canvas = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if canvas is None:
        return
    if matrix is not None and not np.allclose(matrix, np.eye(2, 3)):
        height, width = canvas.shape[:2]
        canvas = cv2.warpAffine(canvas, matrix, (width, height), borderValue=(255, 255, 255))
    for wall in walls:
        if wall["orient"] == "H":
            cv2.line(canvas, (int(wall["start"]), int(wall["center"])),
                     (int(wall["end"]), int(wall["center"])), (0, 0, 255), 1)
        else:
            cv2.line(canvas, (int(wall["center"]), int(wall["start"])),
                     (int(wall["center"]), int(wall["end"])), (0, 0, 255), 1)
        for opening in wall["openings"]:
            if wall["orient"] == "H":
                x = int(opening["center"])
                cv2.line(canvas, (x, int(wall["center"] - wall["thickness"])),
                         (x, int(wall["center"] + wall["thickness"])), (0, 170, 0), 1)
            else:
                y = int(opening["center"])
                cv2.line(canvas, (int(wall["center"] - wall["thickness"]), y),
                         (int(wall["center"] + wall["thickness"]), y), (0, 170, 0), 1)
    for seg in leftovers:
        if seg["orient"] == "H":
            cv2.line(canvas, (int(seg["start"]), int(seg["offset"])),
                     (int(seg["end"]), int(seg["offset"])), (255, 120, 0), 1)
        elif seg["orient"] == "V":
            cv2.line(canvas, (int(seg["offset"]), int(seg["start"])),
                     (int(seg["offset"]), int(seg["end"])), (255, 120, 0), 1)
    cv2.imwrite(path, canvas)


# ---------------------------------------------------------------- CLI

def parse_points(text: str) -> Tuple[float, float]:
    """解析 "x,y"（也容忍空格分隔）"""
    values = [float(part) for part in text.replace(";", " ").replace(",", " ").split()]
    if len(values) != 2:
        raise argparse.ArgumentTypeError('需要 "x,y"（收到 %r）' % text)
    return values[0], values[1]


def resolve_scale(args) -> Optional[float]:
    if args.px_per_mm:
        return float(args.px_per_mm)
    if args.calibrate:
        coords = [float(v) for v in args.calibrate.replace(" ", ",").split(",") if v.strip()]
        if len(coords) != 4:
            raise ValueError('--calibrate 需要 "x1,y1 x2,y2"（两个像素点）')
        pixels = math.hypot(coords[2] - coords[0], coords[3] - coords[1])
        if pixels <= 0 or args.real <= 0:
            raise ValueError("标定无效：两点距离与真实距离都必须大于 0")
        return pixels / float(args.real)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="线稿栅格图 → 墙线 / 调用序列 / DXF")
    parser.add_argument("image")
    parser.add_argument("--mode", choices=("walls", "lines"), default="walls")
    parser.add_argument("--px-per-mm", type=float, help="像素/毫米（标定方式一）")
    parser.add_argument("--calibrate", help='两个像素点 "x1,y1 x2,y2"（标定方式二）')
    parser.add_argument("--real", type=float, help="上述两点的真实距离（毫米）")
    parser.add_argument("--origin", type=parse_points, default=(0.0, 0.0),
                        help="对应 CAD 原点 (0,0) 的像素坐标，默认图像左下角")
    parser.add_argument("--angle-tol", type=float, default=3.0, help="轴线容差（度）")
    parser.add_argument("--merge-tol", type=float, default=30.0, help="同一直线合并容差（毫米，默认 30）")
    parser.add_argument("--join-gap", type=float, default=150.0, help="允许接上的小间断（毫米，默认 150）")
    parser.add_argument("--min-wall", type=float, default=80.0, help="最小墙厚（毫米，默认 80）")
    parser.add_argument("--max-wall", type=float, default=500.0, help="最大墙厚（毫米，默认 500）")
    parser.add_argument("--min-opening", type=float, default=500.0,
                        help="最小洞口宽（毫米，默认 500；识别出的洞口会因线宽比真值小约一个线宽）")
    parser.add_argument("--max-opening", type=float, default=4000.0, help="最大洞口宽（毫米，默认 4000）")
    parser.add_argument("--door-max", type=float, default=1000.0,
                        help="≤该宽度猜成门（毫米，默认 1000；只是猜测，必须人工确认）")
    parser.add_argument("--door-min", type=float, default=700.0,
                        help="≥该宽度才猜成门（毫米，默认 700；更窄的多半是卫生间窗）")
    parser.add_argument("--overlap", type=float, default=0.6, help="平行线对的最小重叠比例")
    parser.add_argument("--grid", type=float, default=10.0, help="网格吸附间距（毫米，默认 10）")
    parser.add_argument("--grid-shift", type=float, default=15.0, help="网格吸附允许的最大位移（毫米）")
    parser.add_argument("--min-line-mm", type=float, default=300.0, help="最短候选线（毫米，默认 300）")
    parser.add_argument("--hough-threshold", type=int, default=40)
    parser.add_argument("--max-gap-px", type=float, default=4.0)
    parser.add_argument("--adaptive", action="store_true", help="自适应阈值（扫描件/光照不均）")
    parser.add_argument("--deskew", action="store_true", help="先纠偏")
    parser.add_argument("--despeckle", action="store_true", help="中值去噪")
    parser.add_argument("--out-plan", help="输出 plan.json")
    parser.add_argument("--out-calls", help="输出调用序列 JSON（复用 wall_plan.py 生成）")
    parser.add_argument("--out-dxf", help="输出 DXF（写线段或墙矩形）")
    parser.add_argument("--preview", help="输出叠加预览 PNG（务必人工确认）")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scale = resolve_scale(args)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        print()
        return EXIT_USAGE
    if scale is None and args.mode == "walls":
        json.dump({"ok": False, "error": "walls 模式必须先标定：--px-per-mm 或 --calibrate ... --real ..."},
                  sys.stdout, ensure_ascii=False)
        print()
        return EXIT_USAGE
    scale = scale or 1.0

    try:
        binary, matrix = load_binary(args.image, args.adaptive, args.deskew, args.despeckle)
    except RuntimeError as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        print()
        return EXIT_FAILURE

    min_length = max(8.0, args.min_line_mm * scale)
    segments = detect_segments(binary, min_length, args.max_gap_px, args.hough_threshold, args.angle_tol)
    axis = [s for s in segments if s["orient"] in ("H", "V")]
    diagonals = [s for s in segments if s["orient"] == "D"]
    merged = merge_collinear(axis, args.merge_tol * scale, args.join_gap * scale)
    walls, leftovers = pair_walls(merged, args.min_wall * scale, args.max_wall * scale, args.overlap)
    # 封边线长度≈墙厚（240mm 在 0.2px/mm 下只有 48px），会被主通道的最短线长滤掉，
    # 因此单独跑一趟短线检测专供洞口定位；阈值提高以保证只有实线段入选。
    jamb_segments = detect_segments(binary, max(6.0, 0.5 * args.min_wall * scale),
                                    args.max_gap_px, max(15, args.hough_threshold // 2),
                                    args.angle_tol)
    grouped = group_wall_runs(walls, args.merge_tol * scale, args.join_gap * scale,
                              args.min_opening * scale, args.max_opening * scale,
                              args.door_max * scale)
    # 封边线定位洞口（更稳、宽度更准），再按宽度补类型、用门扇线证据修正
    from_jamb = detect_jamb_openings(grouped, jamb_segments, merged,
                                     args.min_opening * scale, args.max_opening * scale)
    for wall in grouped:
        for opening in wall["openings"]:
            if not opening.get("kind"):
                width_mm = opening["width"] / scale
                opening["kind"] = ("door" if args.door_min <= width_mm <= args.door_max
                                   else "window")
    leaf_fixed = apply_leaf_evidence(grouped, merged, leaf_tol=args.merge_tol * scale,
                                     min_ratio=0.6)

    origin_px = (args.origin[0], args.origin[1] or binary.shape[0])
    spec = build_plan_spec(grouped, scale, origin_px, args.grid, args.grid_shift)
    stats = {
        "ok": True,
        "mode": args.mode,
        "px_per_mm": round(scale, 6),
        "image_size": [int(binary.shape[1]), int(binary.shape[0])],
        "raw_segments": len(segments),
        "axis_segments": len(axis),
        "diagonal_segments": len(diagonals),
        "merged_segments": len(merged),
        "wall_runs": len(walls),
        "walls": len(grouped),
        "openings": sum(len(wall["openings"]) for wall in grouped),
        "openings_from_jambs": from_jamb,
        "door_leaf_evidence": leaf_fixed,
        "unpaired_lines": len(leftovers),
        "notes": [
            "洞口类型按宽度猜测，必须看预览图确认",
            "识别结果是中心线 + 厚度；洞口位置需要在 CAD/预览里核对",
        ],
    }

    if args.out_plan:
        with open(args.out_plan, "w", encoding="utf-8") as handle:
            json.dump(spec, handle, ensure_ascii=False, indent=2)
        stats["plan"] = args.out_plan
    if args.out_calls:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import wall_plan
        built = wall_plan.build_plan(spec, {"wall": "A-WALL", "door": "A-DOOR", "window": "A-WINDOW"})
        with open(args.out_calls, "w", encoding="utf-8") as handle:
            json.dump(built, handle, ensure_ascii=False, indent=2)
        stats["calls"] = args.out_calls
        stats["calls_count"] = built["summary"]["calls"]
        stats["warnings"] = built["warnings"]
    if args.out_dxf:
        try:
            stats["dxf_entities"] = write_dxf(args.out_dxf, grouped, leftovers, scale, origin_px,
                                              "A-WALL", "A-TRACE")
            stats["dxf"] = args.out_dxf
        except ImportError:
            stats["dxf_error"] = "需要 ezdxf：pip install ezdxf"
    if args.preview:
        render_preview(args.preview, args.image, grouped, leftovers, matrix)
        stats["preview"] = args.preview

    json.dump(stats, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    print()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
