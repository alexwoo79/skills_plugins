#!/usr/bin/env python3
"""plan.json → 调用序列 / .scr / DXF / SVG：同一份几何，多个后端。

以前同一张图有几条各写各的出口：calls 里有门窗结构，DXF 里只剩墙矩形和原始线work。
本脚本让所有后端都从 `wall_plan.build_plan()` 的同一份调用序列出发，保证
"预览看到的" 与 "落进 CAD 的" 是同一套图元。

用法：
  python3 plan_render.py plan.json --out-calls calls.json --out-dxf plan.dxf \
      --out-svg preview.svg --out-scr plan.scr

  # 门窗改成图块（DXF 后端；直连落图仍是基础图元）
  python3 plan_render.py plan.json --out-dxf plan.dxf --blocks

后端说明：
  calls  cad_direct.py 的逐图元调用序列
  scr    整批落图脚本（一次 COM 调用）
  dxf    不经过 COM，可直接 open_drawing 或交给别人打开
  svg    离线预览（浏览器就能看，不需要 CAD）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from html import escape
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import plan_to_scr  # noqa: E402
import wall_plan  # noqa: E402

LAYERS = dict(wall_plan.DEFAULT_LAYERS)
DIMSTYLE = "CADSKILL"

LAYER_STYLE = {
    "A-WALL": ("#111111", 1.6, ""),
    "A-DOOR": ("#2e7d32", 1.1, ""),
    "A-WINDOW": ("#1565c0", 1.1, ""),
    "A-GRID": ("#9e9e9e", 0.8, "6 4"),
    "A-DIM": ("#ef6c00", 0.8, ""),
    "A-TEXT": ("#333333", 0.8, ""),
    "A-FURN": ("#607d8b", 0.8, ""),
    "A-STAR": ("#6a1b9a", 1.0, ""),
    "A-TRACE": ("#bdbdbd", 0.6, ""),
    "A-ROOM": ("#90a4ae", 1.0, "4 3"),
}


def _point(value: Any) -> Tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise wall_plan.SpecError("需要 [x, y]（收到 %r）" % (value,))
    return float(value[0]), float(value[1])


def _attribs(args: Dict[str, Any]) -> Dict[str, Any]:
    layer = str(args.get("layer") or "0")
    return {"layer": layer}


def build(spec: Dict[str, Any], unit: str = "mm", extend: bool = True,
          layers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return wall_plan.build_plan(
        spec, dict(layers or LAYERS), extend_ends=extend,
        scale=1000.0 if unit == "m" else 1.0)


# ---------------------------------------------------------------- 后端：calls / scr

def write_calls(plan: Dict[str, Any], path: str) -> Dict[str, Any]:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"path": path, "calls": len(plan["calls"])}


def write_scr(plan: Dict[str, Any], path: str, dimtxt: float = 250.0,
              encoding: str = "utf-8-sig", zoom: bool = True,
              undo_group: bool = True, comments: bool = True,
              keep_spaces: bool = False) -> Dict[str, Any]:
    space = "\u3000" if encoding == "gbk" else "\u00a0"
    lines, warnings, stats = plan_to_scr.build_scr(
        plan["calls"], dimtxt=dimtxt, zoom=zoom, undo_group=undo_group,
        comments=comments, keep_spaces=keep_spaces, space_char=space)
    with open(path, "w", encoding=encoding, newline="\r\n") as handle:
        handle.write("\n".join(lines) + "\n")
    result = dict(stats)
    result.update({"path": path, "warnings": warnings})
    return result


# ---------------------------------------------------------------- 后端：DXF

def _new_doc():
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出可执行提示
        raise RuntimeError("需要 ezdxf：pip install ezdxf") from exc
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # 毫米
    for name in sorted(set(LAYERS.values()) |
                       {"A-TEXT", "A-FURN", "A-ROOM", "A-TRACE", "0"}):
        if name not in doc.layers:
            doc.layers.new(name)
    if DIMSTYLE not in doc.dimstyles:
        doc.dimstyles.new(DIMSTYLE, dxfattribs={
            "dimtxt": 250.0, "dimasz": 150.0, "dimexe": 100.0, "dimexo": 100.0,
            "dimgap": 50.0, "dimdec": 0,
        })
    return doc


def _dxf_call(model, call: Dict[str, Any]) -> bool:
    tool = call.get("tool")
    a = call.get("args") or {}
    attribs = _attribs(a)
    if tool == "draw_line":
        model.add_line(_point(a.get("start_point")), _point(a.get("end_point")),
                       dxfattribs=attribs)
        return True
    if tool in ("draw_polyline", "draw_rectangle"):
        if tool == "draw_rectangle":
            c1, c2 = _point(a.get("corner1")), _point(a.get("corner2"))
            points = [c1, (c2[0], c1[1]), c2, (c1[0], c2[1])]
            closed = True
        else:
            points = [_point(point) for point in a.get("points") or []]
            closed = bool(a.get("closed"))
        if len(points) < 2:
            raise wall_plan.SpecError("%s.points 至少需要 2 个点" % tool)
        model.add_lwpolyline(points, close=closed, dxfattribs=attribs)
        return True
    if tool == "draw_circle":
        model.add_circle(_point(a.get("center")), float(a.get("radius")), dxfattribs=attribs)
        return True
    if tool == "draw_arc":
        model.add_arc(_point(a.get("center")), float(a.get("radius")),
                      float(a.get("start_angle")), float(a.get("end_angle")),
                      dxfattribs=attribs)
        return True
    if tool == "draw_ellipse":
        center = _point(a.get("center"))
        major = float(a.get("major_axis"))
        minor = float(a.get("minor_axis"))
        rotation = math.radians(float(a.get("rotation", 0)))
        model.add_ellipse(center,
                          major_axis=(major * math.cos(rotation),
                                      major * math.sin(rotation), 0),
                          ratio=minor / major, dxfattribs=attribs)
        return True
    if tool == "draw_text":
        text = str(a.get("text", ""))
        if not text:
            raise wall_plan.SpecError("draw_text.text 不能为空")
        entity = model.add_text(text, height=float(a.get("height", 2.5)),
                                dxfattribs={**attribs,
                                            "rotation": float(a.get("rotation", 0))})
        entity.set_placement(_point(a.get("position")))
        return True
    if tool == "add_dimension":
        p1 = _point(a.get("start_point"))
        p2 = _point(a.get("end_point"))
        base = _point(a.get("text_position")) if a.get("text_position") else (
            (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 + 5)
        angle = 0.0 if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]) else 90.0
        override = {"dimtxt": float(a.get("textheight", 250))}
        model.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                             dimstyle=DIMSTYLE, override=override,
                             dxfattribs=attribs).render()
        return True
    return False


def _door_block(doc, hinge: str, swing: str) -> str:
    name = "CADSKILL_DOOR_%s_%s" % (hinge.upper(), swing.upper())
    if name in doc.blocks:
        return name
    block = doc.blocks.new(name=name, base_point=(0, 0))
    layer = LAYERS["door"]
    spans = {
        ("start", "positive"): (0.0, 90.0, (0.0, 1.0)),
        ("start", "negative"): (270.0, 360.0, (0.0, -1.0)),
        ("end", "positive"): (90.0, 180.0, (0.0, 1.0)),
        ("end", "negative"): (180.0, 270.0, (0.0, -1.0)),
    }
    start, end, leaf = spans[(hinge, swing)]
    block.add_arc((0, 0), 1.0, start, end, dxfattribs={"layer": layer})
    block.add_line((0, 0), leaf, dxfattribs={"layer": layer})
    return name


def _window_block(doc) -> str:
    name = "CADSKILL_WINDOW"
    if name in doc.blocks:
        return name
    block = doc.blocks.new(name=name, base_point=(0, 0))
    layer = LAYERS["window"]
    for y in (0.5, 0.0, -0.5):
        block.add_line((0, y), (1, y), dxfattribs={"layer": layer})
    return name


def _add_block_inserts(doc, model, spec: Dict[str, Any], unit: str) -> Dict[str, int]:
    frames = wall_plan.opening_frames(spec, 1000.0 if unit == "m" else 1.0)
    doors = windows = 0
    for frame in frames:
        if frame["kind"] == "door":
            name = _door_block(doc, frame["hinge"], frame["swing"])
            model.add_blockref(name, frame["hinge_point"], dxfattribs={
                "layer": LAYERS["door"], "rotation": frame["angle"],
                "xscale": frame["width"], "yscale": frame["width"]})
            doors += 1
        elif frame["kind"] == "window":
            name = _window_block(doc)
            model.add_blockref(name, frame["start"], dxfattribs={
                "layer": LAYERS["window"], "rotation": frame["angle"],
                "xscale": frame["width"], "yscale": frame["thickness"]})
            windows += 1
    return {"door_blocks": doors, "window_blocks": windows}


def write_dxf(spec: Dict[str, Any], plan: Dict[str, Any], path: str,
              blocks: bool = False, unit: str = "mm",
              trace_lines: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    doc = _new_doc()
    model = doc.modelspace()
    skipped = {LAYERS["door"], LAYERS["window"]} if blocks else set()
    kept = 0
    unsupported = 0
    for call in plan.get("calls") or []:
        if str((call.get("args") or {}).get("layer") or "") in skipped:
            continue
        if _dxf_call(model, call):
            kept += 1
        else:
            unsupported += 1
    stats = {"path": path, "entities": kept, "unsupported": unsupported}
    if blocks:
        stats.update(_add_block_inserts(doc, model, spec, unit))
    traced = 0
    for line in trace_lines or []:
        if isinstance(line, (list, tuple)) and len(line) >= 4:
            x1, y1, x2, y2 = line[:4]
        elif isinstance(line, dict):
            x1, y1 = line.get("x1"), line.get("y1")
            x2, y2 = line.get("x2"), line.get("y2")
        else:
            continue
        if None in (x1, y1, x2, y2):
            continue
        model.add_line((float(x1), float(y1)), (float(x2), float(y2)),
                       dxfattribs={"layer": "A-TRACE"})
        traced += 1
    if traced:
        stats["trace_lines"] = traced
    doc.saveas(path)
    stats["layers"] = sorted(layer.dxf.name for layer in doc.layers)
    return stats


# ---------------------------------------------------------------- 后端：SVG 预览

def _call_points(call: Dict[str, Any]) -> List[Tuple[float, float]]:
    tool = call.get("tool")
    a = call.get("args") or {}
    if tool == "draw_line":
        return [_point(a.get("start_point")), _point(a.get("end_point"))]
    if tool == "draw_polyline":
        return [_point(point) for point in a.get("points") or []]
    if tool == "draw_rectangle":
        c1, c2 = _point(a.get("corner1")), _point(a.get("corner2"))
        return [c1, (c2[0], c1[1]), c2, (c1[0], c2[1])]
    if tool in ("draw_circle", "draw_arc"):
        cx, cy = _point(a.get("center"))
        r = float(a.get("radius", 0))
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if tool == "draw_ellipse":
        cx, cy = _point(a.get("center"))
        r = float(a.get("major_axis", 0))
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if tool == "draw_text":
        x, y = _point(a.get("position"))
        h = float(a.get("height", 2.5))
        return [(x - 2 * h, y - h), (x + 2 * h, y + h)]
    if tool == "add_dimension":
        p1, p2 = _point(a.get("start_point")), _point(a.get("end_point"))
        base = _point(a.get("text_position")) if a.get("text_position") else p2
        return [p1, p2, base]
    return []


def _svg_bbox(plan: Dict[str, Any], frames: List[Dict[str, Any]]):
    points: List[Tuple[float, float]] = []
    for call in plan.get("calls") or []:
        points.extend(_call_points(call))
    for frame in frames:
        x, y = frame["hinge_point"]
        r = frame["width"]
        points.extend([(x - r, y - r), (x + r, y + r)])
    if not points:
        return 0.0, 0.0, 1.0, 1.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _svg_text(text: str, x: float, y: float, size: float, cls: str,
              anchor: str = "start") -> str:
    return ('<text class="%s" x="%.2f" y="%.2f" font-size="%.2f" '
            'text-anchor="%s">%s</text>' % (cls, x, y, size, anchor, escape(text)))


def write_svg(spec: Dict[str, Any], plan: Dict[str, Any], path: str,
              unit: str = "mm", max_width: int = 1400,
              title: Optional[str] = None) -> Dict[str, Any]:
    frames = wall_plan.opening_frames(spec, 1000.0 if unit == "m" else 1.0)
    min_x, min_y, max_x, max_y = _svg_bbox(plan, frames)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    margin = 0.06 * max(span_x, span_y)
    width_mm = span_x + 2 * margin
    height_mm = span_y + 2 * margin
    scale = max_width / width_mm
    width_px = max_width
    height_px = height_mm * scale

    def px(point: Tuple[float, float]) -> Tuple[float, float]:
        return ((point[0] - min_x + margin) * scale,
                (max_y + margin - point[1]) * scale)

    body: List[str] = []
    for call in plan.get("calls") or []:
        tool = call.get("tool")
        a = call.get("args") or {}
        layer = str(a.get("layer") or "0")
        cls = "l-%s" % layer.replace("-", "_")
        if tool == "draw_line":
            (x1, y1), (x2, y2) = px(_point(a.get("start_point"))), px(_point(a.get("end_point")))
            body.append('<line class="%s" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>'
                        % (cls, x1, y1, x2, y2))
        elif tool in ("draw_polyline", "draw_rectangle"):
            if tool == "draw_rectangle":
                c1, c2 = _point(a.get("corner1")), _point(a.get("corner2"))
                points = [c1, (c2[0], c1[1]), c2, (c1[0], c2[1])]
                closed = True
            else:
                points = [_point(point) for point in a.get("points") or []]
                closed = bool(a.get("closed"))
            if len(points) < 2:
                continue
            path_data = " ".join("%.2f,%.2f" % px(point) for point in points)
            tag = "polygon" if closed else "polyline"
            body.append('<%s class="%s" points="%s"/>' % (tag, cls, path_data))
        elif tool == "draw_circle":
            cx, cy = _point(a.get("center"))
            x, y = px((cx, cy))
            body.append('<circle class="%s" cx="%.2f" cy="%.2f" r="%.2f"/>'
                        % (cls, x, y, float(a.get("radius")) * scale))
        elif tool == "draw_arc":
            cx, cy = _point(a.get("center"))
            r = float(a.get("radius"))
            start = math.radians(float(a.get("start_angle")))
            end = math.radians(float(a.get("end_angle")))
            sx, sy = px((cx + r * math.cos(start), cy + r * math.sin(start)))
            ex, ey = px((cx + r * math.cos(end), cy + r * math.sin(end)))
            r_px = r * scale
            body.append('<path class="%s" d="M %.2f %.2f A %.2f %.2f 0 0 0 %.2f %.2f"/>'
                        % (cls, sx, sy, r_px, r_px, ex, ey))
        elif tool == "draw_text":
            layer_cls = cls
            x, y = px(_point(a.get("position")))
            height = max(float(a.get("height", 2.5)) * scale, 3.0)
            body.append(_svg_text(str(a.get("text", "")), x, y, height, layer_cls))
        elif tool == "add_dimension":
            p1 = _point(a.get("start_point"))
            p2 = _point(a.get("end_point"))
            base = _point(a.get("text_position")) if a.get("text_position") else p2
            if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]):
                q1, q2 = (p1[0], base[1]), (p2[0], base[1])
            else:
                q1, q2 = (base[0], p1[1]), (base[0], p2[1])
            for from_point, to_point in ((p1, q1), (p2, q2), (q1, q2)):
                (x1, y1), (x2, y2) = px(from_point), px(to_point)
                body.append('<line class="%s" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>'
                            % (cls, x1, y1, x2, y2))
            for point in (q1, q2):
                x, y = px(point)
                body.append('<circle class="%s-mark" cx="%.2f" cy="%.2f" r="1.6"/>'
                            % (cls, x, y))
            tx, ty = px(base)
            size = max(float(a.get("textheight", 250)) * scale, 7.0)
            value = "%.0f" % math.dist(p1, p2)
            body.append(_svg_text(value, tx, ty - size * 0.35, size, cls, "middle"))

    for frame in frames:
        if not frame["needs_review"]:
            continue
        x, y = px(frame["hinge_point"])
        body.append('<circle class="review" cx="%.2f" cy="%.2f" r="%.2f"/>'
                    % (x, y, max(frame["width"] * scale * 0.55, 8)))
        body.append(_svg_text("?", x, y + 5, 14.0, "review-text", "middle"))

    css = [".bg{fill:#ffffff}", "text{font-family:'Helvetica','PingFang SC',sans-serif}",
           "line,polyline,polygon,path,circle{stroke:#555;fill:none;stroke-width:1}",
           ".review{fill:none;stroke:#d32f2f;stroke-width:1.6;stroke-dasharray:5 4}",
           ".review-text{fill:#d32f2f;font-weight:700}"]
    for name, (color, width, dash) in LAYER_STYLE.items():
        cls = "l-%s" % name.replace("-", "_")
        extra = ("stroke-dasharray:%s;" % dash) if dash else ""
        css.append(".%s{stroke:%s;fill:none;stroke-width:%.2f;%s}"
                   % (cls, color, width, extra))
        css.append(".%s-mark{fill:%s;stroke:none}" % (cls, color))
        css.append("text.%s{fill:%s}" % (cls, color))
    for name in sorted(set(LAYERS.values()) | {"A-TRACE", "A-ROOM"}):
        cls = "l-%s" % name.replace("-", "_")
        if not any(rule.startswith("." + cls + "{") for rule in css):
            css.append(".%s{stroke:#555;fill:none;stroke-width:1.0}" % cls)

    document = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
        'viewBox="0 0 %.0f %.0f" version="1.1">'
        % (width_px, height_px, width_px, height_px),
        "<title>%s</title>" % escape(title or os.path.basename(path)),
        '<desc>generated by cad-skill/scripts/plan_render.py; unit %s</desc>' % escape(unit),
        "<style>%s</style>" % "".join(css),
        '<rect class="bg" x="0" y="0" width="100%" height="100%"/>',
        *body,
        "</svg>",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(document) + "\n")
    return {"path": path, "width": round(width_px, 1), "height": round(height_px, 1),
            "review_markers": sum(1 for frame in frames if frame["needs_review"])}


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="plan.json → calls / .scr / DXF / SVG（同一份几何，多后端）")
    parser.add_argument("plan", help="plan.json 路径，或 - 表示 stdin")
    parser.add_argument("--unit", default="mm", choices=("mm", "m"),
                        help="输入单位（m 会换算成 mm 输出）")
    parser.add_argument("--out-calls", help="输出调用序列 JSON")
    parser.add_argument("--out-scr", help="输出 AutoCAD 脚本")
    parser.add_argument("--out-dxf", help="输出 DXF")
    parser.add_argument("--out-svg", help="输出 SVG 预览（不需要 CAD）")
    parser.add_argument("--blocks", action="store_true",
                        help="DXF 里把门窗写成图块（直连落图仍用基础图元）")
    parser.add_argument("--dimtxt", type=float, default=250.0, help="脚本里的标注字高（mm）")
    parser.add_argument("--encoding", default="utf-8-sig", choices=("utf-8-sig", "utf-8", "gbk"))
    parser.add_argument("--no-extend", action="store_true", help="墙端不按半墙厚延伸")
    parser.add_argument("--no-zoom", dest="zoom", action="store_false")
    parser.add_argument("--no-undo-group", dest="undo_group", action="store_false")
    parser.add_argument("--no-comments", dest="comments", action="store_false")
    parser.add_argument("--keep-spaces", action="store_true")
    parser.add_argument("--pretty", action="store_true", help="统计结果缩进输出")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = sys.stdin.read() if args.plan == "-" else open(args.plan, encoding="utf-8").read()
    except OSError as exc:
        print("读取规格失败: %s" % exc, file=sys.stderr)
        return 1
    try:
        spec = json.loads(raw)
        plan = build(spec, args.unit, extend=not args.no_extend)
    except json.JSONDecodeError as exc:
        print("规格不是合法 JSON: %s" % exc, file=sys.stderr)
        return 2
    except wall_plan.SpecError as exc:
        print("规格错误: %s" % exc, file=sys.stderr)
        return 2

    stats: Dict[str, Any] = {
        "ok": True,
        "unit": "mm",
        "summary": plan["summary"],
        "warnings": plan["warnings"],
        "review": plan["review"],
    }
    try:
        if args.out_calls:
            stats["calls"] = write_calls(plan, args.out_calls)
        if args.out_scr:
            stats["scr"] = write_scr(plan, args.out_scr, dimtxt=args.dimtxt,
                                     encoding=args.encoding, zoom=args.zoom,
                                     undo_group=args.undo_group, comments=args.comments,
                                     keep_spaces=args.keep_spaces)
        if args.out_dxf:
            stats["dxf"] = write_dxf(spec, plan, args.out_dxf, blocks=args.blocks,
                                     unit=args.unit)
        if args.out_svg:
            stats["svg"] = write_svg(spec, plan, args.out_svg, unit=args.unit)
    except RuntimeError as exc:
        print("导出失败: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("写入失败: %s" % exc, file=sys.stderr)
        return 1
    except wall_plan.SpecError as exc:
        print("规格错误: %s" % exc, file=sys.stderr)
        return 2

    if not any((args.out_calls, args.out_scr, args.out_dxf, args.out_svg)):
        stats["calls_payload"] = plan
    print(json.dumps(stats, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
