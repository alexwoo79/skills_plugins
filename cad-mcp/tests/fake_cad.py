#!/usr/bin/env python3
"""离线测试用的假 CAD/COM 对象。

非 Windows 上 `win32com.client` / `pythoncom` 不存在，而 cad_controller
在导入期就会 import 它们并在失败时直接 raise——所以本模块先注入假模块，
测试文件只要 `import fake_cad` 就能拿到可导入的 CADController。

所有假对象的属性读取/方法调用都会计入 Counter（等价于一次跨进程 COM 往返），
用来量化"改前 / 改后"的往返次数，以及验证 run_script 的脚本落盘行为。
"""

from __future__ import annotations

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_HERE, _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

fake_client = types.ModuleType("win32com.client")
fake_client.VARIANT = lambda vt, value: value  # 假 VARIANT 直接返回原值，便于断言
fake_client.Dispatch = lambda *a, **k: None
fake_client.GetActiveObject = lambda *a, **k: None
fake_win32com = types.ModuleType("win32com")
fake_win32com.client = fake_client

fake_pythoncom = types.ModuleType("pythoncom")
fake_pythoncom.CoInitialize = lambda: None
fake_pythoncom.VT_ARRAY = 8192
fake_pythoncom.VT_R8 = 5
fake_pythoncom.VT_DISPATCH = 9

sys.modules.setdefault("win32com", fake_win32com)
sys.modules.setdefault("win32com.client", fake_client)
sys.modules.setdefault("pythoncom", fake_pythoncom)

from src.cad_controller import CADController  # noqa: E402

LAYER_NAMES = ["0", "A-WALL", "A-DOOR", "A-WINDOW", "A-DIM", "A-TEXT", "A-FURN", "A-GRID"]

# 脚本里能产生实体的命令前缀：只用于假的"执行结果"统计
DRAW_COMMANDS = ("_.LINE", "_.PLINE", "_.CIRCLE", "_.ARC", "_.ELLIPSE",
                 "_.-TEXT", "_.DIMLINEAR")


class Counter:
    """记录 COM 往返次数：假对象每次属性读取/方法调用都 +1。"""

    def __init__(self) -> None:
        self.hops = 0

    def bump(self) -> None:
        self.hops += 1

    def reset(self) -> None:
        self.hops = 0


class FakeLayer:
    def __init__(self, name: str, counter: Counter) -> None:
        self._name = name
        self._counter = counter

    @property
    def Name(self) -> str:
        self._counter.bump()
        return self._name


class FakeEntity:
    """实体的 Layer 属性：图层不存在时模拟 CAD 报错（缓存失效场景）。"""

    def __init__(self, doc: "FakeDoc") -> None:
        self._doc = doc
        self._layer = None

    @property
    def Layer(self):
        self._doc.counter.bump()
        return self._layer

    @Layer.setter
    def Layer(self, name: str) -> None:
        self._doc.counter.bump()
        if name.casefold() not in {n.casefold() for n in self._doc.layer_names}:
            # 与CAD的行为一致：图层被删掉后再往上写会失败
            raise RuntimeError(f"图层不存在: {name}")
        self._layer = name

    @property
    def Handle(self) -> str:
        self._doc.counter.bump()
        return "1F"


class FakeModelSpace:
    def __init__(self, doc: "FakeDoc") -> None:
        self._doc = doc

    @property
    def Count(self) -> int:
        self._doc.counter.bump()
        return len(self._doc.entities)

    def AddLine(self, start, end) -> FakeEntity:
        self._doc.counter.bump()
        entity = FakeEntity(self._doc)
        self._doc.entities.append(entity)
        return entity

    def Item(self, index: int) -> FakeEntity:
        self._doc.counter.bump()
        return self._doc.entities[index]


class FakeLayers:
    def __init__(self, doc: "FakeDoc") -> None:
        self._doc = doc
        self._layers: list[FakeLayer] = []

    @property
    def Count(self) -> int:
        self._doc.counter.bump()
        return len(self._layers)

    def Item(self, index: int) -> FakeLayer:
        self._doc.counter.bump()
        return self._layers[index]

    def Add(self, name: str) -> FakeLayer:
        self._doc.counter.bump()
        layer = FakeLayer(name, self._doc.counter)
        self._layers.append(layer)
        self._doc.layer_names.append(name)
        return layer

    def remove(self, name: str) -> None:
        self._layers = [layer for layer in self._layers if layer._name != name]


