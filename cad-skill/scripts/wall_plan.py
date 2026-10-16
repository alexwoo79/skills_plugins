#!/usr/bin/env python3
"""直墙 + 洞口 → CAD-MCP 调用序列（默认单位 mm）。

只做几何换算，不做设计决策：墙厚、洞口位置必须来自用户确认过的方案。

输入 JSON:
{
  "walls": [
    {"from": [0, 0], "to": [12000, 0], "thickness": 240,
     "openings": [
       {"kind": "door", "width": 1000, "center": 3000, "hinge": "start", "swing": "positive"},
       {"kind": "window", "width": 2400, "center": 7000}
     ]}
  ]
}

墙段：每段一个闭合多段线（矩形截面），墙两端按 半墙厚 延伸近似接角。
洞口：两侧封边线走 A-WALL；门补门扇线 + 90° 开启弧（A-DOOR）；
      窗补洞口内外墙面线 + 中位窗线（A-WINDOW）。

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

DEFAULT_LAYERS = {"wall": "A-WALL", "door": "A-DOOR", "window": "A-WINDOW"}
KINDS = ("door", "window", "opening")
SWING_UP = ("positive", "plus", "+", "left", "in", "n")
SWING_DOWN = ("negative", "minus", "-", "right", "out", "s")


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

    return {
        "kind": kind,
        "start": start,
        "end": end,
        "width": end - start,
        "hinge": hinge,
        "swing": 1.0 if swing in SWING_UP else -1.0,
    }


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


def build_plan(spec: Dict[str, Any], layers: Dict[str, str], extend_ends: bool = True,
               scale: float = 1.0) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise SpecError("输入必须是 JSON 对象")
    walls = spec.get("walls")
    if not isinstance(walls, list) or not walls:
        raise SpecError("输入需要非空的 walls 数组")

    warnings: List[str] = []
    metas: List[Dict[str, Any]] = []
    buckets: Dict[str, List[Dict[str, Any]]] = {"wall": [], "door": [], "window": []}
    opening_count = 0

    for i, wall in enumerate(walls):
        if not isinstance(wall, dict):
            raise SpecError("walls[%d] 必须是对象" % i)
        if scale != 1.0:
            wall = dict(wall)
            wall["from"] = [c * scale for c in wall["from"]]
            wall["to"] = [c * scale for c in wall["to"]]
            wall["thickness"] = _number(wall.get("thickness"), "walls[%d].thickness" % i) * scale
            scaled = []
            for op in wall.get("openings") or []:
                op = dict(op)
                for key in ("width", "center", "offset", "start", "end"):
                    if key in op:
                        op[key] = _number(op[key], "walls[%d].openings.%s" % (i, key)) * scale
                scaled.append(op)
            wall["openings"] = scaled

        result = plan_wall(wall, "walls[%d]" % i, layers, extend_ends, warnings)
        metas.append(result.pop("meta"))
        for key, calls in result.items():
            if calls:
                buckets[key].extend(calls)
        opening_count += len(wall.get("openings") or [])

    # 跨墙检查：洞口贴墙角是制图错误（几何合法但画出来难用），逐条告警
    check_crossings(metas, warnings)

    phases = [
        {"key": "wall", "name": "1. 墙体（%s）" % layers["wall"], "calls": buckets["wall"]},
        {"key": "door", "name": "2. 门（%s）" % layers["door"], "calls": buckets["door"]},
        {"key": "window", "name": "3. 窗（%s）" % layers["window"], "calls": buckets["window"]},
    ]
    calls = [c for phase in phases for c in phase["calls"]]
    wall_segments = sum(1 for c in buckets["wall"] if c["tool"] == "draw_polyline")

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
        },
        "warnings": warnings,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="直墙 + 洞口 → CAD-MCP 调用序列（输出单位 mm）")
    parser.add_argument("spec", help="规格 JSON 文件路径，或 - 表示从 stdin 读")
    parser.add_argument("--unit", default="mm", choices=("mm", "m"),
                        help="输入坐标单位（默认 mm；m 会换算成 mm 输出）")
    parser.add_argument("--wall-layer", default=DEFAULT_LAYERS["wall"])
    parser.add_argument("--door-layer", default=DEFAULT_LAYERS["door"])
    parser.add_argument("--window-layer", default=DEFAULT_LAYERS["window"])
    parser.add_argument("--no-extend", action="store_true", help="墙端不按半墙厚延伸（默认延伸）")
    parser.add_argument("--only", default="", help="只输出指定 phase，逗号分隔：wall,door,window")
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

    layers = {"wall": args.wall_layer, "door": args.door_layer, "window": args.window_layer}
    try:
        plan = build_plan(spec, layers, extend_ends=not args.no_extend,
                          scale=1000.0 if args.unit == "m" else 1.0)
    except SpecError as exc:
        print("规格错误: %s" % exc, file=sys.stderr)
        return 2

    wanted = [name.strip() for name in args.only.split(",") if name.strip()]
    if wanted:
        unknown = [name for name in wanted if name not in ("wall", "door", "window")]
        if unknown:
            print("--only 只接受 wall,door,window（收到 %s）" % ",".join(unknown), file=sys.stderr)
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
