#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
building-metrics-calculation skill 自测

覆盖三件事：
  1. 仓库自带的两层测试（数据契约校验、四城市指标快照回归）
  2. skill CLI 的接口契约（命令、退出码、导出 sheet 结构）
  3. 异常路径（非法城市、跨城市对比、缺陷数据、缺输入文件）

必须用仓库 .venv 运行：
    .venv/bin/python skills/building-metrics-calculation/scripts/self_test.py

退出码：0 全部通过；1 有用例失败
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
CLI = SKILL_DIR / "scripts" / "metrics_cli.py"
PY = sys.executable

sys.path.insert(0, str(SKILL_DIR / "scripts"))
from metrics_cli import repo_root  # noqa: E402

REPO_ROOT = repo_root()

results = []


def run(args, expect_code=0):
    proc = subprocess.run([PY, str(CLI)] + args, capture_output=True, text=True, cwd=REPO_ROOT)
    return proc.returncode, proc.stdout, proc.stderr


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def sheet_names(path: Path):
    import fastexcel

    return fastexcel.read_excel(str(path)).sheet_names


def make_defect_fixture(root: Path):
    """造一套带缺陷的项目数据：非法图层名、未匹配系数key、楼栋属性缺楼号、缺分母键"""
    import polars as pl

    proj = root / "defect"
    (proj / "Buildings_csv").mkdir(parents=True)
    (proj / "Buildings_csv" / "1.csv").write_text(
        "图层,面积\n"
        "AB-LP-1F-主体,500\n"
        "AB-LP-2F-主体,500\n"
        "AB-LP-1F-阳台,60\n"
        "AB-LP-设备平台,20\n"
        "AB-LP-1F-未知key,15\n"
        "BADLAYER,5\n"
        "AB-LP-1F-,3\n",
        encoding="utf-8",
    )
    (proj / "Buildings_csv" / "2.csv").write_text(
        "图层,面积\nAB-YF-1F-主体,800\n", encoding="utf-8")

    pl.DataFrame({
        "楼号": ["1", "3"],  # 楼号3 无面积线；楼号2 在面积线里但这里没有 → 高危
        "排序": [1, 2], "单元数": [1, 1], "每单元户数": [4, 4], "层数": [18, 18],
        "类型": ["LP", "LP"], "是否精装": ["精装", "精装"],
        "户型105": [72, 72], "是否奖励楼栋": [1, 0],
    }).write_excel(proj / "楼栋属性.xlsx")

    pl.DataFrame({
        "系数key": ["主体", "阳台"],
        "计容": [1.0, 0.5], "物理": [1.0, 1.0], "销售": [1.0, 1.0], "赠送": [0.0, 0.5],
    }).write_excel(proj / "系数key.xlsx")

    pl.DataFrame({
        "指标名称": ["不计容配套面积"],  # 故意缺 用地面积
        "数值": [100.0],
    }).write_excel(proj / "补充信息.xlsx")
    return proj


def main() -> int:
    print("== building-metrics-calculation skill 自测 ==")

    # --- 1. 仓库自带测试 ---
    print("\n[1] 仓库自带测试")
    proc = subprocess.run([PY, str(REPO_ROOT / "scripts" / "validate_project_data.py")],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    check("数据契约校验（5 个项目无高危）", proc.returncode == 0,
          f"退出码 {proc.returncode}")

    proc = subprocess.run([PY, str(REPO_ROOT / "scripts" / "smoke_test.py")],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    check("四城市指标快照回归 4/4", proc.returncode == 0 and proc.stdout.count("[PASSED]") == 4,
          f"退出码 {proc.returncode}")

    # --- 2. CLI 接口契约 ---
    print("\n[2] CLI 接口契约")
    code, out, _ = run(["cities"])
    check("cities 列出四城市", code == 0 and all(
        c in out for c in ("上海", "长沙", "合肥", "贵阳")))

    code, out, _ = run(["check", "--project", "data/sh"])
    check("check 正常数据退出码 0", code == 0 and json.loads(out)["高危"] == [])

    code, out, _ = run(["calc", "--project", "data/sh", "--city", "sh"])
    metrics = json.loads(out)["指标"] if code == 0 else {}
    check("calc 输出关键指标", code == 0 and metrics.get("计容面积", 0) > 0,
          f"计容面积={metrics.get('计容面积')}")

    tmp = Path(tempfile.mkdtemp(prefix="bm_selftest_"))
    try:
        code, out, _ = run(["calc", "--project", "data/sh", "--city", "sh",
                            "--outdir", str(tmp)])
        book = tmp / "sh_sh_指标表.xlsx"
        names = sheet_names(book) if book.is_file() else []
        check("指标表含「指标表」与「数据诊断」两个 sheet",
              code == 0 and names == ["指标表", "数据诊断"], f"实际 {names}")

        code, out, _ = run(["compare", "--a", "data/sh", "--b", "data/sh_change"])
        data = json.loads(out) if code == 0 else {}
        check("同城市对比可运行", code == 0 and data.get("对比指标数", 0) > 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 3. 异常路径 ---
    print("\n[3] 异常路径")
    code, out, _ = run(["calc", "--project", "data/sh", "--city", "北京"])
    check("非法城市被拒绝", code == 2 and "不支持的城市" in out)

    code, out, _ = run(["compare", "--a", "data/sh", "--b", "data/cs", "--city", "sh"])
    check("跨城市对比被拒绝", code == 2 and "城市不一致" in out)

    code, out, _ = run(["calc", "--project", "data/nonexistent", "--city", "sh"])
    check("目录不存在被拒绝", code == 2 and "项目目录不存在" in out)

    fixture_root = Path(tempfile.mkdtemp(prefix="bm_fixture_"))
    try:
        proj = make_defect_fixture(fixture_root)
        code, out, err = run(["check", "--project", str(proj), "--city", "sh"])
        data = json.loads(out) if out.strip().startswith("{") else {}
        high = " ".join(data.get("高危", []))
        check("check 抓到四类缺陷并返回 1",
              code == 1 and "图层命名不合法" in high and "用地面积" in high
              and "楼栋属性.xlsx 中无记录" in high,
              f"退出码 {code}，高危 {len(data.get('高危', []))} 条")

        code, out, err = run(["calc", "--project", str(proj), "--city", "sh"])
        check("缺分母键时给出可操作错误而非 traceback",
              code == 2 and "Traceback" not in (out + err) and "计算中断" in out
              and "用地面积" in out)
    finally:
        shutil.rmtree(fixture_root, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n== 汇总：{len(results) - len(failed)}/{len(results)} 通过 ==")
    for name, _, detail in failed:
        print(f"  失败：{name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
