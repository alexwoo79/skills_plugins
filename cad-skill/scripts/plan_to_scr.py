#!/usr/bin/env python3
"""把 CAD-MCP 调用序列编译成 AutoCAD 脚本（.scr），用于批量落图。

逐条调用 MCP 工具 = 每个图元一次跨进程 COM 往返；同一个几何量级改成
一次 SCRIPT 执行，CAD 在进程内自己跑命令，快一个量级。

输入：`wall_plan.py` 的输出（带 calls 的对象），或直接的 calls 数组。
输出：可在 CAD 里执行的 .scr 文件。

用法：
  python3 wall_plan.py plan.json | python3 plan_to_scr.py - --out plan.scr
  python3 plan_to_scr.py calls.json --out plan.scr --dimtxt 250

执行方式（Windows 侧）：
  1) 把 .scr 拷到装 CAD 的机器；
  2) 在 CAD 里手动 `SCRIPT` 选文件，或经 MCP 调用
     send_command("_.SCRIPT C:\\\\path\\\\plan.scr")。

未支持：draw_hatch（填充的交互式选择集无法可靠脚本化），会跳过并告警。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Dict, List, Tuple

SUPPORTED = ("draw_line", "draw_polyline", "draw_rectangle", "draw_circle",
             "draw_arc", "draw_ellipse", "draw_text", "add_dimension")


class PlanError(ValueError):
    """调用序列不合法。"""


def _num(value: Any, where: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PlanError("%s 不是数字：%r" % (where, value))
    if not math.isfinite(number):
        raise PlanError("%s 不是有限数：%r" % (where, value))
    return number


def _fmt(value: float) -> str:
    text = "%.3f" % value
    text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _point(value: Any, where: str) -> Tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise PlanError("%s 必须是 [x, y]" % where)
    return _num(value[0], where + "[0]"), _num(value[1], where + "[1]")


def _xy(value: Any, where: str) -> str:
    x, y = _point(value, where)
    return "%s,%s" % (_fmt(x), _fmt(y))


def _pos(value: Any, where: str) -> float:
    number = _num(value, where)
    if number <= 0:
        raise PlanError("%s 必须大于 0（当前 %s）" % (where, value))
    return number


def _text_token(text: str, keep_spaces: bool, warnings: List[str], space_char: str) -> str:
    if " " in text or "\t" in text:
        if keep_spaces:
            warnings.append("文本 %r 含空格：命令行里空格等于回车，可能被截断" % text)
        else:
            warnings.append("文本 %r 的空格已用无间断空格替代（--keep-spaces 可保留原样）" % text)
            return text.replace(" ", space_char).replace("\t", space_char)
    return text


def compile_call(call: Dict[str, Any], index: int, keep_spaces: bool,
                 warnings: List[str], space_char: str = "\u00a0") -> List[str]:
    """把单个工具调用翻译成 .scr 命令序列。"""
    where = "calls[%d]" % index
    tool = call.get("tool")
    args = call.get("args") or {}
    if not isinstance(args, dict):
        raise PlanError("%s.args 必须是对象" % where)
    if tool not in SUPPORTED:
        warnings.append("%s: 跳过不支持的 %s（填充等请用 MCP 工具单独处理）" % (where, tool))
        return []

    if tool == "draw_line":
        return ["_.LINE", _xy(args.get("start_point"), where + ".start_point"),
                _xy(args.get("end_point"), where + ".end_point"), ""]

    if tool in ("draw_polyline", "draw_rectangle"):
        if tool == "draw_rectangle":
            c1 = _point(args.get("corner1"), where + ".corner1")
            c2 = _point(args.get("corner2"), where + ".corner2")
            points = [c1, (c2[0], c1[1]), c2, (c1[0], c2[1])]
            closed = True
        else:
            raw = args.get("points")
            if not isinstance(raw, list) or len(raw) < 2:
                raise PlanError("%s.points 至少需要 2 个点" % where)
            points = [_point(p, "%s.points[%d]" % (where, i)) for i, p in enumerate(raw)]
            closed = bool(args.get("closed"))
        tokens = ["_.PLINE"] + ["%s,%s" % (_fmt(x), _fmt(y)) for x, y in points]
        tokens.append("C" if (closed and len(points) > 2) else "")
        return tokens

    if tool == "draw_circle":
        return ["_.CIRCLE", _xy(args.get("center"), where + ".center"),
                _fmt(_pos(args.get("radius"), where + ".radius"))]

    if tool == "draw_arc":
        center = _point(args.get("center"), where + ".center")
        radius = _pos(args.get("radius"), where + ".radius")
        start_angle = _num(args.get("start_angle"), where + ".start_angle")
        end_angle = _num(args.get("end_angle"), where + ".end_angle")
        included = (end_angle - start_angle) % 360 or 360.0
        start_point = (center[0] + radius * math.cos(math.radians(start_angle)),
                       center[1] + radius * math.sin(math.radians(start_angle)))
        return ["_.ARC", "C", "%s,%s" % (_fmt(center[0]), _fmt(center[1])),
                "%s,%s" % (_fmt(start_point[0]), _fmt(start_point[1])),
                "A", _fmt(included)]

    if tool == "draw_ellipse":
        center = _point(args.get("center"), where + ".center")
        major = _pos(args.get("major_axis"), where + ".major_axis")
        minor = _pos(args.get("minor_axis"), where + ".minor_axis")
        if minor > major:
            raise PlanError("%s: minor_axis 不能大于 major_axis" % where)
        rotation = _num(args.get("rotation", 0), where + ".rotation")
        endpoint = (center[0] + major * math.cos(math.radians(rotation)),
                    center[1] + major * math.sin(math.radians(rotation)))
        return ["_.ELLIPSE", "C", "%s,%s" % (_fmt(center[0]), _fmt(center[1])),
                "%s,%s" % (_fmt(endpoint[0]), _fmt(endpoint[1])), _fmt(minor)]

    if tool == "draw_text":
        height = _pos(args.get("height", 2.5), where + ".height")
        rotation = _num(args.get("rotation", 0), where + ".rotation")
        text = str(args.get("text", ""))
        if not text:
            raise PlanError("%s.text 不能为空" % where)
        return ["_.-TEXT", _xy(args.get("position"), where + ".position"),
                _fmt(height), _fmt(rotation), _text_token(text, keep_spaces, warnings, space_char)]

    # add_dimension
    start = _point(args.get("start_point"), where + ".start_point")
    end = _point(args.get("end_point"), where + ".end_point")
    text_position = args.get("text_position")
    if text_position is None:
        # 与 CAD-MCP 的默认一致：放在中点上方 5
        mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + 5)
    else:
        mid = _point(text_position, where + ".text_position")
    return ["_.DIMLINEAR",
            "%s,%s" % (_fmt(start[0]), _fmt(start[1])),
            "%s,%s" % (_fmt(end[0]), _fmt(end[1])),
            "%s,%s" % (_fmt(mid[0]), _fmt(mid[1]))]


def build_scr(calls: List[Dict[str, Any]], dimtxt: float | None = None, zoom: bool = True,
              undo_group: bool = True, comments: bool = True, keep_spaces: bool = False,
              space_char: str = "\u00a0",
              ) -> Tuple[List[str], List[str], Dict[str, Any]]:
    warnings: List[str] = []
    lines: List[str] = ["; 由 cad-skill/scripts/plan_to_scr.py 生成：批量落图脚本",
                        "; 单位 mm；执行方式：CAD 里 SCRIPT 选本文件，或 send_command(\"_.SCRIPT <绝对路径>\")"]
    if undo_group:
        lines += ["; 整批作为一个撤销组；脚本中途失败时在命令行执行 UNDO END 收尾",
                  "_.UNDO", "BEGIN"]

    has_dimension = any(c.get("tool") == "add_dimension" for c in calls)
    if has_dimension and dimtxt:
        lines += ["DIMTXT", _fmt(float(dimtxt))]

    current_layer = None
    layer_order: List[str] = []
    counts: Dict[str, int] = {}
    kept = 0

    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            raise PlanError("calls[%d] 必须是对象" % index)
        args = call.get("args") or {}
        layer = args.get("layer") if isinstance(args, dict) else None
        tokens = compile_call(call, index, keep_spaces, warnings, space_char)
        if not tokens:
            continue
        if layer and layer != current_layer:
            lines += ["", "_.-LAYER", "M", str(layer), ""]
            current_layer = str(layer)
            if current_layer not in layer_order:
                layer_order.append(current_layer)
        if comments:
            lines.append("; %s（%s）" % (call.get("tool"), current_layer or "当前层"))
        lines += tokens
        kept += 1
        counts[str(call.get("tool"))] = counts.get(str(call.get("tool")), 0) + 1

    if undo_group:
        lines += ["_.UNDO", "END"]
    if zoom:
        lines += ["_.ZOOM", "E"]

    stats = {"entities": kept, "layers": layer_order, "by_tool": counts,
             "unsupported_skipped": len(warnings)}
    return lines, warnings, stats


def _extract_calls(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        calls = payload.get("calls")
        if isinstance(calls, list) and calls:
            return calls
        phases = payload.get("phases")
        if isinstance(phases, list):
            merged: List[Dict[str, Any]] = []
            for phase in phases:
                if isinstance(phase, dict) and isinstance(phase.get("calls"), list):
                    merged.extend(phase["calls"])
            if merged:
                return merged
    raise PlanError("输入里找不到 calls 数组（先跑 wall_plan.py 或直接给 [{tool,args}, ...]）")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAD-MCP 调用序列 → AutoCAD .scr 批量落图脚本")
    parser.add_argument("plan", help="调用序列 JSON 路径，或 - 表示 stdin")
    parser.add_argument("--out", default="plan.scr", help="输出 .scr 路径（默认 plan.scr）")
    parser.add_argument("--dimtxt", type=float, default=250.0,
                        help="标注字高（mm，默认 250；写进 DIMTXT 系统变量）")
    parser.add_argument("--encoding", default="utf-8-sig",
                        choices=("utf-8-sig", "utf-8", "gbk"),
                        help="输出编码（默认 utf-8-sig；中文文字乱码时试 gbk）")
    parser.add_argument("--no-zoom", dest="zoom", action="store_false", help="脚本末尾不执行 ZOOM E")
    parser.add_argument("--no-undo-group", dest="undo_group", action="store_false",
                        help="不使用 UNDO BEGIN/END 包住整批")
    parser.add_argument("--no-comments", dest="comments", action="store_false",
                        help="不写逐条注释")
    parser.add_argument("--keep-spaces", action="store_true",
                        help="文本里的空格原样保留（默认替换为不换行空格，避免被当回车截断）")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if args.plan == "-" else open(args.plan, encoding="utf-8").read()
    except OSError as exc:
        print("读取失败: %s" % exc, file=sys.stderr)
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("输入不是合法 JSON: %s" % exc, file=sys.stderr)
        return 2

    try:
        calls = _extract_calls(payload)
        lines, warnings, stats = build_scr(calls, dimtxt=args.dimtxt, zoom=args.zoom,
                                           undo_group=args.undo_group, comments=args.comments,
                                           keep_spaces=args.keep_spaces,
                                           # GBK 里 U+00A0 无定义，用全角空格代替
                                           space_char="\u3000" if args.encoding == "gbk" else "\u00a0")
    except PlanError as exc:
        print("调用序列错误: %s" % exc, file=sys.stderr)
        return 2

    try:
        with open(args.out, "w", encoding=args.encoding, newline="\r\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except UnicodeEncodeError as exc:
        print("写入 %s 失败：%s 编码不了字符 %r，改用 --encoding utf-8-sig 或简化文字内容"
              % (args.out, args.encoding, exc.object[exc.start:exc.end]), file=sys.stderr)
        return 2
    except OSError as exc:
        print("写入 %s 失败: %s" % (args.out, exc), file=sys.stderr)
        return 1

    print("已生成 %s：%d 个图元，图层 %s"
          % (args.out, stats["entities"], "/".join(stats["layers"]) or "（未指定）"))
    for tool, count in sorted(stats["by_tool"].items()):
        print("  %-16s %d" % (tool, count))
    for warning in warnings:
        print("警告: %s" % warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
