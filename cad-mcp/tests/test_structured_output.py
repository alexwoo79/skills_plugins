#!/usr/bin/env python3
"""结构化输出与工具注册的离线检查。

服务端每个工具都返回 Pydantic 模型（FastMCP 据此生成 outputSchema），
所以 run_script 返回的 dict 必须能被 ScriptResult 完整接住；字段名写错
在真机上只会表现为客户端拿不到结构化内容，因此这里直接校验。

另外用 AST 静态检查 draw_script 是否真的挂着 @mcp.tool() 装饰器
（本机没有 mcp 包，无法真正启动 FastMCP）。

运行：python3 tests/test_structured_output.py
"""

from __future__ import annotations

import ast
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_HERE, _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_cad import Counter, FakeDoc, make_controller  # noqa: E402

try:
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    from models import ScriptResult  # noqa: E402
except ImportError:  # pragma: no cover - pydantic 缺失时跳过
    ScriptResult = None

SERVER_PATH = os.path.join(_ROOT, "src", "server.py")


@unittest.skipIf(ScriptResult is None, "本机没有 pydantic，跳过结构化输出检查")
class ScriptResultModelTests(unittest.TestCase):
    def test_run_script_result_maps_into_model(self) -> None:
        """run_script 的返回值必须字段齐全地进得了 ScriptResult。"""
        controller = make_controller(FakeDoc(Counter()))
        result = controller.run_script("_.LINE\n0,0\n100,0\n", file_path="/tmp/model_check.scr")
        model = ScriptResult(**result)  # 多字段会报错、缺字段会用默认值

        self.assertEqual(model.success, result["success"])
        self.assertEqual(model.script_path, result["script_path"])
        self.assertEqual(model.completed, result["completed"])
        self.assertEqual(model.entities_created, result["entities_created"])
        self.assertEqual(model.lines, result["lines"])
        self.assertEqual(model.error, result["error"])
        os.remove("/tmp/model_check.scr")


class ToolRegistrationTests(unittest.TestCase):
    """静态检查 MCP 工具注册（本机无 mcp 包，不能真跑 FastMCP）。"""

    @classmethod
    def setUpClass(cls) -> None:
        with open(SERVER_PATH, encoding="utf-8") as handle:
            cls.tree = ast.parse(handle.read())

    def _decorated_tools(self) -> dict:
        tools = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute) and target.attr == "tool":
                    tools[node.name] = node
        return tools

    def test_draw_script_is_registered(self) -> None:
        tools = self._decorated_tools()
        self.assertIn("draw_script", tools, "draw_script 必须挂 @mcp.tool()")
        args = [arg.arg for arg in tools["draw_script"].args.args]
        for expected in ("script", "file_path", "encoding", "timeout", "sentinel"):
            self.assertIn(expected, args, f"draw_script 缺少参数 {expected}")
        self.assertTrue(ast.get_docstring(tools["draw_script"]), "工具需要文档串")

    def test_draw_script_is_marked_destructive(self) -> None:
        """脚本可含 ERASE 等破坏性命令，必须带 destructiveHint。"""
        with open(SERVER_PATH, encoding="utf-8") as handle:
            source = handle.read()
        index = source.index("async def draw_script")
        decorator = source[:index].rsplit("@mcp.tool(", 1)[-1]
        self.assertIn("destructiveHint=True", decorator)

    def test_script_result_is_imported_in_both_branches(self) -> None:
        """包方式与脚本方式两种启动路径都要能拿到 ScriptResult。"""
        with open(SERVER_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("ListBlocksResult,\n                         ScriptResult)", source,
                      "包方式 import（from .models）缺少 ScriptResult")
        self.assertIn("ListBlocksResult,\n                        ScriptResult)", source,
                      "脚本方式 import（from models）缺少 ScriptResult")
        self.assertIn("async def draw_script", source)
        self.assertIn("-> ScriptResult", source, "工具返回类型应标注为 ScriptResult")


if __name__ == "__main__":
    unittest.main(verbosity=2)
