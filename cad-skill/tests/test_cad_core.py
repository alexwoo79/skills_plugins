#!/usr/bin/env python3
"""内联驱动（vendor/cad_core.py）与 CLI（scripts/cad_direct.py）的离线测试。

不需要 Windows、不需要 CAD：注入假 win32com/pythoncom 后，真实地跑一遍
连接 → 绘图 → 脚本批量 → 查询 → 保存的调用路径，并对 CLI 的输出契约
（stdout JSON、退出码）做断言。

运行：python3 tests/test_cad_core.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
FAKE_COM = os.path.join(_HERE, "fake_com")
VENDOR = os.path.join(_SKILL, "vendor")
SCRIPTS = os.path.join(_SKILL, "scripts")
for _path in (FAKE_COM, VENDOR, SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import cad_core  # noqa: E402
import cad_direct  # noqa: E402
from fake_doc import reset_active_app  # noqa: E402


def _real_pywin32_available() -> bool:
    """在干净子进程里探测真实 pywin32（测试进程自己的 sys.path 里有假 win32com，
    所以不能用 find_spec 判断）。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    probe = subprocess.run([sys.executable, "-c", "import win32com.client, pythoncom; print('yes')"],
                           capture_output=True, text=True, env=env, timeout=60)
    return probe.stdout.strip() == "yes"


REAL_PYWIN32 = _real_pywin32_available()


