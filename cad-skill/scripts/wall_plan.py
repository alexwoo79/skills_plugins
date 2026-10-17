#!/usr/bin/env python3
"""直墙 + 洞口 → CAD 调用序列（默认单位 mm）。

只做几何换算，不做设计决策：墙厚、洞口位置必须来自用户确认过的方案。

输入 JSON:
{
  "walls": [
    {"from": [0, 0], "to": [12000, 0], "thickness": 240,
     "openings": [
       {"kind": "door", "width": 1000, "center": 3000, "hinge": "start", "swing": "positive"},
       {"kind": "window", "width": 2400, "center": 7000}
     ]}
  ],
  "axis": {"x": [0, 3600, 7200, 12000], "y": [0, 4800, 8000]},
  "dims": {"offset": 1200, "textheight": 250},
  "stairs": [{"origin": [0, 0], "angle": 90, "steps": 12, "tread": 260, "width": 1200}]
}

墙段：每段一个闭合多段线（矩形截面），墙两端按 半墙厚 延伸近似接角。
洞口：两侧封边线走 A-WALL；门补门扇线 + 90° 开启弧（A-DOOR）；
      窗补洞口内外墙面线 + 中位窗线（A-WINDOW）。
轴网：轴线 + 两端轴号圆圈与文字（A-GRID）；不给 axis 就只画墙。
尺寸：按墙中心线或轴线成链标注（A-DIM）；不给 dims 就不画。
楼梯：等间距踏步线 + 走向箭头 + 文字（A-STAR）。

用法:
  python3 wall_plan.py plan.json --pretty
  python3 wall_plan.py plan.json --unit m --only wall,door --out calls.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Dict, List

DEFAULT_LAYERS = {"wall": "A-WALL", "door": "A-DOOR", "window": "A-WINDOW",
                  "grid": "A-GRID", "dim": "A-DIM", "stair": "A-STAR"}
KINDS = ("door", "window", "opening")
SWING_UP = ("positive", "plus", "+", "left", "in", "n")
SWING_DOWN = ("negative", "minus", "-", "right", "out", "s")
PHASE_ORDER = ("wall", "door", "window", "grid", "dim", "stair")


class SpecError(ValueError):
    """规格不合法（坐标越界、洞口重叠等）。"""


def _point(obj: Any, key: str, where: str) -> List[float]:
    value = obj.get(key)
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise SpecError("%s.%s 必须是 [x, y]" % (where, key))
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        raise SpecError("%s.%s 必须是数字坐标" % (where, key))
    for name, v in (("x", x), ("y", y)):
        if not math.isfinite(v):
            raise SpecError("%s.%s 的 %s 不是有限数" % (where, key, name))
    return [x, y]


def _positive(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SpecError("%s 必须是数字" % where)
    if not math.isfinite(number) or number <= 0:
        raise SpecError("%s 必须大于 0（当前 %s）" % (where, value))
    return number


def _number(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SpecError("%s 必须是数字" % where)
    if not math.isfinite(number):
        raise SpecError("%s 不是有限数" % where)
    return number


def _call(tool: str, **args: Any) -> Dict[str, Any]:
    return {"tool": tool, "args": args}


def _xy(x: float, y: float) -> List[float]:
    return [round(x, 3), round(y, 3)]


def _opening(op: Dict[str, Any], where: str, length: float) -> Dict[str, Any]:
    kind = str(op.get("kind", "opening")).strip().lower()
    if kind not in KINDS:
        raise SpecError("%s.kind 只能是 door / window / opening（当前 %s）" % (where, op.get("kind")))

    width = _positive(op.get("width"), where + ".width")
    if "center" in op:
        center = _number(op["center"], where + ".center")
        start, end = center - width / 2, center + width / 2
    elif "offset" in op:
        start = _number(op["offset"], where + ".offset")
        end = start + width
    elif "start" in op and "end" in op:
        start = _number(op["start"], where + ".start")
        end = _number(op["end"], where + ".end")
        if end - start <= 0:
            raise SpecError("%s: end 必须大于 start" % where)
    else:
        raise SpecError("%s: 需要 center 或 offset（距墙起点的距离）" % where)

    if start < -1e-6 or end > length + 1e-6:
        raise SpecError("%s: 洞口 %.1f~%.1f 超出墙范围 0~%.1f" % (where, start, end, length))

    start, end = max(0.0, start), min(length, end)
    hinge = str(op.get("hinge", "start")).strip().lower()
    if hinge not in ("start", "end"):
        raise SpecError("%s.hinge 只能是 start / end" % where)
    swing = str(op.get("swing", "positive")).strip().lower()
    if swing not in SWING_UP + SWING_DOWN:
        raise SpecError("%s.swing 只能是 positive / negative" % where)

    review = op.get("needs_review")
    reason = op.get("review_reason")
    result = {
        "kind": kind,
        "start": start,
        "end": end,
        "width": end - start,
        "hinge": hinge,
        "swing": 1.0 if swing in SWING_UP else -1.0,
    }
    if review:
        result["needs_review"] = True
        result["review_reason"] = str(reason or "识别结果待确认")
    return result


def plan_wall(wall: Dict[str, Any], where: str, layers: Dict[str, str],
              extend_ends: bool, warnings: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """把一段墙（含洞口）换算成按 phase 分组的工具调用。"""
    a = _point(wall, "from", where)
    b = _point(wall, "to", where)
    thickness = _positive(wall.get("thickness"), where + ".thickness")

    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        raise SpecError("%s: from 与 to 重合，无法确定墙长" % where)
    ux, uy = dx / length, dy / length          # 沿墙方向
    nx, ny = -uy, ux                           # 左法线（swing=positive 一侧）

    extend_start = bool(wall.get("extend_start", extend_ends))
    extend_end = bool(wall.get("extend_end", extend_ends))
    if not (80 <= thickness <= 500):
        warnings.append("%s: 墙厚 %.0f 不在常见 80~500mm 区间，确认一下单位" % (where, thickness))

    raw_openings = wall.get("openings") or []
    if not isinstance(raw_openings, list):
        raise SpecError("%s.openings 必须是数组" % where)
    openings = [_opening(op, "%s.openings[%d]" % (where, i), length)
                for i, op in enumerate(raw_openings)]
    openings.sort(key=lambda o: o["start"])
    for prev, nxt in zip(openings, openings[1:]):
        if nxt["start"] < prev["end"] - 1e-6:
            raise SpecError("%s: 洞口 %.1f~%.1f 与 %.1f~%.1f 重叠"
                            % (where, prev["start"], prev["end"], nxt["start"], nxt["end"]))

    def along(distance: float, offset: float = 0.0) -> List[float]:
        return _xy(a[0] + ux * distance + nx * offset, a[1] + uy * distance + ny * offset)

    def angle(direction: List[float]) -> float:
        return round(math.degrees(math.atan2(direction[1], direction[0])), 3)

    wall_calls: List[Dict[str, Any]] = []
    door_calls: List[Dict[str, Any]] = []
    window_calls: List[Dict[str, Any]] = []

    # --- 墙身：洞口之间的实体墙段，每段一个闭合多段线 ---
    spans: List[List[float]] = []
    cursor = 0.0
    for op in openings:
        spans.append([cursor, op["start"]])
        cursor = op["end"]
    spans.append([cursor, length])

    half = thickness / 2
    for start, end in spans:
        if end - start <= 1e-6:
            continue
        seg_start = start - (half if (start <= 1e-6 and extend_start) else 0.0)
        seg_end = end + (half if (end >= length - 1e-6 and extend_end) else 0.0)
        wall_calls.append(_call(
            "draw_polyline",
            points=[along(seg_start, half), along(seg_end, half),
                    along(seg_end, -half), along(seg_start, -half)],
            closed=True,
            layer=layers["wall"],
        ))

    # --- 洞口：封边线 + 门扇弧 / 窗三线 ---
    for op in openings:
        for edge in (op["start"], op["end"]):
            wall_calls.append(_call(
                "draw_line",
                start_point=along(edge, half),
                end_point=along(edge, -half),
                layer=layers["wall"],
            ))

        width = op["width"]
        if op["kind"] == "door":
            if not (600 <= width <= 6000):
                warnings.append("%s: 门宽 %.0f 不像常规门，确认一下" % (where, width))
            hinge_at = op["start"] if op["hinge"] == "start" else op["end"]
            hinge = along(hinge_at)                       # 合页点在墙中心线上
            open_dir = [nx * op["swing"], ny * op["swing"]]
            leaf_end = _xy(hinge[0] + open_dir[0] * width, hinge[1] + open_dir[1] * width)
            closed_dir = [ux, uy] if op["hinge"] == "start" else [-ux, -uy]

            start_angle, end_angle = angle(closed_dir), angle(open_dir)
            if abs((end_angle - start_angle) % 360 - 90) > 1e-6:
                start_angle, end_angle = end_angle, start_angle
            if abs((end_angle - start_angle) % 360 - 90) > 1e-6:
                raise SpecError("%s: 该合页/开启方向的弧不是 90°" % where)

            door_calls.append(_call("draw_line", start_point=hinge, end_point=leaf_end,
                                    layer=layers["door"]))
            door_calls.append(_call("draw_arc", center=hinge, radius=round(width, 3),
                                    start_angle=start_angle, end_angle=end_angle,
                                    layer=layers["door"]))
        elif op["kind"] == "window":
            if not (300 <= width <= 9000):
                warnings.append("%s: 窗宽 %.0f 不像常规窗，确认一下" % (where, width))
            for offset in (half, -half, 0.0):
                window_calls.append(_call(
                    "draw_line",
                    start_point=along(op["start"], offset),
                    end_point=along(op["end"], offset),
                    layer=layers["window"],
                ))

    return {
        "wall": wall_calls,
        "door": door_calls,
        "window": window_calls,
        # 供跨墙检查使用（不参与输出）
        "meta": {
            "id": wall.get("id"),
            "origin": a,
            "direction": [ux, uy],
            "length": length,
            "thickness": thickness,
            "openings": openings,
        },
    }


def check_crossings(metas: List[Dict[str, Any]], warnings: List[str]) -> None:
    """检查洞口离交叉墙太近的情况（会画出难看的门垛，甚至被墙切断）

    两墙交叉处，洞口边缘到交叉墙中心线至少要留 两墙半厚之和 的净距，
    否则门/窗紧贴墙角：CAD 里是合法几何，制图上属于错误做法。
    """
    for meta in metas:
        ax, ay = meta["origin"]
        ux, uy = meta["direction"]
        length, thickness = meta["length"], meta["thickness"]
        label = meta["id"] or "墙"
        for op in meta["openings"]:
            start, end = op["start"], op["end"]
            for other in metas:
                if other is meta:
                    continue
                if abs(other["direction"][0] * ux + other["direction"][1] * uy) > 0.99:
                    continue  # 平行墙不构成洞口阻挡
                vx, vy = other["direction"]
                det = ux * vy - uy * vx
                if abs(det) < 1e-9:
                    continue
                dx, dy = other["origin"][0] - ax, other["origin"][1] - ay
                at = (dx * vy - dy * vx) / det
                cross = (dx * uy - dy * ux) / det
                if not (0 <= at <= length and 0 <= cross <= other["length"]):
                    continue
                clearance = (thickness + other["thickness"]) / 2
                if start - clearance < at < end + clearance:
                    gap = min(abs(at - start), abs(at - end))
                    warnings.append(
                        "%s 上 %s 洞口 %.0f~%.0f 距交叉墙 %s 中心线仅 %.0fmm"
                        "（建议至少 %.0fmm）" %
                        (label, op["kind"], start, end, other["id"] or "墙", gap, clearance)
                    )


def _unique(values: List[Any], tol: float = 1e-6) -> List[float]:
    ordered = sorted(float(value) for value in values)
    result: List[float] = []
    for value in ordered:
        if not result or value - result[-1] > tol:
            result.append(value)
    return result


def _axis_label(index: int, letters: bool) -> str:
    if not letters:
        return str(index + 1)
    name = ""
    number = index
    while True:
        name = chr(ord("A") + number % 26) + name
        number = number // 26 - 1
        if number < 0:
            return name


def _scale_spec(spec: Dict[str, Any], scale: float) -> Dict[str, Any]:
    """把整份规格从米换到毫米（墙、轴网、尺寸、楼梯一起换，避免单位混用）。"""
    if scale == 1.0:
        return spec
    if not isinstance(spec, dict):
        raise SpecError("输入必须是 JSON 对象")

    out = dict(spec)
    walls: List[Dict[str, Any]] = []
    for i, wall in enumerate(spec.get("walls") or []):
        if not isinstance(wall, dict):
            raise SpecError("walls[%d] 必须是对象" % i)
        scaled_wall = dict(wall)
        for key in ("from", "to"):
            point = _point(wall, key, "walls[%d]" % i)
            scaled_wall[key] = [coordinate * scale for coordinate in point]
        scaled_wall["thickness"] = _number(
            wall.get("thickness"), "walls[%d].thickness" % i) * scale
        openings = []
        for j, opening in enumerate(wall.get("openings") or []):
            if not isinstance(opening, dict):
                raise SpecError("walls[%d].openings[%d] 必须是对象" % (i, j))
            scaled_opening = dict(opening)
            for key in ("width", "center", "offset", "start", "end"):
                if key in scaled_opening:
                    scaled_opening[key] = _number(
                        opening[key], "walls[%d].openings[%d].%s" % (i, j, key)) * scale
            openings.append(scaled_opening)
        scaled_wall["openings"] = openings
        walls.append(scaled_wall)
    out["walls"] = walls

    axis = spec.get("axis")
    if isinstance(axis, dict):
        scaled_axis = dict(axis)
        for key in ("x", "y"):
            if isinstance(scaled_axis.get(key), list):
                scaled_axis[key] = [_number(value, "axis.%s" % key) * scale
                                    for value in scaled_axis[key]]
        for key in ("extend", "bubble_radius", "text_height"):
            if key in scaled_axis:
                scaled_axis[key] = _number(scaled_axis[key], "axis." + key) * scale
        out["axis"] = scaled_axis

    dims = spec.get("dims")
    if isinstance(dims, dict):
        scaled_dims = dict(dims)
        for key in ("offset", "textheight", "text_height", "overall_offset"):
            if key in scaled_dims:
                scaled_dims[key] = _number(scaled_dims[key], "dims." + key) * scale
        for key in ("x", "y"):
            if isinstance(scaled_dims.get(key), list):
                scaled_dims[key] = [_number(value, "dims." + key) * scale
                                    for value in scaled_dims[key]]
        out["dims"] = scaled_dims

    stairs = []
    for i, stair in enumerate(spec.get("stairs") or []):
        if not isinstance(stair, dict):
            raise SpecError("stairs[%d] 必须是对象" % i)
        scaled_stair = dict(stair)
        if "origin" in stair:
            origin = _point(stair, "origin", "stairs[%d]" % i)
            scaled_stair["origin"] = [coordinate * scale for coordinate in origin]
        for key in ("tread", "width", "text_height"):
            if key in scaled_stair:
                scaled_stair[key] = _number(scaled_stair[key], "stairs[%d].%s" % (i, key)) * scale
        stairs.append(scaled_stair)
    if stairs or "stairs" in spec:
        out["stairs"] = stairs
    return out


def plan_bbox(metas: List[Dict[str, Any]]) -> tuple:
    """墙中心线 + 墙厚的整体外接框（mm）。"""
    xs: List[float] = []
    ys: List[float] = []
    for meta in metas:
        x, y = meta["origin"]
        ux, uy = meta["direction"]
        length = meta["length"]
        half = meta["thickness"] / 2
        for px, py in ((x, y), (x + ux * length, y + uy * length)):
            xs.extend((px - half, px + half))
            ys.extend((py - half, py + half))
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def wall_axis_positions(metas: List[Dict[str, Any]]) -> tuple:
    """从墙中心线推出轴网位置：竖墙给 x，横墙给 y。"""
    xs: List[float] = []
    ys: List[float] = []
    for meta in metas:
        ux, uy = meta["direction"]
        if abs(ux) <= 1e-6:
            xs.append(meta["origin"][0])
        if abs(uy) <= 1e-6:
            ys.append(meta["origin"][1])
    return _unique(xs), _unique(ys)


def grid_calls(axis: Dict[str, Any] | None, metas: List[Dict[str, Any]],
               layer: str) -> List[Dict[str, Any]]:
    """轴网：轴线 + 单端轴号圆圈 + 轴号文字。"""
    config: Dict[str, Any] = axis if isinstance(axis, dict) else {}
    auto = bool(config.get("auto", True))
    auto_x, auto_y = wall_axis_positions(metas)

    def positions(key: str) -> List[float]:
        raw = config.get(key)
        if isinstance(raw, list) and raw:
            return _unique([_number(value, "axis.%s" % key) for value in raw])
        return auto_x if (key == "x" and auto) else (auto_y if (key == "y" and auto) else [])

    xs, ys = positions("x"), positions("y")
    if not xs and not ys:
        return []

    x0, y0, x1, y1 = plan_bbox(metas)
    extend = _number(config.get("extend", 1500), "axis.extend")
    if extend < 0:
        raise SpecError("axis.extend 不能为负")
    bubble = _positive(config.get("bubble_radius", 400), "axis.bubble_radius")
    text_height = _positive(config.get("text_height", 300), "axis.text_height")
    labels = config.get("labels") if isinstance(config.get("labels"), dict) else {}
    x_labels = labels.get("x") if isinstance(labels.get("x"), list) else None
    y_labels = labels.get("y") if isinstance(labels.get("y"), list) else None

    calls: List[Dict[str, Any]] = []
    for index, x in enumerate(xs):
        calls.append(_call("draw_line", start_point=_xy(x, y0 - extend),
                           end_point=_xy(x, y1 + extend), layer=layer))
        cy = y0 - extend - bubble
        calls.append(_call("draw_circle", center=_xy(x, cy), radius=round(bubble, 3),
                           layer=layer))
        label = str(x_labels[index]) if x_labels and index < len(x_labels) else _axis_label(index, False)
        calls.append(_call("draw_text", position=_xy(x - 0.18 * text_height, cy - text_height / 2),
                           text=label, height=round(text_height, 3), layer=layer))
    for index, y in enumerate(ys):
        calls.append(_call("draw_line", start_point=_xy(x0 - extend, y),
                           end_point=_xy(x1 + extend, y), layer=layer))
        cx = x0 - extend - bubble
        calls.append(_call("draw_circle", center=_xy(cx, y), radius=round(bubble, 3),
                           layer=layer))
        label = str(y_labels[index]) if y_labels and index < len(y_labels) else _axis_label(index, True)
        calls.append(_call("draw_text", position=_xy(cx - 0.18 * text_height, y - text_height / 2),
                           text=label, height=round(text_height, 3), layer=layer))
    return calls


def dimension_calls(dims: Dict[str, Any] | None, metas: List[Dict[str, Any]],
                    layer: str) -> List[Dict[str, Any]]:
    """按墙中心线成链标注；默认给每段 + 一道总尺寸。"""
    config: Dict[str, Any] = dims if isinstance(dims, dict) else {}
    x0, y0, x1, y1 = plan_bbox(metas)
    auto_x, auto_y = wall_axis_positions(metas)

    def positions(key: str, fallback: List[float]) -> List[float]:
        raw = config.get(key)
        if isinstance(raw, list) and raw:
            return _unique([_number(value, "dims.%s" % key) for value in raw])
        values = auto_x if key == "x" else auto_y
        return values if len(values) >= 2 else fallback

    xs = positions("x", [x0, x1])
    ys = positions("y", [y0, y1])
    offset = _positive(config.get("offset", 1200), "dims.offset")
    text_height = _positive(config.get("textheight", config.get("text_height", 250)),
                            "dims.textheight")
    overall = bool(config.get("overall", True))
    extra = _number(config.get("overall_offset", 1000), "dims.overall_offset")
    sides = str(config.get("sides", "xy"))
    calls: List[Dict[str, Any]] = []

    def add_pair(first: float, second: float, base: float, horizontal: bool,
                 lift: float) -> None:
        if abs(second - first) <= 1e-6:
            return
        if horizontal:
            calls.append(_call("add_dimension",
                               start_point=_xy(first, base), end_point=_xy(second, base),
                               text_position=_xy((first + second) / 2, base - lift),
                               textheight=round(text_height, 3), layer=layer))
        else:
            calls.append(_call("add_dimension",
                               start_point=_xy(base, first), end_point=_xy(base, second),
                               text_position=_xy(base - lift, (first + second) / 2),
                               textheight=round(text_height, 3), layer=layer))

    if "x" in sides:
        base = y0 - offset
        for first, second in zip(xs, xs[1:]):
            add_pair(first, second, base, True, text_height * 1.4)
        if overall and len(xs) >= 2:
            add_pair(xs[0], xs[-1], base - extra, True, text_height * 1.4)
    if "y" in sides:
        base = x0 - offset
        for first, second in zip(ys, ys[1:]):
            add_pair(first, second, base, False, text_height * 1.4)
        if overall and len(ys) >= 2:
            add_pair(ys[0], ys[-1], base - extra, False, text_height * 1.4)
    return calls


def stair_calls(stairs: List[Dict[str, Any]], layer: str) -> List[Dict[str, Any]]:
    """直跑楼梯：等间距踏步线 + 走向箭头 + 文字。"""
    calls: List[Dict[str, Any]] = []
    for i, stair in enumerate(stairs):
        where = "stairs[%d]" % i
        if not isinstance(stair, dict):
            raise SpecError("%s 必须是对象" % where)
        origin = _point(stair, "origin", where)
        angle = _number(stair.get("angle", 0), where + ".angle")
        raw_steps = _number(stair.get("steps", 12), where + ".steps")
        steps = int(round(raw_steps))
        if abs(raw_steps - steps) > 1e-6:
            raise SpecError("%s.steps 必须是整数（当前 %s）" % (where, raw_steps))
        tread = _positive(stair.get("tread", 260), where + ".tread")
        width = _positive(stair.get("width", 1200), where + ".width")
        text_height = _positive(stair.get("text_height", 250), where + ".text_height")
        if not 2 <= steps <= 60:
            raise SpecError("%s.steps 应在 2~60（当前 %d）" % (where, steps))
        if not 100 <= tread <= 400:
            raise SpecError("%s.tread 应在 100~400mm（当前 %.0f）" % (where, tread))
        if not 600 <= width <= 4000:
            raise SpecError("%s.width 应在 600~4000mm（当前 %.0f）" % (where, width))

        ux, uy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        nx, ny = -uy, ux

        def at(distance: float, offset: float) -> List[float]:
            return _xy(origin[0] + ux * distance + nx * offset,
                       origin[1] + uy * distance + ny * offset)

        for step in range(steps + 1):
            calls.append(_call("draw_line", start_point=at(step * tread, -width / 2),
                               end_point=at(step * tread, width / 2), layer=layer))

        run = steps * tread
        start = at(tread / 2, 0)
        end = at(run - tread / 2, 0)
        calls.append(_call("draw_line", start_point=start, end_point=end, layer=layer))
        head = 220.0
        for side in (1.0, -1.0):
            tip = at(run - tread / 2 - head, side * head * 0.45)
            calls.append(_call("draw_line", start_point=end, end_point=tip, layer=layer))
        calls.append(_call("draw_text",
                           position=at(run / 2, width / 2 + text_height * 0.8),
                           text=str(stair.get("label", "楼梯")),
                           height=round(text_height, 3), layer=layer))
    return calls


def opening_frames(spec: Dict[str, Any], scale: float = 1.0) -> List[Dict[str, Any]]:
    """洞口在图纸坐标里的定位信息（单开图块、图例、复核都用得上）。"""
    spec = _scale_spec(spec, scale)
    frames: List[Dict[str, Any]] = []
    for i, wall in enumerate(spec.get("walls") or []):
        where = "walls[%d]" % i
        a = _point(wall, "from", where)
        b = _point(wall, "to", where)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            raise SpecError("%s: from 与 to 重合，无法确定墙长" % where)
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        thickness = _positive(wall.get("thickness"), where + ".thickness")
        for j, raw in enumerate(wall.get("openings") or []):
            op = _opening(raw, "%s.openings[%d]" % (where, j), length)
            start = [round(a[0] + ux * op["start"], 3), round(a[1] + uy * op["start"], 3)]
            end = [round(a[0] + ux * op["end"], 3), round(a[1] + uy * op["end"], 3)]
            hinge_at = op["start"] if op["hinge"] == "start" else op["end"]
            hinge = [round(a[0] + ux * hinge_at, 3), round(a[1] + uy * hinge_at, 3)]
            frames.append({
                "wall": wall.get("id") or "walls[%d]" % i,
                "index": j,
                "kind": op["kind"],
                "width": op["width"],
                "thickness": thickness,
                "start": start,
                "end": end,
                "center": [round((start[0] + end[0]) / 2, 3),
                           round((start[1] + end[1]) / 2, 3)],
                "hinge": op["hinge"],
                "swing": "positive" if op["swing"] > 0 else "negative",
                "hinge_point": hinge,
                "direction": [round(ux, 9), round(uy, 9)],
                "normal": [round(nx, 9), round(ny, 9)],
                "angle": round(math.degrees(math.atan2(uy, ux)), 3),
                "needs_review": bool(op.get("needs_review")),
                "review_reason": op.get("review_reason"),
            })
    return frames


def build_plan(spec: Dict[str, Any], layers: Dict[str, str], extend_ends: bool = True,
               scale: float = 1.0) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise SpecError("输入必须是 JSON 对象")
    spec = _scale_spec(spec, scale)
    walls = spec.get("walls")
    if not isinstance(walls, list) or not walls:
        raise SpecError("输入需要非空的 walls 数组")
    layers = {key: layers.get(key, value) for key, value in DEFAULT_LAYERS.items()}

    warnings: List[str] = []
    metas: List[Dict[str, Any]] = []
    buckets: Dict[str, List[Dict[str, Any]]] = {key: [] for key in PHASE_ORDER}
    opening_count = 0

    for i, wall in enumerate(walls):
        if not isinstance(wall, dict):
            raise SpecError("walls[%d] 必须是对象" % i)
        result = plan_wall(wall, "walls[%d]" % i, layers, extend_ends, warnings)
        metas.append(result.pop("meta"))
        for key, calls in result.items():
            if calls:
                buckets[key].extend(calls)
        opening_count += len(wall.get("openings") or [])

    # 跨墙检查：洞口贴墙角是制图错误（几何合法但画出来难用），逐条告警
    check_crossings(metas, warnings)

    if spec.get("axis") not in (None, False):
        buckets["grid"] = grid_calls(spec.get("axis"), metas, layers["grid"])
    if spec.get("dims") not in (None, False):
        buckets["dim"] = dimension_calls(spec.get("dims"), metas, layers["dim"])
    if spec.get("stairs"):
        buckets["stair"] = stair_calls(spec.get("stairs"), layers["stair"])

    names = {"wall": "墙体", "door": "门", "window": "窗",
             "grid": "轴网", "dim": "尺寸", "stair": "楼梯"}
    phases = [{"key": key, "name": "%d. %s（%s）" % (index + 1, names[key], layers[key]),
               "calls": buckets[key]}
              for index, key in enumerate(PHASE_ORDER)]
    calls = [c for phase in phases for c in phase["calls"]]
    wall_segments = sum(1 for c in buckets["wall"] if c["tool"] == "draw_polyline")
    reviews = []
    for i, meta in enumerate(metas):
        for j, opening in enumerate(meta["openings"]):
            if opening.get("needs_review"):
                reviews.append({
                    "wall": meta.get("id") or "walls[%d]" % i,
                    "opening": j,
                    "kind": opening["kind"],
                    "reason": opening.get("review_reason", "识别结果待确认"),
                })

    return {
        "unit": "mm",
        "layers": layers,
        "phases": [p for p in phases if p["calls"]],
        "calls": calls,
        "summary": {
            "walls": len(walls),
            "wall_segments": wall_segments,
            "openings": opening_count,
            "calls": len(calls),
            "scaled_from": None if scale == 1.0 else ("m" if scale == 1000.0 else "x%.6g" % scale),
            "grid_calls": len(buckets["grid"]),
            "dim_calls": len(buckets["dim"]),
            "stair_calls": len(buckets["stair"]),
            "needs_review": len(reviews),
        },
        "review": reviews,
        "warnings": warnings,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="直墙 + 洞口 → CAD 调用序列（输出单位 mm）")
    parser.add_argument("spec", help="规格 JSON 文件路径，或 - 表示从 stdin 读")
    parser.add_argument("--unit", default="mm", choices=("mm", "m"),
                        help="输入坐标单位（默认 mm；m 会换算成 mm 输出）")
    parser.add_argument("--wall-layer", default=DEFAULT_LAYERS["wall"])
    parser.add_argument("--door-layer", default=DEFAULT_LAYERS["door"])
    parser.add_argument("--window-layer", default=DEFAULT_LAYERS["window"])
    parser.add_argument("--grid-layer", default=DEFAULT_LAYERS["grid"])
    parser.add_argument("--dim-layer", default=DEFAULT_LAYERS["dim"])
    parser.add_argument("--stair-layer", default=DEFAULT_LAYERS["stair"])
    parser.add_argument("--no-extend", action="store_true", help="墙端不按半墙厚延伸（默认延伸）")
    parser.add_argument("--only", default="",
                        help="只输出指定 phase，逗号分隔：" + ",".join(PHASE_ORDER))
    parser.add_argument("--out", help="写入文件（默认打印到 stdout）")
    parser.add_argument("--pretty", action="store_true", help="缩进输出")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if args.spec == "-" else open(args.spec, encoding="utf-8").read()
    except OSError as exc:
        print("读取规格失败: %s" % exc, file=sys.stderr)
        return 1

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("规格不是合法 JSON: %s" % exc, file=sys.stderr)
        return 2

    layers = {"wall": args.wall_layer, "door": args.door_layer, "window": args.window_layer,
              "grid": args.grid_layer, "dim": args.dim_layer, "stair": args.stair_layer}
    try:
        plan = build_plan(spec, layers, extend_ends=not args.no_extend,
                          scale=1000.0 if args.unit == "m" else 1.0)
    except SpecError as exc:
        print("规格错误: %s" % exc, file=sys.stderr)
        return 2

    wanted = [name.strip() for name in args.only.split(",") if name.strip()]
    if wanted:
        unknown = [name for name in wanted if name not in PHASE_ORDER]
        if unknown:
            print("--only 只接受 %s（收到 %s）"
                  % (",".join(PHASE_ORDER), ",".join(unknown)), file=sys.stderr)
            return 2
        plan["phases"] = [p for p in plan["phases"] if p["key"] in wanted]
        plan["calls"] = [c for phase in plan["phases"] for c in phase["calls"]]

    text = json.dumps(plan, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError as exc:
            print("写入失败: %s" % exc, file=sys.stderr)
            return 1
        print("已写入 %s（%d 个调用）" % (args.out, len(plan["calls"])))
    else:
        print(text)

    for warning in plan["warnings"]:
        print("警告: %s" % warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
