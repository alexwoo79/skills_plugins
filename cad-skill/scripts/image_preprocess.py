#!/usr/bin/env python3
"""图像预处理器：背景网格去除与几何吸附。

这里只做"候选几何"的清理，不推断墙体或门窗：
  * remove_background_grid：识别等距、细、贯穿画布的网格线并从墨迹掩膜里去掉；
  * snap_paths：把共线折线接成一条、把近邻端点吸到交点/T 形接头。

所有开关默认由调用方决定；单独使用时保持输入不变（无命中就原样返回）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

Point = Tuple[float, float]
Path = List[Point]


def _binary(ink: np.ndarray) -> np.ndarray:
    array = np.asarray(ink)
    if array.ndim != 2:
        raise ValueError("需要二维墨迹掩膜")
    return (array > 0).astype(np.uint8)


def _line_candidates(binary: np.ndarray, horizontal: bool,
                     min_coverage: float, max_thickness: float) -> List[Dict[str, float]]:
    """找贯穿画布的细直线候选（行/列投影，不依赖霍夫参数）。"""
    coverage = binary.mean(axis=1 if horizontal else 0)
    hits = coverage >= min_coverage
    candidates: List[Dict[str, float]] = []
    index = 0
    limit = len(hits)
    while index < limit:
        if not hits[index]:
            index += 1
            continue
        end = index
        while end + 1 < limit and hits[end + 1]:
            end += 1
        thickness = end - index + 1
        if thickness <= max_thickness:
            segment = coverage[index:end + 1]
            candidates.append({
                "pos": (index + end) / 2.0,
                "thickness": float(thickness),
                "coverage": float(segment.max()),
            })
        index = end + 1
    return candidates


def _regular_series(candidates: List[Dict[str, float]], min_lines: int,
                    spacing_tol: float, max_spacing: float) -> Tuple[List[Dict[str, float]], float]:
    """从候选线里找出等距的一组；找不到就返回空列表。

    网格的判据不是"线很长"，而是"至少 min_lines 条、间距近似相等"。这样
    平面图里两根相距很远的墙线不会因为"都很长"就被删掉。
    """
    if len(candidates) < min_lines:
        return [], 0.0
    ordered = sorted(candidates, key=lambda item: item["pos"])
    unique: List[Dict[str, float]] = []
    for candidate in ordered:
        if unique and abs(candidate["pos"] - unique[-1]["pos"]) < 1.0:
            if candidate["coverage"] > unique[-1]["coverage"]:
                unique[-1] = candidate
            continue
        unique.append(candidate)
    if len(unique) < min_lines:
        return [], 0.0

    positions = [item["pos"] for item in unique]
    best: Optional[Tuple[Tuple[int, float], List[Dict[str, float]], float]] = None
    for first in range(len(positions) - 1):
        for second in range(first + 1, min(len(positions), first + 4)):
            spacing = positions[second] - positions[first]
            if spacing < 6.0 or (max_spacing and spacing > max_spacing):
                continue
            tolerance = max(1.0, spacing * spacing_tol)
            members: Dict[int, Dict[str, float]] = {}
            for candidate in unique:
                k = int(round((candidate["pos"] - positions[first]) / spacing))
                if k < 0:
                    continue
                fitted = positions[first] + k * spacing
                if abs(candidate["pos"] - fitted) <= tolerance:
                    old = members.get(k)
                    if old is None or candidate["coverage"] > old["coverage"]:
                        members[k] = candidate
            if len(members) < min_lines:
                continue
            ks = sorted(members)
            span = ks[-1] - ks[0] + 1
            if span > len(ks) * 1.6:
                continue
            score = (len(members), -spacing)
            if best is None or score > best[0]:
                best = (score, [members[k] for k in ks], spacing)
    if best is None:
        return [], 0.0
    return best[1], best[2]


def remove_background_grid(ink: np.ndarray, min_coverage: float = 0.55,
                           min_lines: int = 4, spacing_tol: float = 0.10,
                           max_thickness: float = 4.0,
                           protect_px: float = 3.0) -> Tuple[np.ndarray, Dict[str, Any]]:
    """去掉等距背景网格，返回清理后的墨迹和统计。

    只删除"细 + 贯穿画布 + 等距"的线。交叉处和粗笔画用垂直/水平支撑保护，
    避免把穿网而过的墙线整段抹掉。这个步骤是可选开关，因为真实图纸里也可能
    存在等距的长线（例如轴网）；误删时应关闭或调高 --grid-min-lines。
    """
    binary = _binary(ink)
    height, width = binary.shape[:2]
    max_spacing = max(32.0, min(height, width) * 0.55)
    horizontal_candidates = _line_candidates(binary, True, min_coverage, max_thickness)
    vertical_candidates = _line_candidates(binary, False, min_coverage, max_thickness)
    horizontal, spacing_h = _regular_series(
        horizontal_candidates, min_lines, spacing_tol, max_spacing)
    vertical, spacing_v = _regular_series(
        vertical_candidates, min_lines, spacing_tol, max_spacing)

    stats: Dict[str, Any] = {
        "applied": bool(horizontal or vertical),
        "grid_lines_h": len(horizontal),
        "grid_lines_v": len(vertical),
        "spacing_h": round(spacing_h, 2) if horizontal else None,
        "spacing_v": round(spacing_v, 2) if vertical else None,
        "removed_pixels": 0,
        "protected_pixels": 0,
    }
    if not horizontal and not vertical:
        return np.asarray(ink).copy(), stats

    # 距离变换保护粗笔画；垂直/水平支撑保护"穿网而过"的线。
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    protected = distance >= max(1.0, protect_px)
    stats["protected_pixels"] = int((binary & protected.astype(np.uint8)).sum())

    remove = np.zeros_like(binary, dtype=bool)
    vertical_support = cv2.boxFilter(binary, cv2.CV_32S, (1, 3), normalize=False)
    horizontal_support = cv2.boxFilter(binary, cv2.CV_32S, (3, 1), normalize=False)

    for line in horizontal:
        center = line["pos"]
        thickness = line["thickness"]
        low = max(0, int(math.floor(center - thickness / 2.0 - 1.0)))
        high = min(height, int(math.ceil(center + thickness / 2.0 + 1.0)) + 1)
        crossing = vertical_support[low:high] >= thickness + 1.5
        row_protect = protected[low:high] | crossing
        remove[low:high] |= ~row_protect

    for line in vertical:
        center = line["pos"]
        thickness = line["thickness"]
        low = max(0, int(math.floor(center - thickness / 2.0 - 1.0)))
        high = min(width, int(math.ceil(center + thickness / 2.0 + 1.0)) + 1)
        crossing = horizontal_support[:, low:high] >= thickness + 1.5
        column_protect = protected[:, low:high] | crossing
        remove[:, low:high] |= ~column_protect

    removed = binary.astype(bool) & remove
    output = np.asarray(ink).copy()
    output[removed] = 0
    stats["removed_pixels"] = int(removed.sum())
    return output, stats


def _clean_path(path: Sequence[Sequence[float]]) -> Path:
    result: Path = []
    for point in path:
        item = (float(point[0]), float(point[1]))
        if not result or math.dist(result[-1], item) > 1e-7:
            result.append(item)
    return result


def _inward(path: Path, end: int) -> Tuple[float, float]:
    """端点处指向路径内部的方向，使用几段点平滑噪声。"""
    if end == 0:
        target = path[min(len(path) - 1, 4)]
        return target[0] - path[0][0], target[1] - path[0][1]
    target = path[max(0, len(path) - 5)]
    return target[0] - path[-1][0], target[1] - path[-1][1]


def _cell(point: Point, size: float) -> Tuple[int, int]:
    return int(math.floor(point[0] / size)), int(math.floor(point[1] / size))


def _near_buckets(point: Point, buckets: Dict[Tuple[int, int], Any],
                  size: float, tol: float) -> Iterable[Any]:
    cx, cy = _cell(point, size)
    reach = max(1, int(math.ceil(tol / size)))
    for x in range(cx - reach, cx + reach + 1):
        for y in range(cy - reach, cy + reach + 1):
            yield from buckets.get((x, y), ())


def _merge_collinear_once(paths: List[Path], tol: float,
                          angle_tol_deg: float) -> Tuple[List[Path], int]:
    """一趟共线合并：端点相邻且方向相反的两段折线接成一条。"""
    if len(paths) < 2:
        return paths, 0
    cell = max(tol, 1e-6)
    buckets: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for index, path in enumerate(paths):
        for end in (0, -1):
            point = path[0] if end == 0 else path[-1]
            buckets.setdefault(_cell(point, cell), []).append((index, end))

    cos_limit = math.cos(math.radians(angle_tol_deg))
    proposals: List[Tuple[float, int, int, int, int]] = []
    for index, path in enumerate(paths):
        for end in (0, -1):
            point = path[0] if end == 0 else path[-1]
            direction = _inward(path, end)
            length = math.hypot(*direction)
            if length < 1e-6:
                continue
            for other, other_end in _near_buckets(point, buckets, cell, tol):
                if other <= index:
                    continue
                other_path = paths[other]
                other_point = other_path[0] if other_end == 0 else other_path[-1]
                gap = math.dist(point, other_point)
                if gap > tol:
                    continue
                other_direction = _inward(other_path, other_end)
                other_length = math.hypot(*other_direction)
                if other_length < 1e-6:
                    continue
                cosine = ((direction[0] * other_direction[0]
                           + direction[1] * other_direction[1]) / (length * other_length))
                if cosine > -cos_limit:
                    continue
                # 两个端点都要贴近对方延长线，避免把相邻但不共线的笔画误接。
                cross_a = abs(direction[0] * (other_point[1] - point[1])
                              - direction[1] * (other_point[0] - point[0])) / length
                cross_b = abs(other_direction[0] * (point[1] - other_point[1])
                              - other_direction[1] * (point[0] - other_point[0])) / other_length
                if max(cross_a, cross_b) > tol:
                    continue
                proposals.append((gap + cross_a + cross_b, index, end, other, other_end))

    if not proposals:
        return paths, 0
    proposals.sort(key=lambda item: item[0])
    used_ends = set()
    connections: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for _, index, end, other, other_end in proposals:
        if (index, end) in used_ends or (other, other_end) in used_ends:
            continue
        used_ends.add((index, end))
        used_ends.add((other, other_end))
        connections[(index, end)] = (other, other_end)
        connections[(other, other_end)] = (index, end)

    visited: set[int] = set()
    merged: List[Path] = []
    for start in range(len(paths)):
        if start in visited:
            continue
        # 优先从自由端开始，保证链的方向自然。
        start_end = 0
        if (start, 0) in connections and (start, -1) not in connections:
            start_end = -1
        chain: Path = []
        current = start
        entry = start_end
        while current not in visited:
            visited.add(current)
            path = paths[current] if entry == 0 else list(reversed(paths[current]))
            if chain and path and math.dist(chain[-1], path[0]) <= tol * 2.0:
                chain.extend(path[1:])
            else:
                chain.extend(path)
            exit_end = -1 if entry == 0 else 0
            link = connections.get((current, exit_end))
            if link is None:
                break
            current, entry = link
        merged.append(_clean_path(chain))
    return merged, len(paths) - len(merged)


def merge_collinear_paths(paths: Sequence[Sequence[Sequence[float]]],
                          tol: float = 2.5,
                          angle_tol_deg: float = 2.0,
                          max_passes: int = 4) -> Tuple[List[Path], int]:
    """反复合并共线折线，直到没有新连接。"""
    current = [_clean_path(path) for path in paths if len(path) >= 2]
    total = 0
    for _ in range(max(1, max_passes)):
        current, count = _merge_collinear_once(current, tol, angle_tol_deg)
        total += count
        if count == 0:
            break
    return current, total


def snap_axis(paths: List[Path], axis_tol: float = 1.5) -> Tuple[List[Path], int]:
    """把已经很接近水平/垂直的短偏差吸附到精确轴向。"""
    if axis_tol <= 0:
        return paths, 0
    snapped = 0
    output: List[Path] = []
    for path in paths:
        points = list(path)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        length = sum(math.dist(first, second) for first, second in zip(points, points[1:]))
        # 一整条已经近似水平的折线，统一吸到同一个 y；逐段取平均会留下 0.1px 级台阶。
        if length >= axis_tol * 4.0 and span_y <= axis_tol * 2.0 and span_x >= span_y:
            middle = sum(ys) / len(ys)
            points = [(x, middle) for x, _ in points]
            snapped += 1
            output.append(points)
            continue
        if length >= axis_tol * 4.0 and span_x <= axis_tol * 2.0 and span_y > span_x:
            middle = sum(xs) / len(xs)
            points = [(middle, y) for _, y in points]
            snapped += 1
            output.append(points)
            continue
        for index in range(len(points) - 1):
            x1, y1 = points[index]
            x2, y2 = points[index + 1]
            dx, dy = x2 - x1, y2 - y1
            if math.hypot(dx, dy) < axis_tol * 4.0:
                continue
            if abs(dx) >= abs(dy) and abs(dy) <= axis_tol:
                middle = (y1 + y2) / 2.0
                points[index] = (x1, middle)
                points[index + 1] = (x2, middle)
                snapped += 1
            elif abs(dy) > abs(dx) and abs(dx) <= axis_tol:
                middle = (x1 + x2) / 2.0
                points[index] = (middle, y1)
                points[index + 1] = (middle, y2)
                snapped += 1
        output.append(points)
    return output, snapped


def snap_endpoint_clusters(paths: List[Path], tol: float = 2.5) -> Tuple[List[Path], int]:
    """把近邻端点吸到同一点；直角端点吸到两线交点。"""
    if tol <= 0 or len(paths) < 2:
        return paths, 0
    endpoints: List[Tuple[int, int, Point, Tuple[float, float]]] = []
    for index, path in enumerate(paths):
        for end in (0, -1):
            point = path[0] if end == 0 else path[-1]
            endpoints.append((index, end, point, _inward(path, end)))

    cell = max(tol, 1e-6)
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for index, (_, _, point, _) in enumerate(endpoints):
        buckets.setdefault(_cell(point, cell), []).append(index)

    seen: set[int] = set()
    snapped = 0
    for start in range(len(endpoints)):
        if start in seen:
            continue
        queue = [start]
        group: List[int] = []
        seen.add(start)
        while queue:
            current = queue.pop()
            group.append(current)
            point = endpoints[current][2]
            for neighbour in _near_buckets(point, buckets, cell, tol):
                if neighbour in seen:
                    continue
                if math.dist(point, endpoints[neighbour][2]) <= tol:
                    seen.add(neighbour)
                    queue.append(neighbour)
        if len(group) < 2:
            continue
        refs = [endpoints[index] for index in group]
        target = (sum(item[2][0] for item in refs) / len(refs),
                  sum(item[2][1] for item in refs) / len(refs))
        if len(refs) == 2 and refs[0][0] != refs[1][0]:
            (_, _, pa, da), (_, _, pb, db) = refs
            la, lb = math.hypot(*da), math.hypot(*db)
            if la > 1e-6 and lb > 1e-6:
                cosine = abs((da[0] * db[0] + da[1] * db[1]) / (la * lb))
                if cosine <= 0.25:
                    intersection = _line_intersection(pa, da, pb, db)
                    if intersection and math.dist(intersection, pa) <= tol * 2.0:
                        target = intersection
        for index, end, point, _ in refs:
            if math.dist(point, target) > 1e-6:
                snapped += 1
            if end == 0:
                paths[index][0] = target
            else:
                paths[index][-1] = target
    return paths, snapped


def _line_intersection(p1: Point, d1: Tuple[float, float],
                       p2: Point, d2: Tuple[float, float]) -> Optional[Point]:
    det = d1[0] * (-d2[1]) - d1[1] * (-d2[0])
    if abs(det) < 1e-9:
        return None
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    t = (rx * (-d2[1]) - ry * (-d2[0])) / det
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def _segments(paths: List[Path]) -> List[Tuple[int, Point, Point, float]]:
    result = []
    for index, path in enumerate(paths):
        for first, second in zip(path, path[1:]):
            length = math.dist(first, second)
            if length > 1e-6:
                result.append((index, first, second, length))
    return result


def snap_endpoints_to_segments(paths: List[Path], tol: float = 2.5,
                               min_segment: float = 12.0) -> Tuple[List[Path], int]:
    """把端点吸到附近长线段的投影点，补上 T 形接头的小缺口。"""
    if tol <= 0 or len(paths) < 2:
        return paths, 0
    segments = [seg for seg in _segments(paths) if seg[3] >= min_segment]
    if not segments:
        return paths, 0
    cell = max(tol * 4.0, 8.0)
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for index, (_, first, second, _) in enumerate(segments):
        low_x, high_x = sorted((first[0], second[0]))
        low_y, high_y = sorted((first[1], second[1]))
        for x in range(int(math.floor(low_x / cell)), int(math.floor(high_x / cell)) + 1):
            for y in range(int(math.floor(low_y / cell)), int(math.floor(high_y / cell)) + 1):
                buckets.setdefault((x, y), []).append(index)

    snapped = 0
    for path_index, path in enumerate(paths):
        for end in (0, -1):
            point = path[0] if end == 0 else path[-1]
            best: Optional[Tuple[float, Point]] = None
            for segment_index in _near_buckets(point, buckets, cell, tol * 2.0):
                owner, first, second, length = segments[segment_index]
                if owner == path_index:
                    continue
                dx, dy = second[0] - first[0], second[1] - first[1]
                t = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / (length * length)
                if not 0.0 <= t <= 1.0:
                    continue
                projection = (first[0] + dx * t, first[1] + dy * t)
                distance = math.dist(point, projection)
                # 已经和对方端点重合的交给 cluster 处理，避免把端点拉回线段中部。
                if distance > tol or min(math.dist(point, first), math.dist(point, second)) <= tol:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, projection)
            if best is not None:
                if end == 0:
                    path[0] = best[1]
                else:
                    path[-1] = best[1]
                if math.dist(point, best[1]) > 1e-6:
                    snapped += 1
    return paths, snapped


def drop_collinear_points(paths: List[Path], tol: float = 0.5) -> Tuple[List[Path], int]:
    """删掉落在相邻两点连线上的中间点，减少 DXF 顶点数。"""
    if tol <= 0:
        return paths, 0
    removed = 0
    output: List[Path] = []
    for path in paths:
        if len(path) < 3:
            output.append(path)
            continue
        result = [path[0]]
        for index in range(1, len(path) - 1):
            previous, current, following = result[-1], path[index], path[index + 1]
            length = math.dist(previous, following)
            if length < 1e-9:
                removed += 1
                continue
            cross = abs((following[0] - previous[0]) * (current[1] - previous[1])
                        - (following[1] - previous[1]) * (current[0] - previous[0])) / length
            if cross <= tol:
                removed += 1
                continue
            result.append(current)
        result.append(path[-1])
        output.append(result)
    return output, removed


def snap_paths(paths: Sequence[Sequence[Sequence[float]]],
               endpoint_tol: float = 2.5, angle_tol_deg: float = 2.0,
               axis_tol: float = 1.5, collinear_tol: float = 0.5,
               t_junction: bool = True) -> Tuple[List[Path], Dict[str, Any]]:
    """共线合并 + 轴向吸附 + 端点吸附，返回 (新折线, 统计)。"""
    current = [_clean_path(path) for path in paths if len(path) >= 2]
    current, merged = merge_collinear_paths(current, endpoint_tol, angle_tol_deg)
    current, axis_snapped = snap_axis(current, axis_tol)
    current, endpoints_snapped = snap_endpoint_clusters(current, endpoint_tol)
    t_junctions = 0
    if t_junction:
        current, t_junctions = snap_endpoints_to_segments(current, endpoint_tol)
        current, extra = snap_endpoint_clusters(current, endpoint_tol)
        endpoints_snapped += extra
    current, points_removed = drop_collinear_points(current, collinear_tol)
    stats = {
        "applied": True,
        "merged_paths": merged,
        "axis_snapped": axis_snapped,
        "endpoints_snapped": endpoints_snapped,
        "t_junctions": t_junctions,
        "points_removed": points_removed,
        "paths_before": len(paths),
        "paths_after": len(current),
    }
    return current, stats


def _segment(x1: float, y1: float, x2: float, y2: float) -> Dict[str, float]:
    length = math.hypot(x2 - x1, y2 - y1)
    dx, dy = x2 - x1, y2 - y1
    base = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if abs(dy) <= abs(dx):
        base.update({"orient": "H", "offset": (y1 + y2) / 2.0,
                     "start": min(x1, x2), "end": max(x1, x2), "length": length,
                     "len_x": abs(dx), "len_y": abs(dy)})
    else:
        base.update({"orient": "V", "offset": (x1 + x2) / 2.0,
                     "start": min(y1, y2), "end": max(y1, y2), "length": length,
                     "len_x": abs(dx), "len_y": abs(dy)})
    return base


def snap_segments(segments: Sequence[Dict[str, float]], tol: float = 2.5,
                  angle_tol_deg: float = 2.0) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
    """线段端点吸附（Hough 路径）；返回重新构造的线段字典。"""
    lines = [(item["x1"], item["y1"], item["x2"], item["y2"]) for item in segments]
    paths = [[(x1, y1), (x2, y2)] for x1, y1, x2, y2 in lines]
    paths, stats = snap_paths(paths, endpoint_tol=tol, angle_tol_deg=angle_tol_deg)
    # 断开的线段仍保持为线段；只有同一直线上的两段才合并成一条。
    output: List[Dict[str, float]] = []
    for path in paths:
        for first, second in zip(path, path[1:]):
            output.append(_segment(first[0], first[1], second[0], second[1]))
    stats["segments_before"] = len(segments)
    stats["segments_after"] = len(output)
    return output, stats