def run_cli(argv):
    """在进程内跑 CLI，返回 (退出码, 解析后的 JSON)"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cad_direct.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


class CadCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = reset_active_app()
        self.doc = self.app.doc
        self.controller = cad_direct.connect()

    def test_layer_lookup_is_cached(self) -> None:
        counter = self.doc.counter
        counter.reset()
        self.controller._ensure_layer_entry("A-WALL")
        first = counter.hops
        self.assertEqual(first, 2 + 2 * len(self.doc.layer_names))
        counter.reset()
        self.controller._ensure_layer_entry("a-wall")
        self.assertEqual(counter.hops, 0)

    def test_draw_line_uses_constant_round_trips(self) -> None:
        self.controller.draw_line([0, 0], [5000, 0], layer="A-WALL")
        self.doc.counter.reset()
        self.controller.draw_line([0, 0], [5000, 0], layer="A-WALL")
        self.assertLessEqual(self.doc.counter.hops, 5)

    def test_run_script_executes_once_with_sentinel(self) -> None:
        script = "_.-LAYER\nM\nA-WALL\n\n_.LINE\n0,0\n5000,0\n\n"
        result = self.controller.run_script(script)
        self.assertTrue(result["success"])
        self.assertTrue(result["completed"])
        self.assertEqual(len(self.doc.sent_commands), 1)
        self.assertTrue(self.doc.sent_commands[0].startswith('_.SCRIPT "'))
        self.assertEqual(result["entities_created"], 1)
        with open(result["script_path"], "rb") as handle:
            raw = handle.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"USERS1", raw)
        os.remove(result["script_path"])


class CadDirectCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = reset_active_app()
        self.doc = self.app.doc
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _calls_file(self, calls) -> str:
        path = os.path.join(self.tmp.name, "calls.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"calls": calls}, handle, ensure_ascii=False)
        return path

    def test_layers_and_stats(self) -> None:
        code, payload = run_cli(["layers"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("A-WALL", [layer["name"] for layer in payload["layers"]])

        code, payload = run_cli(["stats"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["document_name"], "fake.dwg")

    def test_draw_runs_the_call_sequence(self) -> None:
        calls = [
            {"tool": "draw_polyline", "args": {"points": [[0, 0], [5000, 0], [5000, 4000], [0, 4000]],
                                               "closed": True, "layer": "A-WALL"}},
            {"tool": "draw_line", "args": {"start_point": [0, 0], "end_point": [5000, 0],
                                           "layer": "A-GRID"}},
            {"tool": "draw_text", "args": {"position": [1000, 1000], "text": "客厅",
                                           "height": 300, "layer": "A-TEXT"}},
        ]
        code, payload = run_cli(["draw", "--calls", self._calls_file(calls)])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["drawn"], 3)
        self.assertEqual(len(payload["handles"]), 3)
        self.assertEqual(len(self.doc.entities), 3)
        # 每个图元都带对图层（假实体的 Layer 只在图层存在时才允许写入）
        self.assertEqual([entity.Layer for entity in self.doc.entities],
                         ["A-WALL", "A-GRID", "A-TEXT"])

    def test_draw_dry_run_does_not_touch_cad(self) -> None:
        calls = [{"tool": "draw_line", "args": {"start_point": [0, 0], "end_point": [1, 1]}}]
        code, payload = run_cli(["draw", "--calls", self._calls_file(calls), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(self.doc.entities, [])

    def test_dry_run_works_without_pywin32(self) -> None:
        """校验契约应该能在没有 CAD 的机器上跑；真正落图才要求 pywin32。"""
        calls = [{"tool": "draw_line", "args": {"start_point": [0, 0], "end_point": [1, 1]}}]
        original = cad_direct._has_cad
        cad_direct._has_cad = lambda: False
        try:
            code, payload = run_cli(["draw", "--calls", self._calls_file(calls), "--dry-run"])
            self.assertEqual(code, 0, payload)
            self.assertTrue(payload["dry_run"])

            code, payload = run_cli(["stats"])
            self.assertEqual(code, 1)
            self.assertIn("pywin32", payload["error"])
        finally:
            cad_direct._has_cad = original

    def test_draw_rejects_bad_spec(self) -> None:
        calls = [{"tool": "draw_lwpolyline", "args": {}}]
        code, payload = run_cli(["draw", "--calls", self._calls_file(calls)])
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])

        code, payload = run_cli(["draw", "--calls", os.path.join(self.tmp.name, "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("读取调用序列失败", payload["error"])

    def test_script_batch(self) -> None:
        path = os.path.join(self.tmp.name, "plan.scr")
        with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write("_.LINE\n0,0\n5000,0\n\n_.LINE\n0,0\n0,4000\n\n")
        code, payload = run_cli(["script", "--file", path, "--out",
                                 os.path.join(self.tmp.name, "out.scr")])
        self.assertEqual(code, 0)
        self.assertTrue(payload["completed"])
        self.assertEqual(payload["entities_created"], 2)

    def test_shot_writes_png(self) -> None:
        original = cad_core.CADController.screenshot
        cad_core.CADController.screenshot = lambda self: b"\x89PNG\r\n\x1a\n"
        try:
            out = os.path.join(self.tmp.name, "shot.png")
            code, payload = run_cli(["shot", "--out", out])
        finally:
            cad_core.CADController.screenshot = original
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(payload["image"]))

    def test_save_and_edit_commands(self) -> None:
        controller = cad_direct.connect()
        entity = controller.draw_line([0, 0], [100, 0], layer="A-WALL")
        handle = str(entity.Handle)

        code, payload = run_cli(["move", "--handle", handle, "--dx", "100", "--dy", "0"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])

        code, payload = run_cli(["offset", "--handle", handle, "--distance", "120"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])

        path = os.path.join(self.tmp.name, "plan.dwg")
        code, payload = run_cli(["save", "--path", path])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.doc.saved_to, [os.path.abspath(path)])

        code, payload = run_cli(["undo", "--times", "2"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])


class CliSubprocessTests(unittest.TestCase):
    """真正当命令行用：子进程 + PYTHONPATH 指向假 COM，验证端到端契约。"""

    def setUp(self) -> None:
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = os.pathsep.join([FAKE_COM, VENDOR, SCRIPTS])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, argv):
        return subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                              env=self.env, timeout=120)

    def test_layers_subprocess(self) -> None:
        result = self._run([os.path.join(SCRIPTS, "cad_direct.py"), "layers"])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])

    @unittest.skipIf(REAL_PYWIN32, "本机真有 pywin32，跳过缺依赖的报错路径")
    def test_missing_pywin32_reports_clean_json(self) -> None:
        """没装 pywin32 时应该是 JSON 报错 + 退出码 1，而不是抛栈。"""
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, os.path.join(SCRIPTS, "cad_direct.py"), "layers"],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("pywin32", payload["error"])

    def test_wall_plan_output_feeds_draw(self) -> None:
        """skill 的几何脚本 → CLI 直接落图（不用手工拷文件）"""
        plan_path = os.path.join(self.tmp.name, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump({"walls": [{"from": [0, 0], "to": [12000, 0], "thickness": 240,
                                  "openings": [{"kind": "door", "width": 1000, "center": 3000},
                                               {"kind": "window", "width": 2400, "center": 7000}]}]},
                      handle)

        walls = self._run([os.path.join(SCRIPTS, "wall_plan.py"), plan_path])
        self.assertEqual(walls.returncode, 0, walls.stderr)
        calls_path = os.path.join(self.tmp.name, "calls.json")
        with open(calls_path, "w", encoding="utf-8") as handle:
            handle.write(walls.stdout)

        drawn = self._run([os.path.join(SCRIPTS, "cad_direct.py"), "draw", "--calls", calls_path])
        self.assertEqual(drawn.returncode, 0, drawn.stderr)
        payload = json.loads(drawn.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["drawn"], 12)  # 3 段墙 + 4 封边 + 门扇/弧 + 3 窗线
        self.assertEqual(payload["stats"]["entity_count"], 12)

    def test_plan_to_scr_feeds_script_subprocess(self) -> None:
        plan_path = os.path.join(self.tmp.name, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump({"walls": [{"from": [0, 0], "to": [5000, 0], "thickness": 240}]}, handle)
        calls = self._run([os.path.join(SCRIPTS, "wall_plan.py"), plan_path])
        calls_path = os.path.join(self.tmp.name, "calls.json")
        with open(calls_path, "w", encoding="utf-8") as handle:
            handle.write(calls.stdout)
        scr = self._run([os.path.join(SCRIPTS, "plan_to_scr.py"), calls_path, "--out",
                         os.path.join(self.tmp.name, "plan.scr")])
        self.assertEqual(scr.returncode, 0, scr.stderr)

        result = self._run([os.path.join(SCRIPTS, "cad_direct.py"), "script", "--file",
                            os.path.join(self.tmp.name, "plan.scr"), "--out",
                            os.path.join(self.tmp.name, "run.scr")])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["completed"])
        self.assertEqual(payload["entities_created"], 1)  # 一段墙 = 一条闭合多段线


if __name__ == "__main__":
    unittest.main(verbosity=2)
