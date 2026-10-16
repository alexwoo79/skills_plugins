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
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fake_cad import (Counter, FakeDoc, LAYER_NAMES, make_controller,  # noqa: E402
                      simulate_prefix_draw)


class LayerCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counter = Counter()
        self.doc = FakeDoc(self.counter)
        self.controller = make_controller(self.doc)

    def test_layer_lookup_is_cached(self) -> None:
        """首次解析付一次性枚举成本，之后命中缓存为 0 次往返。"""
        self.counter.reset()
        name = self.controller._ensure_layer_entry("A-WALL")[1]
        self.assertEqual(name, "A-WALL")
        self.assertEqual(self.counter.hops, 2 + 2 * len(LAYER_NAMES))  # Layers + Count + 每层 Item/Name

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
