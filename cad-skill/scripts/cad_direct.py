#!/usr/bin/env python3
"""cad_direct — 不用 MCP，直接用程序调用 CAD 的命令行入口。

驱动层是内联在 skill 里的 `vendor/cad_core.py`（同样的 COM 层：图层/模型空间缓存、
run_script 批量脚本与 USERS1 哨兵确认、忙碌重试、撤销组）。

必须在装了 CAD 的 Windows 上运行（COM 只能同机调用）；远程时用
`ssh winbox python C:\\path\\cad_direct.py ...`。

约定：所有子命令都往 stdout 打**一个 JSON 对象**，日志走 stderr，退出码
0=成功 / 1=CAD 侧失败 / 2=参数或规格错误。脚本里的坐标单位由图纸决定（建筑图用 mm）。

例：
  cad_direct.py layers
  cad_direct.py entities --type line --limit 20
  cad_direct.py draw --calls calls.json           # wall_plan.py 的输出可直接喂进来
  cad_direct.py script --file plan.scr            # 整批落图（推荐，等价 MCP 的 draw_script）
  cad_direct.py shot --out check.png              # 截图交给 Codex 看
  cad_direct.py save --path D:\\out\\plan.dwg
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor"))

try:
    from cad_core import CADController, config  # noqa: E402
    _CORE_ERROR = None
except ImportError as exc:  # 没装 pywin32（非 Windows）时给出干净的 JSON 报错
    CADController = None
    config = {"cad": {}, "output": {}}
    _CORE_ERROR = exc

EXIT_OK = 0
EXIT_CAD_FAILURE = 1
EXIT_USAGE = 2


def _emit(payload: dict, exit_code: int = EXIT_OK) -> int:
    json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return exit_code


def _fail(message: str, code: int = EXIT_CAD_FAILURE, **extra) -> int:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return _emit(payload, code)


def _handle(entity) -> str | None:
    try:
        value = getattr(entity, "Handle", None)
    except Exception:
        return None
    return str(value) if value is not None else None


def _read_text(path: str) -> str:
    """读文件（path 为 - 时读 stdin），避免句柄泄漏"""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _has_cad() -> bool:
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except ImportError:
        return False
    return True


def connect(controller: CADController | None = None) -> CADController:
    """连接已运行的 CAD；没有实例时拉起一个（与 MCP 服务端行为一致）"""
    controller = controller or CADController()
    if controller.is_running():
        return controller
    if not controller.start_cad():
        raise RuntimeError("无法连接或启动 CAD（确认已安装并激活 AutoCAD / 浩辰 / 中望）")
    return controller


# ---------- draw：把调用序列（wall_plan.py 的输出）落到 CAD ----------

PRIMITIVE_KEYS = {
    "draw_line": ("start_point", "end_point"),
    "draw_circle": ("center", "radius"),
    "draw_arc": ("center", "radius", "start_angle", "end_angle"),
    "draw_ellipse": ("center", "major_axis", "minor_axis"),
    "draw_polyline": ("points",),
    "draw_rectangle": ("corner1", "corner2"),
    "draw_text": ("position", "text"),
    "draw_hatch": ("points",),
    "add_dimension": ("start_point", "end_point"),
}


def dispatch(controller: CADController, tool: str, args: dict):
    style = {"layer": args.get("layer"), "color": args.get("color")}
    # 文字与标注没有线宽参数（与 CAD-MCP 的工具签名一致），传了会 TypeError
    if tool not in ("draw_text", "add_dimension"):
        style["lineweight"] = args.get("lineweight")
    if tool == "draw_line":
        return controller.draw_line(args["start_point"], args["end_point"], **style)
    if tool == "draw_circle":
        return controller.draw_circle(args["center"], args["radius"], **style)
    if tool == "draw_arc":
        return controller.draw_arc(args["center"], args["radius"], args["start_angle"],
                                   args["end_angle"], **style)
    if tool == "draw_ellipse":
        return controller.draw_ellipse(args["center"], args["major_axis"], args["minor_axis"],
                                       args.get("rotation", 0), **style)
    if tool == "draw_polyline":
        return controller.draw_polyline(args["points"], bool(args.get("closed")), **style)
    if tool == "draw_rectangle":
        return controller.draw_rectangle(args["corner1"], args["corner2"], **style)
    if tool == "draw_text":
        return controller.draw_text(args["position"], args["text"], args.get("height", 2.5),
                                    args.get("rotation", 0), **style)
    if tool == "draw_hatch":
        return controller.draw_hatch(args["points"], args.get("pattern_name", "SOLID"),
                                     args.get("scale", 1.0), **style)
    if tool == "add_dimension":
        return controller.add_dimension(args["start_point"], args["end_point"],
                                        args.get("text_position"),
                                        args.get("textheight", 5), **style)
    raise ValueError(f"不支持的绘图工具: {tool}")


def load_calls(path: str) -> list:
    payload = json.loads(_read_text(path))
    if isinstance(payload, dict):
        payload = payload.get("calls") or []
    if not isinstance(payload, list) or not payload:
        raise ValueError("调用序列为空：需要 [{tool, args}, ...] 或 wall_plan.py 的输出")
    return payload


def validate_calls(calls: list) -> list:
    """只校验，不碰 CAD（--dry-run 用）"""
    report = []
    for index, call in enumerate(calls):
        tool = call.get("tool") if isinstance(call, dict) else None
        args = call.get("args") if isinstance(call, dict) else None
        if tool not in PRIMITIVE_KEYS:
            report.append({"index": index, "ok": False, "error": f"不支持的绘图工具: {tool}"})
            continue
        if not isinstance(args, dict):
            report.append({"index": index, "ok": False, "error": "args 必须是对象"})
            continue
        missing = [key for key in PRIMITIVE_KEYS[tool] if key not in args]
        report.append({"index": index, "tool": tool, "ok": not missing,
                       "error": f"缺少参数 {missing}" if missing else None})
    return report


def cmd_draw(args) -> int:
    try:
        calls = load_calls(args.calls)
        report = validate_calls(calls)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _fail(f"读取调用序列失败: {exc}", EXIT_USAGE)

    invalid = [item for item in report if not item["ok"]]
    if invalid:
        return _fail("调用序列不合法", EXIT_USAGE, results=report)
    if args.dry_run:
        return _emit({"ok": True, "dry_run": True, "validated": len(report), "results": report})

    try:
        controller = connect()
    except RuntimeError as exc:
        return _fail(str(exc))

    results = []
    for index, call in enumerate(calls):
        tool, prim_args = call["tool"], call.get("args") or {}
        try:
            entity = dispatch(controller, tool, prim_args)
            results.append({"index": index, "tool": tool, "ok": entity is not None,
                            "handle": _handle(entity),
                            "error": None if entity is not None else "CAD 返回失败"})
            if entity is None:
                if not args.keep_going:
                    break
        except Exception as exc:  # CAD 侧异常：记录并（默认）停止
            results.append({"index": index, "tool": tool, "ok": False, "error": str(exc)})
            if not args.keep_going:
                break

    created = [item["handle"] for item in results if item["ok"] and item.get("handle")]
    failed = [item for item in results if not item["ok"]]
    payload = {"ok": not failed, "drawn": len(created), "handles": created,
               "results": results,
               "stats": controller.get_drawing_stats() if controller.is_running() else None}
    return _emit(payload, EXIT_OK if not failed else EXIT_CAD_FAILURE)


# ---------- script：整批落图 ----------

def cmd_script(args) -> int:
    try:
        script = _read_text(args.file)
    except OSError as exc:
        return _fail(f"读取脚本失败: {exc}", EXIT_USAGE)
    if not script.strip():
        return _fail("脚本内容为空", EXIT_USAGE)

    try:
        controller = connect()
    except RuntimeError as exc:
        return _fail(str(exc))

    result = controller.run_script(script, file_path=args.out, encoding=args.encoding,
                                   timeout=args.timeout, sentinel=not args.no_sentinel)
    payload = {"ok": bool(result.get("success"))}
    payload.update(result)
    return _emit(payload, EXIT_OK if result.get("success") else EXIT_CAD_FAILURE)


# ---------- 查询与检查 ----------

def cmd_layers(args) -> int:
    try:
        controller = connect()
        return _emit({"ok": True, "layers": controller.list_layers()})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_entities(args) -> int:
    try:
        controller = connect()
        return _emit({"ok": True, **controller.list_entities(args.type, args.limit)})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_props(args) -> int:
    try:
        controller = connect()
        return _emit({"ok": True, **controller.get_entity_properties(args.handle)})
    except Exception as exc:
        return _fail(str(exc))


def cmd_stats(args) -> int:
    try:
        controller = connect()
        return _emit({"ok": True, **controller.get_drawing_stats()})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_shot(args) -> int:
    try:
        controller = connect()
        if args.zoom:
            controller.zoom_extents()
        path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(controller.screenshot())
        return _emit({"ok": True, "image": path, "bytes": os.path.getsize(path)})
    except Exception as exc:
        return _fail(f"截图失败: {exc}")


def cmd_zoom(args) -> int:
    try:
        controller = connect()
        ok = controller.zoom_extents()
        return _emit({"ok": bool(ok)})
    except RuntimeError as exc:
        return _fail(str(exc))


# ---------- 图纸与编辑 ----------

def cmd_save(args) -> int:
    try:
        controller = connect()
        path = os.path.abspath(args.path)
        ok = controller.save_drawing(path)
        return _emit({"ok": bool(ok), "path": path})
    except Exception as exc:
        return _fail(str(exc))


def cmd_open(args) -> int:
    try:
        controller = connect()
        ok = controller.open_drawing(os.path.abspath(args.path))
        return _emit({"ok": bool(ok), "path": os.path.abspath(args.path)})
    except Exception as exc:
        return _fail(str(exc))


def cmd_new(args) -> int:
    try:
        controller = connect()
        ok = controller.new_drawing()
        return _emit({"ok": bool(ok)})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_send(args) -> int:
    try:
        controller = connect()
        ok = controller.send_command(args.command)
        return _emit({"ok": bool(ok), "command": args.command})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_undo(args) -> int:
    try:
        controller = connect()
        done = [bool(getattr(controller, "undo")()) for _ in range(args.times)]
        return _emit({"ok": all(done), "times": args.times, "results": done})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_redo(args) -> int:
    try:
        controller = connect()
        done = [bool(getattr(controller, "redo")()) for _ in range(args.times)]
        return _emit({"ok": all(done), "times": args.times, "results": done})
    except RuntimeError as exc:
        return _fail(str(exc))


def cmd_erase(args) -> int:
    return _edit(lambda c: c.erase_entity(args.handle), args)


def cmd_move(args) -> int:
    return _edit(lambda c: c.move_entity(args.handle, [args.dx, args.dy]), args)


def cmd_copy(args) -> int:
    return _edit(lambda c: c.copy_entity(args.handle, [args.dx, args.dy]), args, new_entity=True)


def cmd_offset(args) -> int:
    return _edit(lambda c: c.offset_entity(args.handle, args.distance), args, multiple=True)


def cmd_mirror(args) -> int:
    return _edit(lambda c: c.mirror_entity(args.handle, args.p1, args.p2), args, new_entity=True)


def cmd_rotate(args) -> int:
    return _edit(lambda c: c.rotate_entity(args.handle, args.base, args.angle), args)


def cmd_scale(args) -> int:
    return _edit(lambda c: c.scale_entity(args.handle, args.base, args.factor), args)


def _edit(operation, args, new_entity: bool = False, multiple: bool = False) -> int:
    try:
        controller = connect()
        result = operation(controller)
    except Exception as exc:
        return _fail(str(exc))
    if multiple:
        handles = [_handle(item) for item in (result or [])]
        return _emit({"ok": bool(result), "handles": [h for h in handles if h]})
    if new_entity:
        handle = _handle(result)
        return _emit({"ok": result is not None, "handle": handle})
    return _emit({"ok": bool(result), "handle": args.handle})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cad_direct.py",
                                     description="直接用程序调用 CAD（不经过 MCP）")
    parser.add_argument("--cad-type", choices=["AUTOCAD", "GCAD", "GSTARCAD", "ZWCAD"],
                        help="覆盖 CAD 类型（默认读 CAD_TYPE 环境变量，再退回 AUTOCAD）")
    parser.add_argument("--output-dir", help="脚本落盘目录（默认读 CAD_OUTPUT_DIR，再退回 ./cad_output）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("layers", help="列出图层").set_defaults(func=cmd_layers)
    sub.add_parser("stats", help="当前图纸统计（实体数/图层数/文档名）").set_defaults(func=cmd_stats)
    sub.add_parser("zoom", help="缩放到全图").set_defaults(func=cmd_zoom)
    sub.add_parser("new", help="新建图纸").set_defaults(func=cmd_new)

    p = sub.add_parser("entities", help="列出模型空间实体（含句柄）")
    p.add_argument("--type", help="按类型过滤，如 line/circle/arc/text")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_entities)

    p = sub.add_parser("props", help="按句柄查实体属性")
    p.add_argument("--handle", required=True)
    p.set_defaults(func=cmd_props)

    p = sub.add_parser("draw", help="执行调用序列（wall_plan.py 的输出可直接用）")
    p.add_argument("--calls", required=True, help="调用序列 JSON 路径，或 - 读 stdin")
    p.add_argument("--dry-run", action="store_true", help="只校验不画")
    p.add_argument("--keep-going", action="store_true", help="出错继续画后面的（默认遇到失败就停）")
    p.set_defaults(func=cmd_draw)

    p = sub.add_parser("script", help="整批落图：一次执行整段脚本（推荐）")
    p.add_argument("--file", required=True, help=".scr 脚本路径，或 - 读 stdin")
    p.add_argument("--out", help="脚本落盘的绝对路径（默认写到输出目录）")
    p.add_argument("--encoding", default="utf-8-sig", choices=["utf-8-sig", "utf-8", "gbk"],
                   help="中文文字乱码时用 gbk")
    p.add_argument("--timeout", type=float, default=180.0, help="等待确认完成的秒数")
    p.add_argument("--no-sentinel", action="store_true", help="不追加 USERS1 哨兵（不确认完成）")
    p.set_defaults(func=cmd_script)

    p = sub.add_parser("shot", help="截图保存为 PNG（交给 Codex 看）")
    p.add_argument("--out", default="cad_shot.png")
    p.add_argument("--no-zoom", dest="zoom", action="store_false", help="截图前不缩放到全图")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("save", help="保存图纸（按扩展名存 DWG/DXF）")
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("open", help="打开已有图纸")
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("send", help="直通 CAD 命令行执行命令（建议 _. 前缀）")
    p.add_argument("--command", required=True)
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("undo", help="撤销 N 步")
    p.add_argument("--times", type=int, default=1)
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("redo", help="重做 N 步")
    p.add_argument("--times", type=int, default=1)
    p.set_defaults(func=cmd_redo)

    p = sub.add_parser("erase", help="删除实体")
    p.add_argument("--handle", required=True)
    p.set_defaults(func=cmd_erase)

    p = sub.add_parser("move", help="平移实体")
    p.add_argument("--handle", required=True)
    p.add_argument("--dx", type=float, required=True)
    p.add_argument("--dy", type=float, required=True)
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("copy", help="复制实体")
    p.add_argument("--handle", required=True)
    p.add_argument("--dx", type=float, required=True)
    p.add_argument("--dy", type=float, required=True)
    p.set_defaults(func=cmd_copy)

    p = sub.add_parser("offset", help="偏移实体")
    p.add_argument("--handle", required=True)
    p.add_argument("--distance", type=float, required=True)
    p.set_defaults(func=cmd_offset)

    p = sub.add_parser("mirror", help="沿两点连线镜像实体")
    p.add_argument("--handle", required=True)
    p.add_argument("--p1", type=float, nargs=2, required=True)
    p.add_argument("--p2", type=float, nargs=2, required=True)
    p.set_defaults(func=cmd_mirror)

    p = sub.add_parser("rotate", help="绕基点旋转实体（度，逆时针为正）")
    p.add_argument("--handle", required=True)
    p.add_argument("--base", type=float, nargs=2, required=True)
    p.add_argument("--angle", type=float, required=True)
    p.set_defaults(func=cmd_rotate)

    p = sub.add_parser("scale", help="绕基点缩放实体")
    p.add_argument("--handle", required=True)
    p.add_argument("--base", type=float, nargs=2, required=True)
    p.add_argument("--factor", type=float, required=True)
    p.set_defaults(func=cmd_scale)

    return parser


def main(argv=None) -> int:
    try:  # Windows 控制台默认可能是 GBK，JSON 里有中文会炸
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")

    args = build_parser().parse_args(argv)
    if args.cad_type:
        config["cad"]["type"] = args.cad_type
    if args.output_dir:
        config["output"]["directory"] = args.output_dir

    if not _has_cad():
        return _fail("缺少 pywin32：请在装了 CAD 的 Windows 上执行 pip install pywin32 pillow"
                     + (f"（驱动层导入失败: {_CORE_ERROR}）" if _CORE_ERROR else ""))
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return _fail("已中断", 130)


if __name__ == "__main__":
    sys.exit(main())
