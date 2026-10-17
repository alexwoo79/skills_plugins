#!/usr/bin/env python3
"""CADCraft 控制通道驱动的离线测试。

用本地假 TCP 服务模拟 CADCraft 的行分隔 JSON 协议，验证：
图元命令映射、图层/颜色/线宽、查询、截图，以及 cad_direct 的 CADCRAFT 分派。
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import cad_direct  # noqa: E402
import cadcraft_direct  # noqa: E402


class _FakeReader:
    def __init__(self, server):
        self.server = server

    def readline(self) -> str:
        if not self.server.responses:
            return ""
        return json.dumps(self.server.responses.pop(0), ensure_ascii=False) + "\n"

    def close(self) -> None:
        return None


class _FakeSocket:
    def __init__(self, server):
        self.server = server

    def settimeout(self, timeout) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        request = json.loads(data.decode("utf-8"))
        self.server.responses.append(self.server.respond(request))

    def makefile(self, *args, **kwargs):
        return _FakeReader(self.server)

    def close(self) -> None:
        return None


class FakeCADCraft:
    def __init__(self) -> None:
        self.requests = []
        self.responses = []
        self._handle = 0

    def respond(self, request):
        method = request.get("method")
        params = request.get("params") or {}
        self.requests.append((method, params))
        if method == "ping":
            return {"id": request.get("id"), "ok": True, "result": "pong"}
        if method == "control.methods":
            return {"id": request.get("id"), "ok": True, "result": []}
        if method == "drawing.inspect":
            return {"id": request.get("id"), "ok": True, "result": {
                "entityCount": 2,
                "title": "FakeCAD",
                "path": None,
                "currentLayer": "0",
                "counts": {"Line": 1, "Polyline": 1},
                "extents": [[0, 0], [1000, 500]],
                "layers": [
                    {"name": "0", "color": "White", "frozen": False,
                     "locked": False, "on": True},
                    {"name": "A-WALL", "color": "White", "frozen": False,
                     "locked": False, "on": True},
                ],
                "entities": [
                    {"handle": "100", "type": "Line", "layer": "A-WALL",
                     "color": "ByLayer",
                     "geometry": {"a": {"x": 0, "y": 0}, "b": {"x": 1000, "y": 0}}},
                    {"handle": "101", "type": "Polyline", "layer": "0",
                     "color": "ByLayer",
                     "geometry": {"vertices": [{"x": 0, "y": 0}, {"x": 0, "y": 500}]}},
                ],
            }}
        if method == "engine.execute":
            command = params.get("command")
            args = params.get("params") or {}
            if command == "properties":
                return {"id": request.get("id"), "ok": True, "result": {
                    "count": 1,
                    "objects": [{
                        "handle": "100", "type": "Line", "layer": "A-WALL",
                        "color": "ByLayer", "lineweight": "ByLayer",
                        "geometry": {"a": {"x": 0, "y": 0}, "b": {"x": 1000, "y": 0}},
                    }],
                }}
            if command in ("properties.set", "layer.new", "dimoverride",
                           "undo", "redo", "zoom", "new"):
                return {"id": request.get("id"), "ok": True, "result": {}}
            self._handle += 1
            return {"id": request.get("id"), "ok": True,
                    "result": {"handles": ["%X" % self._handle]}}
        if method in ("app.open", "app.save", "cmdline.input"):
            return {"id": request.get("id"), "ok": True, "result": {}}
        if method == "cmdline.script":
            return {"id": request.get("id"), "ok": True,
                    "result": {"output": ["Command: LINE"], "state": {"prompt": "Command:"}}}
        if method == "ui.render":
            ok, encoded = cv2.imencode(".png", np.zeros((2, 2, 3), dtype=np.uint8))
            return {"id": request.get("id"), "ok": True, "result": {
                "pngBase64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                "width": 2, "height": 2}}
        return {"id": request.get("id"), "ok": False, "error": "unknown method"}

    def connect(self, address, timeout=None):
        return _FakeSocket(self)


class CADCraftDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeCADCraft()
        self._real_connect = cadcraft_direct.socket.create_connection
        cadcraft_direct.socket.create_connection = self.server.connect
        self._previous_type = cad_direct.config["cad"].get("type")
        self._previous_port = cad_direct.config["cad"].get("cadcraft_port")
        self.controller = cadcraft_direct.CADCraftController(
            port=43210, launch=False)
        cad_direct.config["cad"]["type"] = "CADCRAFT"
        cad_direct.config["cad"]["cadcraft_port"] = 43210

    def tearDown(self) -> None:
        self.controller.close()
        cadcraft_direct.socket.create_connection = self._real_connect
        cad_direct.config["cad"]["type"] = self._previous_type
        cad_direct.config["cad"]["cadcraft_port"] = self._previous_port

    def _commands(self):
        return [params.get("command")
                for method, params in self.server.requests
                if method == "engine.execute"]

    def test_draw_line_maps_points_and_style(self) -> None:
        entity = self.controller.draw_line(
            [0, 0], [1000, 0], layer="A-WALL", color=1, lineweight=25)
        self.assertTrue(entity.Handle)
        self.assertIn("line", self._commands())
        style = next(params["params"] for method, params in self.server.requests
                     if method == "engine.execute"
                     and params.get("command") == "properties.set")
        self.assertEqual(style["layer"], "A-WALL")
        self.assertEqual(style["color"], 1)
        self.assertAlmostEqual(style["lineweight"], 0.25)
        line = next(params["params"] for method, params in self.server.requests
                    if method == "engine.execute"
                    and params.get("command") == "line")
        self.assertEqual(line["points"], [[0.0, 0.0], [1000.0, 0.0]])

    def test_drawing_commands_map_to_cadcraft(self) -> None:
        self.controller.draw_polyline([[0, 0], [10, 0], [10, 10]], closed=True)
        self.controller.draw_circle([0, 0], 5)
        self.controller.draw_arc([0, 0], 5, 0, 90)
        self.controller.draw_ellipse([0, 0], 10, 5, rotation=30)
        self.controller.draw_text([0, 0], "TEST", height=250)
        self.controller.add_dimension([0, 0], [1000, 0])
        commands = self._commands()
        for expected in ("pline", "circle", "arc", "ellipse", "text", "dimaligned"):
            self.assertIn(expected, commands)

    def test_hatch_creates_boundary_and_hatch(self) -> None:
        self.controller.draw_hatch(
            [[0, 0], [10, 0], [10, 10], [0, 10]], pattern_name="SOLID",
            layer="A-HATCH")
        commands = self._commands()
        self.assertIn("pline", commands)
        self.assertIn("hatch", commands)

    def test_layers_stats_and_entity_filter(self) -> None:
        layers = self.controller.list_layers()
        self.assertTrue(layers[0]["is_current"])
        stats = self.controller.get_drawing_stats()
        self.assertEqual(stats["entity_count"], 2)
        entities = self.controller.list_entities("line", limit=5)
        self.assertEqual(entities["total"], 2)
        self.assertEqual([item["entity_type"] for item in entities["entities"]], ["line"])

    def test_properties_normalizes_objects_wrapper(self) -> None:
        props = self.controller.get_entity_properties("100")
        self.assertEqual(props["entity_type"], "line")
        self.assertEqual(props["layer"], "A-WALL")
        self.assertEqual(props["properties"]["geometry"]["b"]["x"], 1000)

    def test_screenshot_decodes_png(self) -> None:
        data = self.controller.screenshot()
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_cli_routes_to_cadcraft(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cad_direct.main([
                "--cad-type", "CADCRAFT",
                "--cadcraft-port", "43210",
                "stats",
            ])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["entity_count"], 2)

    def test_cadcraft_does_not_load_windows_core(self) -> None:
        """CADCRAFT 后端不能被 cad_core/pywin32 的导入路径挡住。"""
        original = cad_direct._load_core
        cad_direct._load_core = lambda: (_ for _ in ()).throw(
            AssertionError("CADCRAFT 不应加载 COM 驱动"))
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cad_direct.main([
                    "--cad-type", "CADCRAFT",
                    "--cadcraft-port", "43210",
                    "stats",
                ])
        finally:
            cad_direct._load_core = original
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["entity_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
