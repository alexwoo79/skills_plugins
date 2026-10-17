#!/usr/bin/env python3
"""CADCraft 0.7+ 本机控制通道驱动。

CADCraft 启动时带 `--control <port>` 会在 127.0.0.1 上开一个行分隔 JSON 服务。
本模块把 skill 的 calls/图元接口映射到它的 `engine.execute` 命令，不依赖
AppleScript，也不模拟鼠标点击。

控制协议：
  {"id": 1, "method": "engine.execute",
   "params": {"command": "line", "params": {"points": [[0,0],[1000,0]]}}}

响应：
  {"id": 1, "ok": true, "result": {"handles": ["100"]}}
"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_PORT = int(os.environ.get("CADCRAFT_CONTROL_PORT", "43210"))
MACOS_APP = "/Applications/CADCraft.app/Contents/MacOS/CADCraft"


def find_binary() -> Optional[str]:
    """查找当前系统的 CADCraft 可执行文件；环境变量优先。"""
    explicit = os.environ.get("CADCRAFT_BINARY")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    candidates = (
        MACOS_APP,
        shutil.which("cadcraft"),
        shutil.which("CADCraft"),
    )
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


class CADCraftError(RuntimeError):
    """CADCraft 控制通道返回的错误。"""


class CADCraftEntity:
    """兼容 cad_direct._handle 的轻量实体引用。"""

    def __init__(self, handle: str):
        self.Handle = str(handle)

    def __repr__(self) -> str:
        return "CADCraftEntity(%s)" % self.Handle


def _point(value: Sequence[float]) -> List[float]:
    if value is None or len(value) < 2:
        raise ValueError("坐标点至少需要两个分量: %r" % (value,))
    return [float(value[0]), float(value[1])]


def _points(values: Iterable[Sequence[float]]) -> List[List[float]]:
    return [_point(value) for value in values]


def _lineweight(value: Any) -> Any:
    """把 AutoCAD 的 1/100mm 线宽索引转换为 CADCraft 的毫米值。"""
    if value is None or isinstance(value, str):
        return value
    number = float(value)
    known = {0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70,
             80, 90, 100, 106, 120, 140, 158, 200, 211}
    if abs(number - round(number)) < 1e-9 and int(round(number)) in known:
        return round(number / 100.0, 3)
    return number


def _color(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return "%d,%d,%d" % tuple(int(part) for part in value)
    return value


def _friendly_type(value: str) -> str:
    return {
        "line": "line",
        "polyline": "polyline",
        "lwpolyline": "polyline",
        "circle": "circle",
        "arc": "arc",
        "ellipse": "ellipse",
        "text": "text",
        "mtext": "mtext",
        "hatch": "hatch",
        "dimension": "dimension",
        "dimalert": "dimension",
    }.get(str(value).lower(), str(value).lower())


class CADCraftController:
    """通过 CADCraft 本地控制端口操作当前图纸。"""

    @staticmethod
    def find_binary() -> Optional[str]:
        return find_binary()

    def __init__(self, host: str = "127.0.0.1", port: Optional[int] = None,
                 launch: bool = True, startup_timeout: float = 15.0,
                 timeout: float = 30.0):
        self.host = host
        self.port = int(port or DEFAULT_PORT)
        self.launch = launch
        self.startup_timeout = startup_timeout
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._reader = None
        self._next_id = 0
        self._known_layers: set[str] = set()

    # ------------------------------------------------------------ transport

    def _connect(self) -> None:
        self._close_socket()
        sock = socket.create_connection((self.host, self.port), timeout=2.0)
        sock.settimeout(self.timeout)
        self._sock = sock
        self._reader = sock.makefile("r", encoding="utf-8", newline="\n")

    def _close_socket(self) -> None:
        try:
            if self._reader is not None:
                self._reader.close()
        except OSError:
            pass
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._reader = None
        self._sock = None

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """发送一次 RPC；返回 result，失败抛 CADCraftError。"""
        if self._sock is None:
            try:
                self._connect()
            except OSError as exc:
                raise CADCraftError(
                    "连不上 CADCraft 控制端口 %s:%s（先用 --control %s 启动 CADCraft）: %s"
                    % (self.host, self.port, self.port, exc)) from exc
        self._next_id += 1
        request = {"id": self._next_id, "method": method, "params": params or {}}
        try:
            self._sock.sendall(
                (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            line = self._reader.readline()
        except (OSError, AttributeError) as exc:
            self._close_socket()
            raise CADCraftError("CADCraft 控制连接中断: %s" % exc) from exc
        if not line:
            self._close_socket()
            raise CADCraftError("CADCraft 控制连接已关闭")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CADCraftError("CADCraft 返回了非法 JSON: %r" % line) from exc
        if not response.get("ok"):
            raise CADCraftError(str(response.get("error") or "CADCraft 命令失败"))
        return response.get("result")

    def is_running(self) -> bool:
        try:
            self.call("ping")
            return True
        except CADCraftError:
            self._close_socket()
            return False

    def start_cad(self) -> bool:
        """连接已有实例；没有控制端口时按需启动 CADCraft。"""
        if self.is_running():
            return True
        if not self.launch:
            return False
        binary = find_binary()
        if not binary:
            return False
        log_path = os.path.join(tempfile.gettempdir(), "cadcraft-control.log")
        log = open(log_path, "a", encoding="utf-8")
        try:
            env = dict(os.environ)
            env.setdefault("CADCRAFT_CONTROL_PORT", str(self.port))
            subprocess.Popen(
                [binary, "--control", str(self.port)],
                env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
        except OSError:
            return False
        finally:
            log.close()
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.is_running():
                return True
            time.sleep(0.25)
        return False

    @classmethod
    def launch_hint(cls, port: int = DEFAULT_PORT) -> str:
        binary = cls.find_binary()
        if not binary:
            binary = MACOS_APP if sys.platform == "darwin" else "cadcraft"
        return "`%s --control %d`" % (binary, port)

    def close(self) -> None:
        self._close_socket()

    # ------------------------------------------------------------ commands

    def _execute(self, command: str, params: Optional[Dict[str, Any]] = None
                 ) -> Tuple[List[str], Any]:
        result = self.call("engine.execute", {
            "command": command,
            "params": params or {},
        })
        handles: List[str] = []
        if isinstance(result, dict):
            raw = result.get("handles") or result.get("handle")
            if isinstance(raw, str):
                handles = [raw]
            elif isinstance(raw, list):
                handles = [str(item) for item in raw]
        elif isinstance(result, list):
            handles = [str(item) for item in result if isinstance(item, str)]
        return handles, result

    @staticmethod
    def _first_handle(handles: List[str]) -> str:
        if not handles:
            raise CADCraftError("CADCraft 命令没有返回实体句柄")
        return handles[0]

    def _ensure_layer(self, layer: str) -> None:
        if not layer or layer in self._known_layers:
            return
        try:
            self._execute("layer.new", {"name": layer, "current": False})
        except CADCraftError:
            # 图层已存在是正常情况；后面的 properties.set 会给出真正的错误。
            pass
        self._known_layers.add(layer)

    def _apply_style(self, handles: Sequence[str], layer: Optional[str] = None,
                     color: Any = None, lineweight: Any = None) -> None:
        if not handles:
            return
        if layer:
            self._ensure_layer(layer)
        params: Dict[str, Any] = {"handles": list(handles)}
        if layer:
            params["layer"] = layer
        if color is not None:
            params["color"] = _color(color)
        if lineweight is not None:
            params["lineweight"] = _lineweight(lineweight)
        if len(params) > 1:
            self._execute("properties.set", params)

    # ------------------------------------------------------------ drawing

    def draw_line(self, start_point, end_point, layer=None, color=None,
                  lineweight=None):
        handles, _ = self._execute("line", {
            "points": [_point(start_point), _point(end_point)],
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color, lineweight)
        return CADCraftEntity(handle)

    def draw_circle(self, center, radius: float, layer=None, color=None,
                    lineweight=None):
        if radius <= 0:
            raise ValueError("圆半径必须为正数")
        handles, _ = self._execute("circle", {
            "center": _point(center), "radius": float(radius),
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color, lineweight)
        return CADCraftEntity(handle)

    def draw_arc(self, center, radius: float, start_angle: float,
                 end_angle: float, layer=None, color=None, lineweight=None):
        if radius <= 0:
            raise ValueError("圆弧半径必须为正数")
        handles, _ = self._execute("arc", {
            "center": _point(center), "radius": float(radius),
            "start": float(start_angle), "end": float(end_angle),
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color, lineweight)
        return CADCraftEntity(handle)

    def draw_ellipse(self, center, major_axis: float, minor_axis: float,
                     rotation: float = 0, layer=None, color=None,
                     lineweight=None):
        if major_axis <= 0 or minor_axis <= 0 or minor_axis > major_axis:
            raise ValueError("椭圆长轴必须为正且不小于短轴")
        angle = math.radians(float(rotation))
        handles, _ = self._execute("ellipse", {
            "center": _point(center),
            "major": [major_axis * math.cos(angle), major_axis * math.sin(angle)],
            "ratio": minor_axis / major_axis,
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color, lineweight)
        return CADCraftEntity(handle)

    def draw_polyline(self, points, closed: bool = False, layer=None,
                      color=None, lineweight=None):
        vertices = _points(points)
        if len(vertices) < 2:
            raise ValueError("多段线至少需要两个点")
        handles, _ = self._execute("pline", {
            "vertices": vertices, "closed": bool(closed),
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color, lineweight)
        return CADCraftEntity(handle)

    def draw_rectangle(self, corner1, corner2, layer=None, color=None,
                       lineweight=None):
        x1, y1 = _point(corner1)
        x2, y2 = _point(corner2)
        return self.draw_polyline(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            closed=True, layer=layer, color=color, lineweight=lineweight)

    def draw_text(self, position, text: str, height: float = 2.5,
                  rotation: float = 0, layer=None, color=None):
        handles, _ = self._execute("text", {
            "at": _point(position), "text": str(text),
            "height": float(height), "rotation": float(rotation),
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color)
        return CADCraftEntity(handle)

    def draw_hatch(self, points, pattern_name: str = "SOLID",
                   scale: float = 1.0, layer=None, color=None,
                   lineweight=None):
        vertices = _points(points)
        if len(vertices) < 3:
            raise ValueError("填充边界至少需要三个点")
        boundary = self.draw_polyline(vertices, closed=True, layer=layer)
        try:
            handles, _ = self._execute("hatch", {
                "handles": [boundary.Handle],
                "pattern": pattern_name,
                "scale": float(scale),
            })
            handle = self._first_handle(handles)
            self._apply_style([handle], layer=layer, color=color)
            return CADCraftEntity(handle)
        except Exception:
            try:
                self.erase_entity(boundary.Handle)
            except Exception:
                pass
            raise

    def add_dimension(self, start_point, end_point, text_position=None,
                      textheight: float = 5, layer=None, color=None):
        p1 = _point(start_point)
        p2 = _point(end_point)
        at = _point(text_position) if text_position else [
            (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0 + 5.0]
        handles, _ = self._execute("dimaligned", {
            "p1": p1, "p2": p2, "at": at,
        })
        handle = self._first_handle(handles)
        self._apply_style([handle], layer, color)
        if textheight is not None:
            for key in ("textHeight", "DIMTXT"):
                try:
                    self._execute("dimoverride", {
                        "handles": [handle], key: float(textheight),
                    })
                    break
                except CADCraftError:
                    continue
        return CADCraftEntity(handle)

    # ------------------------------------------------------------ queries

    def _inspect(self, **params) -> Dict[str, Any]:
        result = self.call("drawing.inspect", params)
        return result if isinstance(result, dict) else {}

    def list_layers(self) -> List[Dict[str, Any]]:
        result = self._inspect()
        current = result.get("currentLayer")
        layers = []
        for item in result.get("layers", []):
            name = item.get("name")
            layers.append({
                "name": name,
                "color": item.get("color"),
                "is_current": name == current,
                "is_frozen": bool(item.get("frozen", False)),
                "is_locked": bool(item.get("locked", False)),
                "is_on": bool(item.get("on", True)),
            })
        return layers

    def list_entities(self, entity_type: str = None, limit: int = 50) -> Dict[str, Any]:
        fetch_limit = min(max(int(limit) * (20 if entity_type else 1), 50), 5000)
        result = self._inspect(entities=True, limit=fetch_limit)
        total = int(result.get("entityCount", 0))
        entities = []
        for item in result.get("entities", []):
            friendly = _friendly_type(item.get("type", ""))
            if entity_type and friendly != entity_type.lower():
                continue
            entities.append({
                "handle": str(item.get("handle")),
                "entity_type": friendly,
                "layer": item.get("layer"),
                "color": item.get("color"),
                "geometry": item.get("geometry"),
            })
            if len(entities) >= limit:
                break
        payload = {"total": total, "returned": len(entities), "entities": entities}
        if entity_type and total > fetch_limit:
            payload["note"] = "只在前 %d 个实体中按类型过滤" % fetch_limit
        return payload

    def get_entity_properties(self, handle: str) -> Dict[str, Any]:
        try:
            result = self.call("engine.execute", {
                "command": "properties", "params": {"handles": [str(handle)]},
            })
            if isinstance(result, list) and result:
                item = result[0]
            elif isinstance(result, dict) and result.get("objects"):
                item = result["objects"][0]
            else:
                item = result
            if isinstance(item, dict):
                return {
                    "handle": str(item.get("handle", handle)),
                    "entity_type": _friendly_type(item.get("type", "")),
                    "layer": item.get("layer"),
                    "color": item.get("color"),
                    "properties": item,
                }
        except CADCraftError:
            pass
        # 老版本或 properties 命令不可用时退回实体列表查找。
        result = self._inspect(entities=True, limit=5000)
        for item in result.get("entities", []):
            if str(item.get("handle")) == str(handle):
                return {
                    "handle": str(handle),
                    "entity_type": _friendly_type(item.get("type", "")),
                    "layer": item.get("layer"),
                    "color": item.get("color"),
                    "properties": item,
                }
        raise ValueError("未找到句柄为 %s 的实体" % handle)

    def get_drawing_stats(self) -> Dict[str, Any]:
        result = self._inspect()
        return {
            "running": True,
            "entity_count": result.get("entityCount", 0),
            "layer_count": len(result.get("layers", [])),
            "document_name": result.get("title"),
            "path": result.get("path"),
            "counts": result.get("counts", {}),
            "extents": result.get("extents"),
        }

    def screenshot(self) -> bytes:
        result = self.call("ui.render", {"fit": True})
        encoded = result.get("pngBase64") if isinstance(result, dict) else None
        if encoded:
            return base64.b64decode(encoded)
        path = os.path.join(tempfile.gettempdir(), "cadcraft-shot.png")
        self.call("ui.screenshot", {"path": path})
        with open(path, "rb") as handle:
            return handle.read()

    # ------------------------------------------------------------ document/edit

    def new_drawing(self) -> bool:
        self._execute("new", {"metric": True})
        return True

    def open_drawing(self, path: str) -> bool:
        self.call("app.open", {"path": os.path.abspath(path)})
        return True

    def save_drawing(self, path: str) -> bool:
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        self.call("app.save", {"path": target})
        return True

    def zoom_extents(self) -> bool:
        self._execute("zoom", {"mode": "extents"})
        return True

    def send_command(self, command: str) -> bool:
        self.call("cmdline.input", {"text": command})
        return True

    def undo(self) -> bool:
        self._execute("undo", {"count": 1})
        return True

    def redo(self) -> bool:
        self._execute("redo", {"count": 1})
        return True

    def erase_entity(self, handle: str) -> bool:
        self._execute("erase", {"handles": [str(handle)]})
        return True

    def move_entity(self, handle: str, displacement: Sequence[float]) -> bool:
        self._execute("move", {
            "handles": [str(handle)],
            "delta": [float(displacement[0]), float(displacement[1])],
        })
        return True

    def copy_entity(self, handle: str, displacement: Sequence[float]):
        handles, _ = self._execute("copy", {
            "handles": [str(handle)],
            "delta": [float(displacement[0]), float(displacement[1])],
        })
        if not handles:
            raise CADCraftError("复制没有返回新实体句柄")
        return CADCraftEntity(handles[-1])

    def offset_entity(self, handle: str, distance: float):
        side = self._offset_side(handle, float(distance))
        handles, _ = self._execute("offset", {
            "handle": str(handle), "distance": float(distance), "side": side,
        })
        return [CADCraftEntity(item) for item in handles]

    def _offset_side(self, handle: str, distance: float) -> List[float]:
        props = self.get_entity_properties(handle)
        detail = props.get("properties") or {}
        geometry = detail.get("geometry", detail)
        center = geometry.get("center")
        radius = geometry.get("radius")
        if isinstance(center, dict):
            center = [center.get("x", 0), center.get("y", 0)]
        if center and radius is not None:
            return [float(center[0]) + float(radius) + max(distance, 1.0),
                    float(center[1])]
        vertices = geometry.get("vertices") or geometry.get("points")
        if not vertices and geometry.get("a") and geometry.get("b"):
            vertices = [geometry.get("a"), geometry.get("b")]
        if not vertices and isinstance(geometry.get("start"), dict):
            vertices = [geometry.get("start"), geometry.get("end")]
        if vertices and len(vertices) >= 2:
            first, second = vertices[0], vertices[1]
            if isinstance(first, dict):
                first = [first.get("x", 0), first.get("y", 0)]
            if isinstance(second, dict):
                second = [second.get("x", 0), second.get("y", 0)]
            dx, dy = float(second[0]) - float(first[0]), float(second[1]) - float(first[1])
            length = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / length, dx / length
            mid = [(float(first[0]) + float(second[0])) / 2.0,
                   (float(first[1]) + float(second[1])) / 2.0]
            return [mid[0] + nx * max(distance, 1.0),
                    mid[1] + ny * max(distance, 1.0)]
        return [0.0, max(distance, 1.0)]

    def mirror_entity(self, handle: str, p1, p2):
        handles, _ = self._execute("mirror", {
            "handles": [str(handle)], "p1": _point(p1), "p2": _point(p2),
            "erase": False,
        })
        if not handles:
            raise CADCraftError("镜像没有返回新实体句柄")
        return CADCraftEntity(handles[-1])

    def rotate_entity(self, handle: str, base_point, angle_degrees: float) -> bool:
        self._execute("rotate", {
            "handles": [str(handle)], "base": _point(base_point),
            "angle": float(angle_degrees),
        })
        return True

    def scale_entity(self, handle: str, base_point, scale_factor: float) -> bool:
        self._execute("scale", {
            "handles": [str(handle)], "base": _point(base_point),
            "factor": float(scale_factor),
        })
        return True

    def run_script(self, script: str, **kwargs) -> Dict[str, Any]:
        try:
            result = self.call("cmdline.script", {"text": script})
        except CADCraftError as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "result": result}
