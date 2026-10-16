#!/usr/bin/env python3
"""图层/模型空间缓存的离线回归测试（不需要 Windows、不需要 CAD）。

用假的 COM 对象记录每一次属性访问与方法调用（等价于一次跨进程往返），
以此证明两点：
  1. 补丁后单条实体的往返次数从 ~6+2×图层数 降到常数级；
  2. 缓存不会把旧文档、已删除图层的对象用错——失效路径有覆盖。

运行：python3 tests/test_layer_cache.py
"""

from __future__ import annotations

import os
import sys
import types
import unittest

# 让本文件既能直接运行，也能被 unittest discover 找到
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# cad_controller 在导入期就会 `import win32com.client` / `import pythoncom`
# 并在失败时直接 raise，因此非 Windows 上必须先注入假模块才能导入。
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
    def __init__(self, counter: Counter, layer_names=None) -> None:
        self.counter = counter
        self.layer_names = list(layer_names or LAYER_NAMES)
        self.layers_collection = FakeLayers(self)
        for name in self.layer_names:
            self.layers_collection._layers.append(FakeLayer(name, counter))
        self.model_space = FakeModelSpace(self)
        self.entities: list[FakeEntity] = []

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


class LayerCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counter = Counter()
        self.doc = FakeDoc(self.counter)
        self.controller = make_controller(self.doc)

    def test_layer_lookup_is_cached(self) -> None:
        """首次解析付枚举成本，之后命中缓存为 0 次往返。"""
        self.counter.reset()
        name = self.controller._ensure_layer_entry("A-WALL")[1]
        self.assertEqual(name, "A-WALL")
        first = self.counter.hops
        self.assertEqual(first, 2 + 2 * len(LAYER_NAMES))  # Layers + Count + 每层 Item/Name

        self.counter.reset()
        self.controller._ensure_layer_entry("a-wall")  # 大小写不敏感
        self.assertEqual(self.counter.hops, 0)

    def test_first_enumeration_seeds_every_layer(self) -> None:
        """一次枚举顺手缓存全部图层：换图层绘制不再付枚举成本。"""
        self.controller._ensure_layer_entry("A-WALL")
        self.counter.reset()
        self.controller._ensure_layer_entry("A-DOOR")
        self.assertEqual(self.counter.hops, 0)

    def test_draw_line_round_trips(self) -> None:
        """补丁后单条实体的往返次数，与补丁前同场景对比。"""
        # 预热缓存（真实工作流里第一批实体付一次枚举成本）
        self.controller.draw_line([0, 0], [100, 0], layer="A-WALL")
        # 取最后一个图层作为对比场景：补丁前它要走满一整轮枚举
        self.counter.reset()
        self.controller.draw_line([0, 0], [100, 0], layer="A-GRID")
        patched = self.counter.hops

        self.counter.reset()
        simulate_prefix_draw(self.doc, "A-GRID")
        prefix = self.counter.hops

        self.assertLessEqual(patched, 5, f"补丁后应有常数级往返，实际 {patched}")
        self.assertGreaterEqual(prefix, 20, f"补丁前应为 6+2N 量级，实际 {prefix}")
        self.assertGreaterEqual(prefix / patched, 4.0,
                                f"补丁前后差距不足 4 倍：{prefix} vs {patched}")

    def test_document_change_invalidates_caches(self) -> None:
        """换文档后必须重新取 ModelSpace 与图层，不能复用旧对象。"""
        self.controller.draw_line([0, 0], [100, 0], layer="A-WALL")
        self.controller.doc = FakeDoc(self.counter)
        self.counter.reset()
        self.controller._ms()
        self.assertEqual(self.counter.hops, 1)  # 重新取了一次 ModelSpace
        self.counter.reset()
        self.controller._ensure_layer_entry("A-WALL")
        self.assertEqual(self.counter.hops, 2 + 2 * len(LAYER_NAMES))  # 重新枚举

    def test_deleted_layer_recovers(self) -> None:
        """图层被CAD界面删掉后，缓存命中失败要能自愈并重建图层。"""
        self.controller.draw_line([0, 0], [100, 0], layer="A-WALL")
        self.doc.delete_layer("A-WALL")

        entity = self.controller.draw_line([0, 0], [100, 0], layer="A-WALL")
        self.assertIsNotNone(entity)
        self.assertIn("A-WALL", self.doc.layer_names)
        self.assertEqual(entity.Layer, "A-WALL")
        self.assertIn("a-wall", self.controller._layer_cache)


if __name__ == "__main__":
    unittest.main(verbosity=2)
