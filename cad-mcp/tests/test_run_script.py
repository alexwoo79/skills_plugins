#!/usr/bin/env python3
"""run_script（批量脚本执行）的离线回归测试。

验证：脚本按 CRLF + 指定编码落盘、提交给 CAD 的是 SCRIPT 命令、
末尾哨兵（USERS1 令牌）能确认执行完成、实体数前后差值被报告，
以及空脚本/超大脚本/非法编码被挡下、哨兵超时不误报成功。

运行：python3 tests/test_run_script.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fake_cad import Counter, FakeDoc, make_controller  # noqa: E402
import src.cad_controller as controller_module  # noqa: E402

SCRIPT = "\n".join([
    "_.-LAYER", "M", "A-WALL", "",
    "_.LINE", "0,0", "5000,0", "",
    "_.PLINE", "0,0", "5000,0", "5000,4000", "0,4000", "C",
    "_.-TEXT", "1000,1000", "300", "0", "客厅",
])


class RunScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counter = Counter()
        self.doc = FakeDoc(self.counter)
        self.controller = make_controller(self.doc)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "plan.scr")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_script_written_and_executed(self) -> None:
        result = self.controller.run_script(SCRIPT, file_path=self.path)

        self.assertTrue(result["success"], result)
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["script_path"], os.path.abspath(self.path))

        # 提交给CAD的是一条 SCRIPT 命令，而不是逐图元调用
        self.assertEqual(len(self.doc.sent_commands), 1)
        self.assertTrue(self.doc.sent_commands[0].startswith('_.SCRIPT "'), self.doc.sent_commands)

        with open(self.path, "rb") as handle:
            raw = handle.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "默认输出 UTF-8 BOM")
        self.assertIn(b"\r\n", raw, ".scr 需要 CRLF")
        text = raw.decode("utf-8-sig")
        self.assertIn("客厅", text)
        # 哨兵：USERS1 + 随机令牌，且令牌与实体内容不同
        token = self.doc.variables.get("USERS1")
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 32)
        self.assertIn(f"USERS1\r\n{token}", text)

        # 实体数前后差值被报告（脚本里 3 条绘图命令 → 假执行加 3 个实体）
        self.assertEqual(result["entities_before"], 0)
        self.assertEqual(result["entities_after"], 3)
        self.assertEqual(result["entities_created"], 3)
        self.assertIn("实体数 0 → 3", result["message"])

    def test_sentinel_timeout_is_not_reported_as_complete(self) -> None:
        """CAD不支持 USERS1 时：脚本仍算提交成功，但必须标记未确认完成。"""
        self.doc.honor_sentinel = False
        result = self.controller.run_script(SCRIPT, file_path=self.path, timeout=0.6)

        self.assertTrue(result["success"])
        self.assertFalse(result["completed"])
        self.assertIn("未确认执行结束", result["message"])

    def test_no_sentinel_mode(self) -> None:
        """sentinel=False 时不追加 USERS1，也不做完成确认。"""
        result = self.controller.run_script(SCRIPT, file_path=self.path, sentinel=False)
        self.assertTrue(result["success"])
        self.assertTrue(result["completed"])
        with open(self.path, encoding="utf-8-sig") as handle:
            self.assertNotIn("USERS1", handle.read())

    def test_rejects_empty_oversize_and_bad_encoding(self) -> None:
        empty = self.controller.run_script("   \n  ", file_path=self.path)
        self.assertFalse(empty["success"])
        self.assertIn("脚本内容为空", empty["message"])

        oversize = self.controller.run_script("_.LINE\n0,0\n1,1\n" * 400000, file_path=self.path)
        self.assertFalse(oversize["success"])
        self.assertIn("脚本过大", oversize["message"])

        bad_encoding = self.controller.run_script(SCRIPT, file_path=self.path, encoding="utf-99")
        self.assertFalse(bad_encoding["success"])
        self.assertIn("无法用 utf-99 编码", bad_encoding["message"])

        self.assertEqual(self.doc.sent_commands, [], "被挡下的脚本不能提交给CAD")

    def test_default_path_uses_output_directory(self) -> None:
        original = controller_module.config["output"]["directory"]
        try:
            controller_module.config["output"]["directory"] = self.tmp.name
            result = self.controller.run_script(SCRIPT)
            self.assertTrue(os.path.exists(result["script_path"]), result)
            self.assertTrue(result["script_path"].startswith(os.path.abspath(self.tmp.name)))
            self.assertTrue(os.path.basename(result["script_path"]).endswith(".scr"))
        finally:
            controller_module.config["output"]["directory"] = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
