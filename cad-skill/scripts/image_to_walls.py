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


def load_channels(path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (灰度, 明度V, 饱和度S)

    彩色效果图里地板/木饰是**有饱和度的中间调**（灰阶上看是"深色"），
    只按灰度取墨迹会把它们整片当成墙。墙体是近黑且接近中性灰，
    所以判据要同时看明度与饱和度。
    """
    if Image is None:
        raise RuntimeError("需要 Pillow")
    rgb = np.array(Image.open(path).convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    return (cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), hsv[:, :, 2], hsv[:, :, 1])


def auto_ink_threshold(gray: np.ndarray) -> float:
    """Otsu 分层后再取两类均值的中间值（硬边图上 Otsu 会落在类别灰度值上）"""
    cut = float(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
    lower, upper = gray[gray <= cut], gray[gray > cut]
    if lower.size and upper.size:
        cut = (float(lower.mean()) + float(upper.mean())) / 2.0
    return cut


def wall_mask(gray: np.ndarray, threshold: float, min_wall_px: float,
              saturation: np.ndarray = None, sat_max: float = 0.0):
    """墙体掩膜：先按灰度取深色墨迹，再用开运算滤掉比 min_wall_px 更细的线（家具、文字）

    saturation/sat_max 给了就再卡一道饱和度：彩色地板、木饰、彩色家具填充
    是有饱和度的，不属于"墨线"；墙体近黑且中性。这一步能显著减少彩色图上的误检。
    """
    ink_map = gray < threshold
    if saturation is not None and sat_max > 0:
        ink_map = ink_map & (saturation < sat_max)
    ink = ink_map.astype(np.uint8) * 255
    radius = max(1, int(round(min_wall_px / 2)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    walls = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    return walls, ink


def drop_blob_components(mask: np.ndarray, thickness_hint: float,
                         min_size_ratio: float = 5.0, fill_ratio_max: float = 0.55
                         ) -> Tuple[np.ndarray, int, int]:
    """剔除"块状填色物"（家具、洁具、植物、深色台面），返回 (掩膜, 丢弃数, 丢弃面积)

    判据：在**两个方向上都明显大于墙厚**（min(w,h) ≥ min_size_ratio × 墙厚），
    且外接框被填得较满（fill_ratio > fill_ratio_max）——这是色块的形状特征；
    墙段是细长的、转角是空心的 L 形，都不会满足。
    """
    if thickness_hint <= 0:
        return mask, 0, 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    keep = np.ones(count, dtype=bool)
    dropped = dropped_area = 0
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area <= 0:
            continue
        if min(w, h) >= min_size_ratio * thickness_hint and area / float(w * h) > fill_ratio_max:
            keep[index] = False
            dropped += 1
            dropped_area += int(area)
    return np.where(keep[labels], mask, 0).astype(np.uint8), dropped, dropped_area


def drop_small_components(mask: np.ndarray, min_area: float) -> Tuple[np.ndarray, int, int]:
    """丢掉面积过小的连通域（指北针、尺寸箭头、文字笔画），返回 (掩膜, 丢弃数, 丢弃面积)

    注意不能"只留最大块"：门窗会把墙网打断成几十块（本样本 43 块），
    只留最大块会把大半面墙丢掉。按绝对面积过滤才安全。
    """
    if min_area <= 0:
        return mask, 0, 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    keep = np.zeros(count, dtype=bool)
    dropped = dropped_area = 0
    for index in range(1, count):
        area = int(stats[index][4])
        if area >= min_area:
            keep[index] = True
        else:
            dropped += 1
            dropped_area += area
    return np.where(keep[labels], mask, 0).astype(np.uint8), dropped, dropped_area


def snap_thickness(walls: List[Dict[str, Any]], targets: List[float],
                   tolerance: float) -> int:
    """把墙厚吸附到常见值（如 100/120/200/240mm），容差内才吸附

    厚度取的是距离变换中位数，交汇与洞口附近会偏薄（本样本 237 → 240mm 更接近真实）。
    """
    if not targets:
        return 0
    snapped = 0
    for wall in walls:
        thickness = wall.get("thickness")
        if not thickness:
            continue
        nearest = min(targets, key=lambda value: abs(value - thickness))
        if abs(nearest - thickness) <= tolerance * nearest:
            if abs(nearest - thickness) > 1e-9:
                snapped += 1
            wall["thickness"] = float(nearest)
    return snapped


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


# ---------------------------------------------------------------- 空间（房间）识别

def building_envelope(wall_mask: np.ndarray, seal_px: int) -> np.ndarray:
    """建筑外壳：闭运算封门窗洞口后填洞，取最大连通域

    外壳用来把"室外"排除掉——门窗是房间通向室外的开口，不封上就会让室内
    和室外连成一片（实测只剩 1~3 个房间）。

    核尺寸两头都试过：短边 1/8 封不住 188px 的窗洞（灰图只剩 0 个房间），
    短边 1/3 又会把外壳拓扑搞坏、把室外算进来。现在按墙厚推导（12 倍墙厚）、
    上限取短边 1/4，并在 detect_rooms 里用"边界必须有真墙"再筛一遍。
    """
    seal_px = max(3, min(seal_px, min(wall_mask.shape) // 4))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * seal_px + 1, 2 * seal_px + 1))
    closed = cv2.morphologyEx(wall_mask, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((closed > 0).astype(np.uint8), 8)
    if count <= 1:
        return closed
    biggest = 1 + int(np.argmax(stats[1:, 4]))
    component = (labels == biggest).astype(np.uint8) * 255
    envelope = cv2.bitwise_or(component, fill_holes(component))
    # 裁到墙网外接框：闭运算会把外壳撑到图边，边距会被当成"室内"多算面积
    # （实测合成图上因此多出 4 个假房间、面积接近真实值的两倍）
    ys, xs = np.nonzero(wall_mask)
    if len(xs):
        envelope[:max(0, ys.min() - 1), :] = 0
        envelope[ys.max() + 2:, :] = 0
        envelope[:, :max(0, xs.min() - 1)] = 0
        envelope[:, xs.max() + 2:] = 0
    return envelope


def neighbour_count(binary: np.ndarray) -> np.ndarray:
    """8 邻域计数。

    别用 cv2.filter2D(ddepth=-1)：在 uint8 上实测把"两个邻居"算成了 1，
    于是每个骨架像素都被当成端点（实测 9115/9239），进而桥接乱连。
    """
    image = (binary > 0).astype(np.int16)
    padded = np.pad(image, 1)
    height, width = image.shape
    return sum(padded[dy:dy + height, dx:dx + width]
               for dy in (0, 1, 2) for dx in (0, 1, 2) if (dy, dx) != (1, 1))


def prune_spurs(skeleton: np.ndarray, min_branch: int = 12) -> np.ndarray:
    """剪掉短于 min_branch 的骨架毛刺：毛刺会在洞口检测里制造假端点"""
    image = (skeleton > 0).astype(np.uint8).copy()
    height, width = image.shape
    for _ in range(3):
        neighbours = neighbour_count(image)
        ys, xs = np.nonzero((image > 0) & (neighbours == 1))
        removed = 0
        for x, y in zip(xs, ys):
            path = [(int(x), int(y))]
            px, py = int(x), int(y)
            reached_branch = False
            for _step in range(min_branch):
                nxt = None
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        cx, cy = px + dx, py + dy
                        if (dx or dy) and 0 <= cy < height and 0 <= cx < width \
                                and image[cy, cx] and (cx, cy) not in path:
                            nxt = (cx, cy)
                            break
                    if nxt:
                        break
                if nxt is None:
                    break
                if neighbours[nxt[1], nxt[0]] >= 3:
                    reached_branch = True
                    break
                path.append(nxt)
                px, py = nxt
            if reached_branch:
                for rx, ry in path:
                    image[ry, rx] = 0
                    removed += 1
        if not removed:
            break
    return image


def skeleton_endpoints(skeleton: np.ndarray, radius: int = 12):
    """骨架端点及其局部主方向（邻域 PCA），用于找洞口两侧相对的墙端"""
    neighbours = neighbour_count(skeleton)
    ys, xs = np.nonzero((skeleton > 0) & (neighbours == 1))
    endpoints = []
    for x, y in zip(xs, ys):
        window = skeleton[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1]
        wy, wx = np.nonzero(window)
        if len(wx) < 5:
            continue
        points = np.stack([wx, wy], axis=1).astype(float)
        points -= points.mean(axis=0)
        _, singular, vt = np.linalg.svd(points, full_matrices=False)
        if singular[1] > 0.35 * singular[0]:      # 附近分支多 → 交汇点，不是洞口端
            continue
        endpoints.append((int(x), int(y), vt[0]))
    return endpoints


def bridge_openings(mask: np.ndarray, max_gap_px: float, min_gap_px: float = 4.0,
                    angle_tol_deg: float = 35.0):
    """把相对的墙端接起来（门窗洞口），返回 (桥接后的掩膜, 桥接数量)"""
    skeleton = prune_spurs(thin(mask))
    distance = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    endpoints = skeleton_endpoints(skeleton)
    bridged_mask = mask.copy()
    used = set()
    bridges = 0
    for i, (x1, y1, direction1) in enumerate(endpoints):
        if i in used:
            continue
        best = None
        for j in range(len(endpoints)):
            if j == i or j in used:
                continue
            x2, y2, direction2 = endpoints[j]
            gap = math.hypot(x2 - x1, y2 - y1)
            if not (min_gap_px <= gap <= max_gap_px):
                continue
            vector = np.array([x2 - x1, y2 - y1], float) / gap
            angle1 = math.degrees(math.acos(min(1.0, abs(float(np.dot(direction1, vector))))))
            angle2 = math.degrees(math.acos(min(1.0, abs(float(np.dot(direction2, vector))))))
            if angle1 > angle_tol_deg or angle2 > angle_tol_deg:
                continue
            if best is None or gap < best[0]:     # 取最近的合规搭档，避免跨房间乱连
                best = (gap, j, x2, y2)
        if best is not None:
            _, j, x2, y2 = best
            thickness = int(round(2 * max(distance[y1, x1], distance[y2, x2])))
            cv2.line(bridged_mask, (x1, y1), (x2, y2), 255, thickness=max(1, thickness))
            bridges += 1
            used.update((i, j))
    return bridged_mask, bridges


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """填内部空洞：把"不接触图像边界"的背景连通域当作洞

    比"从 (0,0) 灌背景再取反"稳：核过大时角落可能已属于连通域，
    那时灌水法会把整张图误当外壳（实测合成图上房间被切碎成 4 块）。
    """
    background = (mask == 0).astype(np.uint8)
    count, labels = cv2.connectedComponents(background, 4)
    if count <= 1:
        return np.zeros_like(mask)
    border = set(labels[0, :].tolist()) | set(labels[-1, :].tolist()) \
        | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    border.discard(0)
    keep = np.ones(count, dtype=bool)
    for label in border:
        keep[label] = False
    return np.where(keep[labels], 255, 0).astype(np.uint8)


def detect_rooms(wall_mask: np.ndarray, scale: float, min_area_m2: float,
                 seal_mm: float = 3000.0, door_half_mm: float = 450.0,
                 epsilon_px: float = 4.0, seal_px: int = None,
                 door_half_px: float = None,
                 min_area_px: float = None,
                 min_support: float = 0.5) -> List[Dict[str, Any]]:
    """在外壳内按"门口颈部"切分房间（距离变换 + 分水岭）

    做法参考经典房间分割：门口是自由空间里的窄颈，房间中心离墙远。
    以"离墙 > 门口半宽"的区域作种子，再让分水岭在自由空间内生长到边界，
    颈部自然成为分界——不依赖洞口是否被完全桥接。
    """
    height, width = wall_mask.shape
    seal = seal_px if seal_px is not None else max(3, int(round(seal_mm * scale / 2)))
    envelope = building_envelope(wall_mask, seal)
    interior = ((envelope > 0) & (wall_mask == 0)).astype(np.uint8)
    interior = cv2.erode(interior, np.ones((3, 3), np.uint8))   # 削掉贴墙的 1px 缝
    if not interior.any():
        return []
    distance = cv2.distanceTransform(interior, cv2.DIST_L2, 5)
    door_half = door_half_px if door_half_px is not None else door_half_mm * scale
    seeds = (distance > door_half).astype(np.uint8)
    seed_labels = cv2.connectedComponents(seeds, 4)[1]
    markers = np.zeros((height, width), np.int32)               # 0 = 未知，交给分水岭
    markers[interior == 0] = 1                                  # 1 = 背景
    markers[seeds > 0] = seed_labels[seeds > 0] + 1
    if markers.max() < 2:
        return []
    canvas = cv2.cvtColor(interior * 255, cv2.COLOR_GRAY2BGR)
    labels = cv2.watershed(canvas, markers)

    rooms: List[Dict[str, Any]] = []
    calibrated = scale != 1.0
    for label in sorted(int(v) for v in np.unique(labels) if v > 1):
        region = ((labels == label) & (interior > 0)).astype(np.uint8)
        area = int(region.sum())
        if area == 0:
            continue
        # 未标定时"1m²"没有意义（会变成 1e6px²），改用按墙厚推导的像素面积
        if min_area_px is not None:
            if area < min_area_px:
                continue
        else:
            area_m2 = area / (scale * scale) / 1e6
            if area_m2 < min_area_m2:
                continue
        ys, xs = np.nonzero(region)
        if xs.min() <= 0 or ys.min() <= 0 or xs.max() >= width - 1 or ys.max() >= height - 1:
            continue                                            # 贴图像边界 → 室外
        # 真实性校验：房间边界必须由真墙支撑。闭运算造出来的假区域/室外口袋
        # 边界下面没有真墙，这一条能把它们筛掉（实测灰图曾因此多算 85% 面积）。
        band = cv2.dilate(region, np.ones((5, 5), np.uint8)) & cv2.bitwise_not(region * 255)
        wall_band = cv2.dilate((wall_mask > 0).astype(np.uint8) * 255, np.ones((5, 5), np.uint8))
        band_pixels = float((band > 0).sum())
        if band_pixels <= 0:
            continue
        real_wall = float(((band > 0) & (wall_band > 0)).sum()) / band_pixels
        if real_wall < min_support:
            continue
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        approx = cv2.approxPolyDP(contour, epsilon_px, True).reshape(-1, 2).astype(float)
        if len(approx) < 3:
            continue
        moments = cv2.moments(region, binaryImage=True)
        centroid = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
        rooms.append({"index": 0, "area_px": area,
                      "area_m2": round(area / (scale * scale) / 1e6, 2) if calibrated else None,
                      "centroid_px": [float(centroid[0]), float(centroid[1])],
                      "vertices": int(len(approx)), "polygon_px": approx.tolist()})
    rooms.sort(key=lambda room: -room["area_px"])
    for order, room in enumerate(rooms, start=1):
        room["index"] = order
    return rooms


# ---------------------------------------------------------------- 输出

def to_mm(value: float, scale: float, flip: bool, size: int, origin: float) -> float:
    delta = (value - origin) / scale
    return -delta if flip else delta


def write_dxf(path: str, walls: List[Dict[str, Any]], thin: List[Dict[str, Any]],
              scale: float, shape, origin, wall_style: str, wall_layer: str,
              trace_layer: str, centerline_layer: str, extend: bool = True,
              rooms: List[Dict[str, Any]] = None, room_layer: str = "A-ROOM",
              room_labels: bool = False, label_height_mm: float = 200.0) -> Dict[str, int]:
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

    for room in (rooms or []):
        polygon = [point(*p) for p in room["polygon_px"]]
        model.add_lwpolyline(polygon, close=True, dxfattribs={"layer": room_layer})
        counts["room_polylines"] = counts.get("room_polylines", 0) + 1
        if room_labels:
            centre = point(*room["centroid_px"])
            label = model.add_text("R%d  %.1fm2" % (room["index"], room["area_m2"]),
                                   height=label_height_mm,  # 图纸单位（毫米）
                                   dxfattribs={"layer": room_layer})
            label.set_placement(centre)
            counts["room_labels"] = counts.get("room_labels", 0) + 1
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
                        "thickness": round((wall.get("thickness") or 0) / scale, 1),
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
    parser.add_argument("--sat-max", type=float, default=90.0,
                        help="墙体最大饱和度（默认 90，0=关闭）：彩色地板/木饰/彩色家具填充"
                             "有饱和度，不该被当成墨线；墙体近黑且中性")
    parser.add_argument("--line-threshold", type=float, default=235.0,
                        help="线work灰度阈值（默认 235，含家具/洁具/文字）")
    parser.add_argument("--min-wall-px", type=float, default=8.0,
                        help="最小墙厚（像素，默认 8）：比它细的线（家具、文字）会被开运算滤掉")
    parser.add_argument("--blob-size-ratio", type=float, default=5.0,
                        help="块状物剔除阈值（默认 5）：两个方向都大于 5 倍墙厚、且外接框填得"
                             "很满的连通域按家具色块剔除；0=关闭")
    parser.add_argument("--min-room-m2", type=float, default=1.0,
                        help="最小房间面积（平方米，默认 1.0），更小的当噪点")
    parser.add_argument("--no-rooms", dest="rooms", action="store_false",
                        help="不做房间（空间）识别")
    parser.add_argument("--room-layer", default="A-ROOM")
    parser.add_argument("--room-labels", action="store_true",
                        help="在房间中心写序号与面积（默认不写，保持画面整洁）")
    parser.add_argument("--seal-mm", type=float, default=3000.0,
                        help="外壳封洞尺度（毫米，默认 3000）：闭运算把门窗封上，"
                             "不封的话室内会从窗口漏到室外（实测只剩 3 个房间）")
    parser.add_argument("--door-half-mm", type=float, default=450.0,
                        help="门口半宽（毫米，默认 450）：离墙超过它的区域作房间种子，"
                             "分水岭在门口颈部切分房间")
    parser.add_argument("--room-wall-support", type=float, default=0.5,
                        help="房间边界的真墙支撑下限（默认 0.5）：低于它认为是闭运算"
                             "造出的假区域；窗洞大的图可下调")
    parser.add_argument("--keep-outside-trace", dest="trace_inside_only", action="store_false",
                        help="保留室外线work（默认只保留室内，尺寸线/指北针等不进 DXF）")
    parser.add_argument("--component-min-area", type=float, default=0.0,
                        help="丢弃面积小于该值（像素²）的连通域，默认 0=不过滤；"
                             "指北针/尺寸箭头/文字笔画通常 <1000，可设 600")
    parser.add_argument("--max-wall-px", type=float, default=0.0,
                        help="丢弃厚度超过该值（像素）的段，默认 0=不过滤；"
                             "T 型交汇处最大内切圆偏大，会产生个别虚厚段（可设 60）")
    parser.add_argument("--snap-thickness",
                        help="把墙厚吸附到这些常见值（毫米，逗号分隔，如 100,120,200,240）")
    parser.add_argument("--snap-tolerance", type=float, default=0.2,
                        help="墙厚吸附容差（相对值，默认 0.2）")
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
        gray, value, saturation = load_channels(args.image)
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return EXIT_FAILURE

    threshold = args.wall_threshold or auto_ink_threshold(gray)
    walls_mask, ink_mask = wall_mask(gray, threshold, args.min_wall_px,
                                     saturation=saturation, sat_max=args.sat_max)
    walls_mask, blob_dropped, blob_area = drop_blob_components(
        walls_mask, args.min_wall_px, args.blob_size_ratio) if args.blob_size_ratio > 0 \
        else (walls_mask, 0, 0)
    walls_mask, dropped_components, dropped_area = drop_small_components(
        walls_mask, args.component_min_area)
    if not walls_mask.any():
        print(json.dumps({"ok": False,
                          "error": "没有识别到墙体：调小 --min-wall-px 或调大 --wall-threshold"},
                         ensure_ascii=False))
        return EXIT_FAILURE

    distance = cv2.distanceTransform((walls_mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    skeleton = thin(walls_mask)
    walls = [s for s in skeleton_segments(skeleton, distance, args.min_segment_px, args.join_gap)
             if (s.get("thickness") or 0) >= args.min_wall_px * 0.8]
    dropped_thick = 0
    if args.max_wall_px > 0:
        kept = [s for s in walls if (s.get("thickness") or 0) <= args.max_wall_px]
        dropped_thick = len(walls) - len(kept)
        walls = kept
    targets = [float(v) * scale for v in args.snap_thickness.split(",") if v.strip()] \
        if args.snap_thickness else []
    raw_groups = merge_thickness(walls)
    candidates = [float(v) for v in args.snap_thickness.split(",") if v.strip()] \
        if args.snap_thickness else []
    snapped = snap_thickness(walls, targets, args.snap_tolerance)
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

    # 空间识别：先桥接门洞（否则房间会从门口连成一片），再把墙外连通域当房间
    room_list: List[Dict[str, Any]] = []
    if args.rooms:
        if scale == 1.0:
            # 未标定：毫米阈值没有意义（450mm 会被当成 450px）。改用实测墙厚推导：
            # 门口半宽 ≈ 3 倍墙厚、封洞尺度 ≈ 12 倍墙厚。
            thickness_px = merge_thickness(walls)["groups"][0]["thickness_px"] if walls else 18.0
            room_list = detect_rooms(walls_mask, scale, args.min_room_m2,
                                     seal_px=int(round(15 * thickness_px)),
                                     door_half_px=3 * thickness_px,
                                     min_area_px=max(500.0, (12 * thickness_px) ** 2))
        else:
            room_list = detect_rooms(walls_mask, scale, args.min_room_m2,
                                     seal_mm=args.seal_mm, door_half_mm=args.door_half_mm,
                                     min_support=args.room_wall_support)

    trace_filtered_out = 0
    if args.trace and args.trace_inside_only and room_list:
        interior = np.zeros(gray.shape, np.uint8)
        for room in room_list:
            cv2.fillPoly(interior, [np.array(room["polygon_px"], np.int32)], 255)
        interior[walls_mask > 0] = 255
        kept_segments = []
        for segment in thin_segments:
            if segment["orient"] == "H":
                point_px = (int((segment["start"] + segment["end"]) / 2), int(segment["offset"]))
            else:
                point_px = (int(segment["offset"]), int((segment["start"] + segment["end"]) / 2))
            if 0 <= point_px[1] < interior.shape[0] and 0 <= point_px[0] < interior.shape[1] \
                    and interior[point_px[1], point_px[0]]:
                kept_segments.append(segment)
        trace_filtered_out = len(thin_segments) - len(kept_segments)
        thin_segments = kept_segments

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
        "px_per_mm": scale,
        "wall_segments": len(walls),
        "wall_total_length_px": round(sum(s["length"] for s in walls), 1),
        "wall_total_length_mm": round(sum(s["length"] for s in walls) / scale, 1),
        "thickness": merge_thickness(walls),
        "thickness_raw": raw_groups,
        "thickness_classes": ([
            {"thickness_mm": target,
             "segments": sum(1 for s in walls
                             if s.get("thickness") and abs(s["thickness"] - target * scale) < 1e-6)}
            for target in candidates
        ] + [{"thickness_mm": None,
              "segments": sum(1 for s in walls if s.get("thickness") and
                              not any(abs(s["thickness"] - t * scale) < 1e-6 for t in candidates))}]
            if candidates else []),
        "wall_mask_iou": round(intersection / union, 4) if union else 0.0,
        "wall_mask_coverage": round(intersection / float(truth.sum()), 4) if truth.sum() else 0.0,
        "trace_segments": len(thin_segments),
        "ink_pixels": int((ink_mask > 0).sum()),
        "wall_pixels": int((walls_mask > 0).sum()),
        "dropped_components": dropped_components,
        "dropped_component_area": dropped_area,
        "dropped_thick_segments": dropped_thick,
        "snapped_segments": snapped,
        "dropped_blob_components": blob_dropped,
        "dropped_blob_area": blob_area,
        "sat_max": args.sat_max,
        "rooms": len(room_list),
        "room_total_area_m2": (round(sum(r["area_m2"] for r in room_list), 2)
                               if scale != 1.0 else None),
        "room_total_area_px": sum(r["area_px"] for r in room_list),
        "room_sizes_m2": ([r["area_m2"] for r in room_list] if scale != 1.0 else None),
        "trace_filtered_out": trace_filtered_out,
    }
    if scale == 1.0:
        payload["scale_note"] = ("未标定：几何单位是像素。给 --px-per-mm 或 "
                                 "--calibrate \"x1,y1 x2,y2\" --real 毫米 才是真实尺寸；"
                                 "未标定时 plan.json 的墙厚（约 19/10px）会被 wall_plan.py 判为异常")
    if args.out_dxf:
        counts = write_dxf(args.out_dxf, walls, thin_segments, scale, gray.shape, origin,
                           args.walls, args.wall_layer, args.trace_layer, args.centerline_layer,
                           extend=args.extend_ends, rooms=room_list,
                           room_layer=args.room_layer, room_labels=args.room_labels)
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
