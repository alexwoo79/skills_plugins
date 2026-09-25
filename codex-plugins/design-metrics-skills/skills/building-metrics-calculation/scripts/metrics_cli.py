#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
building_metrics 命令行入口

把 DesignMetrics（规则计算引擎）封装成可直接调用的命令：
  check    数据契约校验（跑计算之前先查输入）
  calc     单项目指标计算 + 导出
  compare  两个项目的指标对比
  cities   列出支持的城市与规则要点

必须用本仓库的 .venv 运行（依赖 polars）：
    .venv/bin/python skills/building-metrics-calculation/scripts/metrics_cli.py calc \
        --project data/sh --city sh

数值一律来自 DesignMetrics，不在命令行里做任何二次计算。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# skill 自身所在目录（脚本的上一级）
SKILL_DIR = Path(__file__).resolve().parents[1]
# 插件根目录（skill 装在 <plugin>/skills/<skill-name>/ 时）
PLUGIN_ROOT = SKILL_DIR.parents[1] if len(SKILL_DIR.parents) > 1 else SKILL_DIR
# 随包分发的引擎快照目录（独立安装时使用）
BUNDLED_ENGINE = PLUGIN_ROOT / "engine"
# 与 skill 同目录的可选配置文件，用于记录仓库绝对路径
CONFIG_FILE = SKILL_DIR / "skill_config.json"
# 用环境变量指定仓库位置
ENV_VAR = "BUILDING_METRICS_REPO"
# 判定「这是 building_metrics 仓库」的标记文件
REPO_MARKER = "design_metrics.py"

_repo_cache = None

CITY_ALIAS = {
    "sh": "sh", "上海": "sh",
    "cs": "cs", "长沙": "cs",
    "hf": "hf", "合肥": "hf",
    "gy": "gy", "贵阳": "gy",
}

CITY_LABEL = {"sh": "上海", "cs": "长沙", "hf": "合肥", "gy": "贵阳"}


def _looks_like_repo(path: Path) -> bool:
    return path.is_dir() and (path / REPO_MARKER).is_file()


def repo_root(explicit=None) -> Path:
    """
    定位计算引擎所在目录。按下列顺序查找，命中即用：
      1. 命令行 --repo
      2. 环境变量 BUILDING_METRICS_REPO
      3. 从 skill 目录逐级向上找 design_metrics.py（skill 装在仓库内时的路径）
      4. skill 同目录的 skill_config.json 里的 "repo"
      5. 随包分发的内置引擎快照 <plugin>/engine（独立安装时走到这一步）
    """
    global _repo_cache
    if _repo_cache is not None:
        return _repo_cache

    candidates = []
    if explicit:
        candidates.append(("--repo", Path(explicit).expanduser()))
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        candidates.append((ENV_VAR, Path(env_value).expanduser()))
    for parent in [SKILL_DIR] + list(SKILL_DIR.parents):
        candidates.append(("从 skill 目录向上查找", parent))
    if CONFIG_FILE.is_file():
        try:
            configured = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("repo")
            if configured:
                candidates.append(("skill_config.json", Path(configured).expanduser()))
        except (OSError, ValueError):
            pass
    candidates.append(("内置引擎快照", BUNDLED_ENGINE))

    for _label, path in candidates:
        if _looks_like_repo(path):
            _repo_cache = path.resolve()
            return _repo_cache

    raise CliError(
        "找不到计算引擎（需要一个含 design_metrics.py 的目录）。四种指定方式任选其一：\n"
        "  1. 加参数 --repo /路径/to/building_metrics\n"
        f"  2. 设置环境变量 {ENV_VAR}=/路径/to/building_metrics\n"
        "  3. 把本 skill 放在仓库内（如 <仓库>/skills/）\n"
        f'  4. 在 {CONFIG_FILE} 写入 {{"repo": "/路径/to/building_metrics"}}\n'
        f"另外确认内置引擎快照存在：{BUNDLED_ENGINE}"
    )


