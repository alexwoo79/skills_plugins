"""离线测试用的假 CAD 文档/应用对象（不依赖 Windows 与 CAD）。

配合同目录的假 `win32com.client` / `pythoncom` 使用，让 `vendor/cad_core.py`
能在 macOS/Linux 上被导入并真正跑一遍调用路径。
"""

from __future__ import annotations

LAYER_NAMES = ["0", "A-WALL", "A-DOOR", "A-WINDOW", "A-DIM", "A-TEXT", "A-FURN", "A-GRID"]

DRAW_COMMANDS = ("_.LINE", "_.PLINE", "_.CIRCLE", "_.ARC", "_.ELLIPSE",
                 "_.-TEXT", "_.DIMLINEAR")


class Counter:
    """记录 COM 往返次数（每次属性读取/方法调用 +1）。"""

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
    """实体的 Layer 属性：图层不存在时报错（缓存失效场景）。"""

    def __init__(self, doc: "FakeDoc", handle: str) -> None:
        self._doc = doc
        self._layer = None
        self._handle = handle

    @property
    def Layer(self):
        self._doc.counter.bump()
        return self._layer

    @Layer.setter
    def Layer(self, name: str) -> None:
        self._doc.counter.bump()
        if name.casefold() not in {n.casefold() for n in self._doc.layer_names}:
            raise RuntimeError(f"图层不存在: {name}")
        self._layer = name

    @property
    def Handle(self) -> str:
        self._doc.counter.bump()
        return self._handle

    @property
    def ObjectName(self) -> str:
        self._doc.counter.bump()
        return "AcDbLine"

    def Delete(self) -> None:
        self._doc.counter.bump()
        self._doc.entities.remove(self)

    # ---- 编辑方法（假实现：只计数并返回新实体）----
    def Move(self, *_args) -> None:
        self._doc.counter.bump()

    def Rotate(self, *_args) -> None:
        self._doc.counter.bump()

    def ScaleEntity(self, *_args) -> None:
        self._doc.counter.bump()

    def Copy(self) -> "FakeEntity":
        self._doc.counter.bump()
        clone = FakeEntity(self._doc, f"{len(self._doc.entities) + 1}F")
        self._doc.entities.append(clone)
        return clone

    def Mirror(self, *_args) -> "FakeEntity":
        return self.Copy()

    def Offset(self, *_args):
        return [self.Copy()]


class FakeModelSpace:
    def __init__(self, doc: "FakeDoc") -> None:
        self._doc = doc

    @property
    def Count(self) -> int:
        self._doc.counter.bump()
        return len(self._doc.entities)

    def _add(self) -> FakeEntity:
        self._doc.counter.bump()
        entity = FakeEntity(self._doc, f"{len(self._doc.entities) + 1}F")
        self._doc.entities.append(entity)
        return entity

    def AddLine(self, *_args):
        return self._add()

    def AddCircle(self, *_args):
        return self._add()

    def AddArc(self, *_args):
        return self._add()

    def AddEllipse(self, *_args):
        return self._add()

    def AddPolyline(self, *_args):
        return self._add()

    def AddText(self, *_args):
        return self._add()

    def AddHatch(self, *_args):
        return self._add()

    def AddDimAligned(self, *_args):
        return self._add()

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
    """假文档：支持绘图、图层、脚本执行（SendCommand/GetVariable）与保存。"""

    def __init__(self, counter: Counter | None = None, honor_sentinel: bool = True,
                 polls_before_done: int = 2) -> None:
        self.counter = counter or Counter()
        self.layer_names = list(LAYER_NAMES)
        self.layers_collection = FakeLayers(self)
        for name in self.layer_names:
            self.layers_collection._layers.append(FakeLayer(name, self.counter))
        self.model_space = FakeModelSpace(self)
        self.entities: list[FakeEntity] = []
        self.variables: dict = {}
        self.honor_sentinel = honor_sentinel
        self.polls_before_done = polls_before_done
        self.sent_commands: list[str] = []
        self.saved_to: list[str] = []
        self.full_name = ""
        self._pending_script = None
        self._polls_left = 0

    # ---- 文档属性 ----
    @property
    def Name(self) -> str:
        self.counter.bump()
        return "fake.dwg"

    @property
    def FullName(self) -> str:
        self.counter.bump()
        return self.full_name or "fake.dwg"

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

    def Regen(self, *_args) -> None:
        self.counter.bump()

    def SaveAs(self, path: str) -> None:
        self.counter.bump()
        self.saved_to.append(path)
        self.full_name = path

    def Close(self, _save=False) -> None:
        self.counter.bump()

    def HandleToObject(self, handle: str) -> FakeEntity:
        self.counter.bump()
        for entity in self.entities:
            if entity._handle == str(handle):
                return entity
        raise ValueError(f"未找到句柄为 {handle} 的实体")

    # ---- 脚本执行 ----
    def SendCommand(self, command: str) -> None:
        self.counter.bump()
        self.sent_commands.append(command)
        text = command.strip()
        if not text.startswith("_.SCRIPT"):
            # UNDO / REDO / 任意命令：假实现只记录
            return
        path = text[len("_.SCRIPT"):].strip().strip('"')
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
        script = self._pending_script or ""
        self._pending_script = None
        lines = [line.strip() for line in script.splitlines()]
        for index, line in enumerate(lines):
            if line.startswith(DRAW_COMMANDS):
                FakeModelSpace(self)._add()
            elif self.honor_sentinel and line == "USERS1" and index + 1 < len(lines):
                self.variables["USERS1"] = lines[index + 1]


class FakeApp:
    """假 CAD 应用：Documents 集合 + 活动文档 + ZoomExtents。"""

    def __init__(self, doc: FakeDoc | None = None) -> None:
        self.counter = Counter()
        self.doc = doc or FakeDoc(self.counter)
        self.Visible = True

    @property
    def Documents(self) -> "FakeDocuments":
        self.counter.bump()
        return FakeDocuments(self)

    @property
    def ActiveDocument(self) -> FakeDoc:
        self.counter.bump()
        return self.doc

    def ZoomExtents(self) -> None:
        self.counter.bump()

    @property
    def HWND(self) -> int:
        self.counter.bump()
        return 0


class FakeDocuments:
    def __init__(self, app: FakeApp) -> None:
        self._app = app

    @property
    def Count(self) -> int:
        self._app.counter.bump()
        return 1

    def Item(self, _index: int) -> FakeDoc:
        self._app.counter.bump()
        return self._app.doc

    def Add(self) -> FakeDoc:
        self._app.counter.bump()
        self._app.doc = FakeDoc(self._app.counter)
        return self._app.doc


_ACTIVE_APP: FakeApp | None = None


def get_active_app() -> FakeApp:
    """进程内单例：CLI 每次运行只会连接一次"""
    global _ACTIVE_APP
    if _ACTIVE_APP is None:
        _ACTIVE_APP = FakeApp()
    return _ACTIVE_APP


def reset_active_app() -> FakeApp:
    global _ACTIVE_APP
    _ACTIVE_APP = FakeApp()
    return _ACTIVE_APP
