#!/usr/bin/env python3
"""把语义平面直接执行到 CADCraft 工作 DWG，不生成 DXF。

模板可以是 .dwt（NEW from template）或 .dwg（打开后另存工作图）。
墙、尺寸和文字使用原生图元；门、窗使用命名块按名称插入。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import cad_direct  # noqa: E402
import plan_render  # noqa: E402
import wall_plan  # noqa: E402
from cadcraft_direct import CADCraftController  # noqa: E402


def _engine(controller, command: str, params: Dict[str, Any] | None = None):
    return controller.call("engine.execute", {
        "command": command,
        "params": params or {},
    })


def _template_layers(controller) -> Dict[str, str]:
    names = {layer["name"] for layer in controller.list_layers()}
    layers = dict(plan_render.LAYERS)
    if "A-GLAZ" in names:
        layers["window"] = "A-GLAZ"
    if "A-ANNO" in names:
        layers["dim"] = "A-ANNO"
        layers["text"] = "A-ANNO"
    return layers


def _clear_current_space(controller) -> int:
    inspect = controller.call("drawing.inspect", {
        "entities": True,
        "limit": 10000,
    })
    handles = [item["handle"] for item in inspect.get("entities", [])]
    if handles:
        _engine(controller, "erase", {"handles": handles})
    return len(handles)


def _create_unit_blocks(controller, origin: List[float],
                        door_layer: str, window_layer: str,
                        names: Dict[str, str]) -> List[str]:
    created: List[str] = []
    x, y = origin

    definitions = [
        (names["ccw"], [
            ("line", [x, y], [x + 1000, y]),
            ("arc", 0, 90),
        ], door_layer),
        (names["cw"], [
            ("line", [x, y], [x + 1000, y]),
            ("arc", 0, -90),
        ], door_layer),
    ]

    for name, parts, layer in definitions:
        entities = []
        for part in parts:
            if part[0] == "line":
                entities.append(controller.draw_line(
                    part[1], part[2], layer=layer).Handle)
            else:
                entities.append(controller.draw_arc(
                    [x, y], 1000, part[1], part[2], layer=layer).Handle)
        controller.create_block(
            name, [x, y], entities, keep="delete",
            description="CADCraft native unit door block")
        created.append(name)

    window_name = names["window"]
    entities = []
    for offset in (-500, 0, 500):
        entities.append(controller.draw_line(
            [x, y + offset], [x + 1000, y + offset],
            layer=window_layer).Handle)
    controller.create_block(
        window_name, [x, y], entities, keep="delete",
        description="CADCraft native unit window block")
    created.append(window_name)
    return created


def _insert_openings(controller, plan: Dict[str, Any],
                     names: Dict[str, str]) -> Dict[str, int]:
    doors = 0
    windows = 0
    for frame in wall_plan.opening_frames(plan["spec"]):
        if frame["kind"] == "door":
            block = (names["ccw"]
                     if frame["swing"] != "negative"
                     else names["cw"])
            rotation = frame["angle"] + (180 if frame["hinge"] == "end" else 0)
            controller.insert_block(
                block, frame["hinge_point"],
                scale=frame["width"] / 1000.0,
                rotation=rotation)
            doors += 1
        elif frame["kind"] == "window":
            controller.insert_block(
                names["window"], frame["start"],
                scale=[frame["width"] / 1000.0,
                       frame["thickness"] / 1000.0],
                rotation=frame["angle"])
            windows += 1
    return {"doors": doors, "windows": windows}


def _draw_calls(controller, calls: List[Dict[str, Any]]) -> int:
    drawn = 0
    for call in calls:
        cad_direct.dispatch(controller, call["tool"], call.get("args") or {})
        drawn += 1
    return drawn


def run(args) -> Dict[str, Any]:
    with open(args.plan, encoding="utf-8") as handle:
        spec = json.load(handle)

    controller = CADCraftController(port=args.port, launch=True)
    if args.template.lower().endswith(".dwt"):
        template_result = _engine(controller, "new", {
            "template": os.path.abspath(args.template),
        })
    else:
        template_result = controller.call("app.open", {
            "path": os.path.abspath(args.template),
        })

    layers = _template_layers(controller)
    cleared = _clear_current_space(controller) if args.clear else 0
    _engine(controller, "dimstyle", {
        "name": "CADCRAFT_MM",
        "current": True,
        "DIMDEC": 0,
        "DIMTXT": 180,
        "DIMASZ": 100,
        "DIMTAD": 1,
    })
    plan = wall_plan.build_plan(spec, layers)
    plan["spec"] = spec

    block_names = {
        "ccw": args.door_block_ccw,
        "cw": args.door_block_cw,
        "window": args.window_block,
    }
    existing = {item["name"] for item in controller.list_blocks()}
    missing = [name for name in block_names.values() if name not in existing]
    if missing and not args.create_missing_blocks:
        raise RuntimeError("模板缺少块: %s" % ", ".join(missing))
    blocks = []
    if missing:
        blocks = _create_unit_blocks(
            controller, [args.scratch_origin, args.scratch_origin],
            layers["door"], layers["window"], block_names)

    native_calls = [
        call
        for phase in plan["phases"]
        if phase["key"] not in ("door", "window")
        for call in phase["calls"]
    ]
    drawn = _draw_calls(controller, native_calls)
    inserted = _insert_openings(controller, plan, block_names)

    labels = 0
    if args.labels:
        with open(args.labels, encoding="utf-8") as handle:
            payload = json.load(handle)
        label_calls = payload.get("calls", payload) if isinstance(payload, dict) else payload
        labels = _draw_calls(controller, label_calls)

    output = os.path.abspath(args.output)
    saved = _engine(controller, "saveas", {"path": output, "version": 2018})
    reopened = controller.call("app.open", {"path": output})
    stats = controller.get_drawing_stats()
    block_defs = controller.list_blocks()
    counts = controller.block_count()

    screenshot = None
    if args.screenshot:
        screenshot = os.path.abspath(args.screenshot)
        with open(screenshot, "wb") as handle:
            handle.write(controller.screenshot())

    return {
        "ok": True,
        "template": os.path.abspath(args.template),
        "template_result": template_result,
        "cleared_template_entities": cleared,
        "layers": layers,
        "plan_summary": plan["summary"],
        "warnings": plan["warnings"],
        "blocks_created": blocks,
        "native_calls": drawn,
        "inserted": inserted,
        "labels": labels,
        "save": saved,
        "reopened": reopened,
        "stats": stats,
        "block_definitions": block_defs,
        "block_counts": counts,
        "screenshot": screenshot,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="语义平面 → CADCraft 原生 DWG（不生成 DXF）")
    parser.add_argument("plan", help="plan.json 路径")
    parser.add_argument("--template", required=True, help="模板 .dwt 或 .dwg")
    parser.add_argument("--output", required=True, help="工作 DWG 路径")
    parser.add_argument("--labels", help="房间文字调用 JSON")
    parser.add_argument("--clear", action="store_true",
                        help="清除模板中的示例图元，仅保留图层与样式")
    parser.add_argument("--scratch-origin", type=float, default=50000)
    parser.add_argument("--door-block-ccw", default="CAD_TEST_DOOR_CCW_1000",
                        help="正向开启门块名称")
    parser.add_argument("--door-block-cw", default="CAD_TEST_DOOR_CW_1000",
                        help="反向开启门块名称")
    parser.add_argument("--window-block", default="CAD_TEST_WINDOW_1000",
                        help="窗块名称")
    parser.add_argument("--create-missing-blocks", action=argparse.BooleanOptionalAction,
                        default=True, help="模板缺少块时创建单位测试块")
    parser.add_argument("--screenshot")
    parser.add_argument("--port", type=int, default=43210)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