def engine_source() -> str:
    """说明当前用的是外部仓库还是内置快照，便于发现两份引擎漂移。"""
    root = repo_root()
    try:
        inside_bundle = root.samefile(BUNDLED_ENGINE)
    except OSError:
        inside_bundle = False
    return "内置快照" if inside_bundle else "外部仓库"

# 各城市规则要点（源自 city_rules.py，改规则时同步更新）
CITY_RULES = {
    "sh": {
        "规则入口": "CityRules.calculate_sh_metrics",
        "计容口径": "S_计容面积 − 不计容配套面积",
        "奖励口径": "奖励楼栋计容面积 ÷ 奖励系数，再扣不计容配套",
        "特征指标": "保障房不计容半阳台、联排/洋房拆分、屋顶机房、电缆夹层、套内面积",
    },
    "cs": {
        "规则入口": "CityRules.calculate_cs_metrics",
        "计容口径": "计容住宅 + 计容配套 + 商业",
        "不计容住宅": "架空面积 + 屋顶机房面积 + 地下楼梯间面积",
        "特征指标": "骑楼面积、绿地率、配套/商业/住宅基底分列",
    },
    "hf": {
        "规则入口": "CityRules.calculate_hf_metrics",
        "计容口径": "住宅计容 =（地上住宅 − 保障房）×0.97 − 架空×0.03 + 保障房",
        "奖励口径": "住宅装配式奖励 =（地上住宅 − 保障房 + 架空）×0.03（系数硬编码在规则内）",
        "特征指标": "装配式奖励面积、保障房面积",
    },
    "gy": {
        "规则入口": "CityRules.calculate_gy_metrics",
        "计容口径": "S_计容面积（不再扣配套）",
        "特征指标": "商办面积、架空层面积、地下停车场 + 人防合计地库",
    },
}


class CliError(Exception):
    pass


def load_engine():
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from design_metrics import DesignMetrics
    except ModuleNotFoundError as exc:
        raise CliError(
            f"导入 design_metrics 失败：{exc}\n请用仓库的 .venv 运行：\n"
            f"  {root}/.venv/bin/python {Path(__file__)}\n"
            "若 .venv 不存在，先在仓库根目录执行 uv sync"
        )
    return DesignMetrics