class FakeDoc:
    """假文档：除图层/模型空间外，支持 SendCommand/GetVariable 以测试 run_script。

    honor_sentinel=False 模拟"CAD不支持 USERS1"：脚本永远不会写哨兵变量。
    polls_before_done 模拟 SCRIPT 的异步性（提交后要过几次查询才跑完）。
    """

    def __init__(self, counter: Counter, layer_names=None, honor_sentinel: bool = True,
                 polls_before_done: int = 2) -> None:
        self.counter = counter
        self.layer_names = list(layer_names or LAYER_NAMES)
        self.layers_collection = FakeLayers(self)
        for name in self.layer_names:
            self.layers_collection._layers.append(FakeLayer(name, counter))
        self.model_space = FakeModelSpace(self)
        self.entities: list[FakeEntity] = []
        self.variables: dict = {}
        self.honor_sentinel = honor_sentinel
        self.polls_before_done = polls_before_done
        self.sent_commands: list[str] = []
        self._pending_script = None
        self._polls_left = 0

    # ---- 文档基础属性 ----
    @property
    def Name(self) -> str:
        self.counter.bump()
        return "fake.dwg"

    @property
    def Layers(self) -> FakeLayers:
        self.counter.bump()
        return self.layers_collection

    @property
    def ModelSpace(self) -> FakeModelSpace:
        self.counter.bump()
        return self.model_space

    @property
    def ActiveLayer(self) -> FakeLayer:
        self.counter.bump()
        return self.layers_collection._layers[0]

    def StartUndoMark(self) -> None:
        self.counter.bump()

    def EndUndoMark(self) -> None:
        self.counter.bump()

    def delete_layer(self, name: str) -> None:
        """模拟用户在CAD界面里删掉图层（缓存会因此变脏）"""
        self.layer_names = [n for n in self.layer_names if n != name]
        self.layers_collection.remove(name)

    # ---- 脚本执行 ----
    def SendCommand(self, command: str) -> None:
        self.counter.bump()
        self.sent_commands.append(command)
        path = command.strip()
        path = path[len("_.SCRIPT"):].strip().strip('"') if path.startswith("_.SCRIPT") else path
        with open(path, encoding="utf-8-sig") as handle:
            self._pending_script = handle.read()
        self._polls_left = self.polls_before_done

    def GetVariable(self, name: str):
        self.counter.bump()
        if self._pending_script is not None:
            if self._polls_left > 0:
                self._polls_left -= 1
            else:
                self._finish_script()
        return self.variables.get(name)

    def _finish_script(self) -> None:
        """模拟脚本执行：按脚本内容加实体，并写入 USERS1 哨兵"""
        script = self._pending_script or ""
        self._pending_script = None
        lines = [line.strip() for line in script.splitlines()]
        for index, line in enumerate(lines):
            if line.startswith(DRAW_COMMANDS):
                self.entities.append(FakeEntity(self))
            elif self.honor_sentinel and line == "USERS1" and index + 1 < len(lines):
                self.variables["USERS1"] = lines[index + 1]


def make_controller(doc: FakeDoc) -> CADController:
    controller = CADController()
    controller.app = object()  # is_running() 要求 app 与 doc 都非空
    controller.doc = doc
    return controller


def simulate_prefix_draw(doc: FakeDoc, layer_name: str) -> None:
    """复刻补丁前的调用序列：模型空间 + 撤销标记 + 全量图层枚举
    + AddLine + 每个实体再读一次真实图层名。"""
    model_space = doc.ModelSpace
    doc.StartUndoMark()
    line = model_space.AddLine([0, 0, 0], [100, 0, 0])
    layers = doc.Layers
    found = None
    for i in range(layers.Count):
        candidate = layers.Item(i)
        if candidate.Name.casefold() == layer_name.casefold():
            found = candidate
            break
    setattr(line, "Layer", found.Name)
    doc.EndUndoMark()