def resolve_project(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = (repo_root() / p).resolve()
    if not p.is_dir():
        raise CliError(f"项目目录不存在：{p}")
    return p


def normalize_city(city: str) -> str:
    key = CITY_ALIAS.get(city)
    if key is None:
        raise CliError(f"不支持的城市：{city}（支持 sh/上海、cs/长沙、hf/合肥、gy/贵阳）")
    return key


def city_from_path(path: Path):
    """从目录名推断城市：data/sh -> sh、data/sh_change -> sh；推断不出返回 None"""
    name = path.name
    if name in CITY_ALIAS:
        return CITY_ALIAS[name]
    return CITY_ALIAS.get(name.split("_")[0])


def parse_list(value):
    if value is None:
        return None
    items = [v.strip() for v in str(value).replace("，", ",").split(",") if v.strip()]
    return items or None


def outdir_path(value) -> Path:
    d = Path(value)
    if not d.is_absolute():
        d = (repo_root() / d).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_validator():
    for script in (repo_root() / "scripts" / "validate_project_data.py",
                   BUNDLED_ENGINE / "validate_project_data.py"):
        if script.is_file():
            break
    else:
        return None
    spec = importlib.util.spec_from_file_location("validate_project_data", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_engine(project: Path, city: str, ratio: float, factor: float,
               bonus_list=None, facility_list=None):
    """与 app.py 的 build_project 保持同一调用顺序"""
    DesignMetrics = load_engine()
    p = DesignMetrics(str(project), city)
    p.update_ratio(ratio)
    p.update_factor(factor)
    p.load_data()
    if bonus_list is not None:
        p.update_bonus_list(bonus_list)
    if facility_list is not None:
        p.update_facility_list(facility_list)
    if bonus_list is not None or facility_list is not None:
        p.update_data()
    p.calculate_metrics()
    p.metrics_output()
    return p


def metrics_dict(project) -> dict:
    return {str(k): float(v) for k, v in zip(project.output["指标名称"], project.output["数值"])}


def write_workbook(path: Path, sheets: dict) -> Path:
    """把多个 DataFrame 写成同一个 xlsx 的多个 sheet"""
    import xlsxwriter

    wb = xlsxwriter.Workbook(str(path))
    try:
        for name, df in sheets.items():
            if df is None:
                continue
            df.write_excel(workbook=wb, worksheet=name)
    finally:
        wb.close()
    return path


def validate(project: Path, city: str):
    mod = load_validator()
    if mod is None:
        return [], [], ["未找到数据契约校验脚本，本次跳过校验"]
    res = mod.validate_project(project, city)
    return res["高危"], res["警告"], res["提示"]


def cmd_cities(args):
    out = {}
    out["_引擎"] = {"位置": str(repo_root()), "类型": engine_source()}
    for key, label in CITY_LABEL.items():
        entry = dict(CITY_RULES[key])
        data_dir = repo_root() / "data" / key
        entry["样例数据"] = str(data_dir) if data_dir.is_dir() else "未提供"
        out[label] = entry
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_check(args):
    mod = load_validator()
    if mod is None:
        print(json.dumps({"ok": False, "错误": "找不到 scripts/validate_project_data.py"},
                         ensure_ascii=False, indent=2))
        return 2
    project = resolve_project(args.project)
    city = normalize_city(args.city) if args.city else normalize_city(project.name)
    res = mod.validate_project(project, city)
    res["引擎来源"] = {"位置": str(repo_root()), "类型": engine_source()}
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res["高危"] else 0


def cmd_calc(args):
    project = resolve_project(args.project)
    city = normalize_city(args.city)
    high, warn, tips = validate(project, city)
    bonus = parse_list(args.bonus)
    facility = parse_list(args.facility)
    try:
        p = run_engine(project, city, args.ratio, args.factor, bonus, facility)
    except CliError:
        raise
    except Exception as exc:
        # 引擎异常转为可操作提示：多数情况下是数据契约高危项导致的
        raise CliError(
            f"计算中断：{type(exc).__name__}: {exc}\n"
            + ("先处理以下高危项后重试：\n  - " + "\n  - ".join(high) if high else
               "数据契约校验未发现高危项，请检查引擎日志。常见原因是补充信息.xlsx 缺分母键"
               "（如 用地面积），或面积线/系数表为空")
        )
    out = {
        "项目": str(project),
        "城市": CITY_LABEL[city],
        "引擎来源": {"位置": str(repo_root()), "类型": engine_source()},
        "参数": {"容积率限值": args.ratio, "奖励系数": args.factor,
               "自定义奖励楼栋": bonus, "自定义配套楼栋": facility},
        "指标": metrics_dict(p),
        "楼栋数": len(p.buildings_list or []),
        "奖励楼栋": p.bonus_list,
        "配套楼栋": p.facility_list,
        "数据诊断": p.diagnostics.to_dicts() if p.diagnostics is not None else [],
        "数据校验": {"高危": high, "警告": warn, "提示": tips},
    }
    if args.outdir:
        try:
            d = outdir_path(args.outdir)
            out_path = d / f"{project.name}_{city}_指标表.xlsx"
            detail_path = d / f"{project.name}_{city}_楼栋面积表.xlsx"
            hp_path = d / f"{project.name}_{city}_户配汇总.xlsx"
            # 指标表与诊断表写在同一个工作簿的不同 sheet
            write_workbook(out_path, {"指标表": p.output, "数据诊断": p.diagnostics})
            p.data.write_excel(str(detail_path))
            p.hp_data.write_excel(str(hp_path))
            out["导出文件"] = {"指标表": str(out_path), "楼栋面积表": str(detail_path),
                           "户配汇总": str(hp_path)}
        except OSError as exc:
            raise CliError(
                f"导出失败：{exc}\n输出目录不可写或不存在：{args.outdir}\n"
                "换一个可写目录（如用户主目录下的目录），或用绝对路径重试")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_compare(args):
    a = resolve_project(args.a)
    b = resolve_project(args.b)
    # 城市优先按目录名分别推断；推断不出才用 --city。两侧必须一致
    city = city_from_path(a) or (normalize_city(args.city) if args.city else None)
    city_b = city_from_path(b) or (normalize_city(args.city) if args.city else None)
    if city is None or city_b is None:
        raise CliError(
            f"无法从目录名判断城市（A={a.name}、B={b.name}），请用 --city 指定；"
            "两侧城市不同时对比没有意义")
    if city != city_b:
        raise CliError(
            f"两个项目的城市不一致：{CITY_LABEL[city]}（{a.name}）vs "
            f"{CITY_LABEL[city_b]}（{b.name}）；四套城市规则口径不同，跨城市对比没有意义")
    try:
        pa = run_engine(a, city, args.ratio, args.factor)
        pb = run_engine(b, city, args.ratio, args.factor)
    except CliError:
        raise
    except Exception as exc:
        raise CliError(f"对比中断：{type(exc).__name__}: {exc}\n"
                       "先用 check 校验两个项目的数据契约")
    cmp = pa.compare(pb)
    rows = cmp.to_dicts()
    diff = [r for r in rows if abs(r["Difference"]) > 1e-9]
    out = {
        "城市": CITY_LABEL[city],
        "引擎来源": {"位置": str(repo_root()), "类型": engine_source()},
        "项目A": str(a),
        "项目B": str(b),
        "对比指标数": len(rows),
        "存在差异的指标数": len(diff),
        "差异明细": diff,
    }
    if args.outdir:
        try:
            d = outdir_path(args.outdir)
            path = d / f"对比_{a.name}_vs_{b.name}.xlsx"
            cmp.write_excel(str(path))
            out["导出文件"] = {"对比表": str(path)}
        except OSError as exc:
            raise CliError(
                f"导出失败：{exc}\n输出目录不可写或不存在：{args.outdir}\n"
                "换一个可写目录（如用户主目录下的目录），或用绝对路径重试")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="building_metrics 规则计算命令行")
    # 所有子命令共用：指定仓库位置（skill 不在仓库内时必需）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=None,
                        help="building_metrics 仓库根目录；缺省时按环境变量/向上查找/skill_config.json 定位")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("cities", parents=[common], help="列出支持的城市与规则要点")
    p.set_defaults(func=cmd_cities)

    p = sub.add_parser("check", parents=[common], help="数据契约校验")
    p.add_argument("--project", required=True)
    p.add_argument("--city", default=None)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("calc", parents=[common], help="单项目指标计算")
    p.add_argument("--project", required=True)
    p.add_argument("--city", required=True)
    p.add_argument("--ratio", type=float, default=1.2, help="容积率限值")
    p.add_argument("--factor", type=float, default=1.03, help="奖励系数（与界面默认一致，区间 1.0—1.2）")
    p.add_argument("--bonus", default=None, help="自定义奖励楼栋，逗号分隔")
    p.add_argument("--facility", default=None, help="自定义配套楼栋，逗号分隔")
    p.add_argument("--outdir", default=None)
    p.set_defaults(func=cmd_calc)

    p = sub.add_parser("compare", parents=[common], help="两项目指标对比")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--city", default=None, help="缺省按目录名判断")
    p.add_argument("--ratio", type=float, default=1.2)
    p.add_argument("--factor", type=float, default=1.03, help="奖励系数（与界面默认一致，区间 1.0—1.2）")
    p.add_argument("--outdir", default=None)
    p.set_defaults(func=cmd_compare)

    args = ap.parse_args(argv)
    try:
        if getattr(args, "repo", None):
            repo_root(args.repo)  # 提前校验，避免跑到一半才发现路径不对
        return args.func(args)
    except CliError as exc:
        print(json.dumps({"ok": False, "错误": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
