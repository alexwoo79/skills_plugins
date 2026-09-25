#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可研阶段用地测算与楼型组合寻优引擎（keyan-calculation skill 主脚本）

口径来源：本工程 keyan_calculation.py（可研阶段方案测试工具 v4.0）。
本脚本复刻其指标链，并补齐三件事：
  1) 输入条件完整度体检与补充建议；
  2) 楼型组合方案的自动枚举与比选（替代人工反复试排）；
  3) 数据报表（xlsx / csv）与测算报告（markdown）落盘。

依赖：Python 标准库 + openpyxl（仅 Excel 读写需要）。

用法：
    python3 keyan.py template --out 输入模板.xlsx
    python3 keyan.py check    --input 项目.json
    python3 keyan.py calc     --input 项目.json  [--outdir 目录]
    python3 keyan.py report   --input 项目.json  --outdir 目录
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    HAS_OPENPYXL = True
except Exception:  # pragma: no cover
    HAS_OPENPYXL = False


KOUJING_VERSION = "keyan_calculation.py v4.0（可研阶段方案测试工具）"

LOUXING_HOUSEHOLDS = {"T8": 8, "T6": 6, "T5": 5, "T4": 4, "T3": 3, "T2": 2, "T1": 1}

FOOTPRINT_EXTRA_PER_BUILDING = 20.0

UNIT_LEVEL = 1e-6

# 占位值：用户填了但并未真正提供数据，不能算作已提供条件
PLACEHOLDERS = {"待确认", "待提供", "待补充", "待定", "待核实", "未知", "不详",
                "暂无", "n/a", "na", "none", "null", "-", "--", "—", "?", "？"}


def fmt(x, nd=2):
    if x is None:
        return ""
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, (int, float)):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return ""
        return f"{x:,.{nd}f}"
    return str(x)


def num(v, default=None):
    if v is None:
        return default
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def is_blank(v):
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def is_placeholder(v):
    if not isinstance(v, str):
        return False
    return v.strip().lower() in PLACEHOLDERS


def resolve(raw, path):
    if path in raw:
        return True, raw[path]
    cur = raw
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def round2(x):
    return None if x is None else round(x + 0.0, 2)


def clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# 字段: (分组, 逻辑名, 级别, 键候选, 缺失影响, 建议来源)
FIELD_SPECS = [
    ("基本信息", "项目名称", "可选", ["项目名称", "项目名", "project_name"],
     "报表与报告标题使用默认名", "—"),
    ("用地条件", "用地面积", "必填", ["用地面积", "site_area"],
     "无法计算总计容面积、建筑密度、楼栋数上限", "挂牌文件 / 规划设计条件通知书"),
    ("用地条件", "容积率", "必填", ["容积率", "far"],
     "无法计算总计容面积，全链条测算失去基准", "规划设计条件通知书（区间时取上限验算）"),
    ("用地条件", "限高", "必填", ["限高", "height_limit"],
     "无法计算极限层数，楼栋层数与组合不可解", "规划设计条件 / 航空与地方限高规定"),
    ("用地条件", "建筑密度", "必填", ["建筑密度", "site_coverage"],
     "无法判定基底面积是否超限，密度合规核查缺失", "规划设计条件通知书（输入小数，如 0.28）"),
    ("用地条件", "层高", "必填", ["层高", "floor_height"],
     "无法计算极限层数", "方案专业（住宅标准层高，通常 2.9—3.15m）"),
    ("用地条件", "商业面积", "重要", ["商业面积", "commercial_area"],
     "按 0 计会使住宅计容面积偏大，商品房面积虚高", "方案专业 / 产品定位"),
    ("用地条件", "公共配套面积", "重要", ["公共配套面积", "public_facility_area"],
     "按 0 计会使住宅计容面积偏大", "方案专业 / 配套专项"),
    ("用地条件", "物业用房面积", "重要", ["物业用房面积", "property_area"],
     "按 0 计会使住宅计容面积偏大", "方案专业"),
    ("用地条件", "配电房和开闭站面积", "重要", ["配电房和开闭站面积", "配电房面积", "power_area"],
     "按 0 计会使住宅计容面积偏大", "机电专业"),
    ("用地条件", "非住宅基底面积", "重要", ["非住宅基底面积", "non_res_footprint"],
     "按 0 计会低估建筑密度，可能掩盖密度超限", "方案专业（商业、配套、配电房基底）"),
    ("用地条件", "室内外高差", "重要", ["室内外高差", "high_diff"],
     "按 0 计会使极限层数偏大", "建筑专业（通常 0.15m）"),
    ("用地条件", "女儿墙", "重要", ["女儿墙", "女儿墙高度", "parapet"],
     "按 0 计会使极限层数偏大", "建筑专业（通常 0.55—1.2m）"),
    ("架空层", "架空层启用", "视情", ["架空层启用", "是否有架空层", "架空层.启用"],
     "默认按无架空层测算；如实际有架空层，极限层数与奖励面积会失真", "方案专业"),
    ("架空层", "架空层高度", "视情", ["架空层高度", "架空层.高度"],
     "有架空层时该项缺失将无法扣除层高占用", "方案专业（通常 3.3—4.5m）"),
    ("架空层", "架空率", "视情", ["架空率", "架空层率", "架空层.架空率"],
     "架空层面积按 0 计，架空层奖励面积随之漏算", "方案专业（通常 0.3）"),
    ("保障房", "保障房启用", "视情", ["保障房启用", "是否有保障房", "保障房.启用"],
     "默认按无保障房测算，住宅计容面积将全部计入商品房", "规划设计条件 / 片区政策"),
    ("保障房", "保障房模式", "视情", ["保障房模式", "保障房.模式"],
     "无法判断按面积比例还是按输入面积测算", "规划设计条件（面积比例 / 输入面积）"),
    ("保障房", "保障房面积比例", "视情", ["保障房面积比例", "保障房.面积比例"],
     "按比例模式时该项缺失将使保障房计容面积为 0", "规划设计条件"),
    ("保障房", "保障房输入面积", "视情", ["保障房输入面积", "保障房.输入面积"],
     "按输入面积模式时该项缺失将使保障房计容面积为 0", "规划设计条件 / 配建协议"),
    ("保障房", "保障房户均面积", "视情", ["保障房户均面积", "保障房.户均面积"],
     "保障房户数与基底面积无法计算", "产品标准 / 配建协议（通常 50—70m²）"),
    ("保障房", "保障房标准层户数", "视情", ["保障房标准层户数", "保障房.标准层户数"],
     "保障房基底面积无法计算，密度核查失真", "方案专业"),
    ("面积奖励", "商品房奖励系数", "视情", ["商品房奖励系数", "奖励.商品房"],
     "按无奖励测算，商品房面积偏小", "地方规划细则（阳台/飘窗/架空层奖励口径）"),
    ("面积奖励", "保障房奖励系数", "视情", ["保障房奖励系数", "奖励.保障房"],
     "按无奖励测算", "地方规划细则"),
    ("面积奖励", "架空层奖励系数", "视情", ["架空层奖励系数", "奖励.架空层"],
     "按无奖励测算", "地方规划细则"),
    ("产品配比", "户型配比", "必填", ["户型配比", "配比明细", "product_mix"],
     "无户型配比无法计算商品房户均面积、户数与配比对账", "产研 / 客研（套数比或面积比）"),
    ("产品配比", "楼型候选", "必填", ["楼型候选", "building_types"],
     "无可选楼型无法生成组合方案建议", "方案专业（含楼型、户型组合、层数）"),
    ("外部条件", "规划细则版本", "外部", ["规划细则版本", "细则版本"],
     "合规判定无版本锚点，新旧口径易混用", "标准化岗（锁定版本号与日期）"),
    ("外部条件", "日照要求", "外部", ["日照要求"],
     "无法判断楼型排布是否满足日照，方案可比性下降", "方案专业 / 日照分析"),
    ("外部条件", "退线要求", "外部", ["退线要求"],
     "无法校核基底面积上限的可实现性", "规划设计条件通知书"),
    ("外部条件", "车位配比", "外部", ["车位配比"],
     "地下建筑面积与成本无法纳入测算", "规划设计条件 / 交通专项"),
    ("外部条件", "得房率上限", "外部", ["得房率上限"],
     "无法对比测算得房率与政策/标准要求", "标准化岗 / 地方细则"),
    ("外部条件", "赠送面积口径", "外部", ["赠送面积口径"],
     "奖励面积测算缺少口径依据，报批对账风险上升", "地方规划细则"),
    ("外部条件", "地下室指标", "外部", ["地下室指标"],
     "地下部分面积与成本缺位", "方案专业 / 机电"),
    ("外部条件", "装配式要求", "外部", ["装配式要求"],
     "装配式奖励面积与增量成本无法计入", "集团标准 / 地方政策"),
    ("外部条件", "成本售价假设", "外部", ["成本售价假设"],
     "无法出货值与收益测算，只能出物理指标", "成本专业（售价、建安成本、车位价）"),
]

LEVEL_WEIGHT = {"必填": 3.0, "重要": 2.0, "视情": 1.0, "外部": 1.0, "可选": 0.5}

SHEET_HAOHU = "户型配比"
SHEET_LOUXING = "楼型候选"
SHEET_WAI_BU = "外部条件"


class InputError(Exception):
    pass


def spec_supplied(raw, spec):
    _grp, _name, _lvl, keys, _impact, _src = spec
    for k in keys:
        ok, v = resolve(raw, k)
        if ok and not is_blank(v) and not is_placeholder(v):
            return True
    return False


def read_input_xlsx(path: Path) -> dict:
    if not HAS_OPENPYXL:
        raise InputError("读取 xlsx 需要 openpyxl，请改用 json 输入")
    wb = load_workbook(path, data_only=True)
    raw: dict = {}
    for sheet in ("用地条件", SHEET_WAI_BU):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row:
                continue
            k = row[0]
            v = row[1] if len(row) > 1 else None
            if is_blank(k):
                continue
            raw[str(k).strip()] = v
    if "寻优参数" in wb.sheetnames:
        ws = wb["寻优参数"]
        opts = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or is_blank(row[0]):
                continue
            opts[str(row[0]).strip()] = row[1] if len(row) > 1 else None
        if opts:
            raw["寻优参数"] = opts
    if SHEET_HAOHU in wb.sheetnames:
        ws = wb[SHEET_HAOHU]
        header = [str(c).strip() if c is not None else ""
                  for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row is None or all(is_blank(c) for c in row):
                continue
            rows.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
        raw["户型配比"] = rows
    if SHEET_LOUXING in wb.sheetnames:
        ws = wb[SHEET_LOUXING]
        header = [str(c).strip() if c is not None else ""
                  for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row is None or all(is_blank(c) for c in row):
                continue
            item = {header[i]: row[i] for i in range(min(len(header), len(row)))}
            if is_blank(item.get("楼型")):
                continue
            rows.append(item)
        raw["楼型候选"] = rows
    return raw


def read_input(path: Path) -> dict:
    if not path.exists():
        raise InputError(f"输入文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    if suffix in (".xlsx", ".xlsm"):
        return read_input_xlsx(path)
    raise InputError(f"不支持的输入格式：{suffix}（支持 .json / .xlsx）")


def _parse_mix_text(text) -> list:
    out = []
    if is_blank(text):
        return out
    normalized = str(text)
    for ch in ("，", ",", "；", ";"):
        normalized = normalized.replace(ch, "+")
    for token in normalized.split("+"):
        token = token.strip()
        if not token:
            continue
        low = token.lower()
        if "x" in low:
            left, right = low.split("x", 1)
        elif "*" in token:
            left, right = token.split("*", 1)
        elif "×" in token:
            left, right = token.split("×", 1)
        else:
            raise InputError(f"户型组合格式无法解析：{token}（示例：100x2+120x2）")
        a = num(left)
        c = num(right)
        if a is None or c is None:
            raise InputError(f"户型组合格式无法解析：{token}（示例：100x2+120x2）")
        out.append({"面积段": a, "数量": int(round(c))})
    return out


def normalize(raw: dict) -> tuple:
    """把原始输入规范化；记录自动补值（假设）与硬性缺项。"""
    assumptions = []

    def take(keys, default=None, note=None):
        for k in keys:
            ok, v = resolve(raw, k)
            if ok and not is_blank(v):
                return v
        if note is not None:
            assumptions.append(note)
        return default

    def take_f(keys, default=None, note=None, nd=None):
        f = num(take(keys, default, note), default)
        return round(f, nd) if (f is not None and nd is not None) else f

    missing_required = []
    for spec in FIELD_SPECS:
        grp, name, lvl, keys, impact, src = spec
        if lvl == "必填" and not spec_supplied(raw, spec):
            missing_required.append((grp, name, impact, src))
    if missing_required:
        lines = ["以下必填条件缺失，无法进入测算（不得用假定值替代）："]
        for grp, name, impact, src in missing_required:
            lines.append(f"  - [{grp}] {name}：{impact}；建议来源：{src}")
        raise InputError("\n".join(lines))

    cfg = {}
    cfg["项目名称"] = str(take(["项目名称", "项目名", "project_name"], "未命名项目"))
    cfg["用地面积"] = take_f(["用地面积", "site_area"], nd=2)
    cfg["容积率"] = take_f(["容积率", "far"], nd=4)
    cfg["限高"] = take_f(["限高", "height_limit"], nd=2)
    cfg["建筑密度"] = take_f(["建筑密度", "site_coverage"], nd=4)
    cfg["层高"] = take_f(["层高", "floor_height"], nd=3)

    zero_fields = [
        (["商业面积", "commercial_area"],
         "商业面积未提供，按 0 计；住宅计容面积与商品房面积将偏大，须复核"),
        (["非住宅基底面积", "non_res_footprint"],
         "非住宅基底面积未提供，按 0 计；建筑密度被低估，可能掩盖密度超限"),
        (["公共配套面积", "public_facility_area"], "公共配套面积未提供，按 0 计；住宅计容面积将偏大"),
        (["物业用房面积", "property_area"], "物业用房面积未提供，按 0 计；住宅计容面积将偏大"),
        (["配电房和开闭站面积", "配电房面积", "power_area"],
         "配电房和开闭站面积未提供，按 0 计；住宅计容面积将偏大"),
        (["室内外高差", "high_diff"], "室内外高差未提供，按 0 计；极限层数偏大"),
        (["女儿墙", "女儿墙高度", "parapet"], "女儿墙高度未提供，按 0 计；极限层数偏大"),
    ]
    for keys, note in zero_fields:
        cfg[keys[0]] = take_f(keys, 0.0, note, nd=2)

    jk_flag = take(["架空层启用", "是否有架空层", "架空层.启用"], None)
    jk_on = str(jk_flag).strip() in ("有", "是", "True", "true", "1", "1.0") if jk_flag is not None else False
    cfg["架空层"] = {
        "启用": jk_on,
        "高度": take_f(["架空层高度", "架空层.高度"], 0.0,
                      "架空层高度未提供，按 0 计" if jk_on else None, nd=2),
        "架空率": take_f(["架空率", "架空层率", "架空层.架空率"], 0.0,
                       "架空率未提供，按 0 计；架空层面积与奖励面积将漏算" if jk_on else None, nd=4),
    }
    if not jk_on:
        cfg["架空层"]["高度"] = 0.0
        cfg["架空层"]["架空率"] = 0.0

    bz_flag = take(["保障房启用", "是否有保障房", "保障房.启用"], None)
    bz_on = str(bz_flag).strip() in ("有", "是", "True", "true", "1", "1.0") if bz_flag is not None else False
    bz_mode_raw = str(take(["保障房模式", "保障房.模式"], "面积比例"))
    bz_mode = "输入面积" if ("面积" in bz_mode_raw and "比例" not in bz_mode_raw) else "面积比例"
    cfg["保障房"] = {
        "启用": bz_on,
        "模式": bz_mode,
        "面积比例": take_f(["保障房面积比例", "保障房.面积比例"], 0.0, None, nd=4),
        "输入面积": take_f(["保障房输入面积", "保障房.输入面积"], 0.0, None, nd=2),
        "户均面积": take_f(["保障房户均面积", "保障房.户均面积"], 0.0,
                         "保障房户均面积未提供，按 0 计；保障房户数与基底面积不可用" if bz_on else None, nd=2),
        "标准层户数": take_f(["保障房标准层户数", "保障房.标准层户数"], 0.0,
                          "保障房标准层户数未提供，按 0 计；保障房基底面积不可用" if bz_on else None, nd=0),
    }
    if not bz_on:
        for k in ("面积比例", "输入面积", "户均面积", "标准层户数"):
            cfg["保障房"][k] = 0.0
    if bz_on and bz_mode == "面积比例" and cfg["保障房"]["面积比例"] <= 0:
        raise InputError("保障房已启用且模式为「面积比例」，但 保障房面积比例 未提供或为 0")
    if bz_on and bz_mode == "输入面积" and cfg["保障房"]["输入面积"] <= 0:
        raise InputError("保障房已启用且模式为「输入面积」，但 保障房输入面积 未提供或为 0")

    cfg["面积奖励"] = {
        "商品房奖励系数": take_f(["商品房奖励系数", "奖励.商品房"], 0.0, None, nd=4),
        "保障房奖励系数": take_f(["保障房奖励系数", "奖励.保障房"], 0.0, None, nd=4),
        "架空层奖励系数": take_f(["架空层奖励系数", "奖励.架空层"], 0.0, None, nd=4),
    }

    mix_raw = take(["户型配比", "配比明细", "product_mix"], None)
    mix_kj = str(take(["配比口径", "mix_basis"], "套数比"))
    mix_kj = "面积比" if "面积" in mix_kj else "套数比"
    if isinstance(mix_raw, dict):
        mix_kj = "面积比" if "面积" in str(mix_raw.get("口径", mix_kj)) else mix_kj
        mix_raw = mix_raw.get("明细", [])
    details = []
    for row in (mix_raw or []):
        if not isinstance(row, dict):
            continue
        if row.get("口径"):
            mix_kj = "面积比" if "面积" in str(row["口径"]) else "套数比"
        area = num(row.get("面积段", row.get("户型面积段", row.get("面积"))))
        v = num(row.get(mix_kj if mix_kj in row else ("套数比" if "套数比" in row else "面积比")))
        if area is None or v is None or v <= 0:
            continue
        details.append({"面积段": float(area), "值": float(v)})
    if not details:
        raise InputError("户型配比为空或格式不正确（需要 面积段 + 套数比/面积比）")
    details.sort(key=lambda d: d["面积段"])
    cfg["户型配比"] = {"口径": mix_kj, "明细": details}

    lx_raw = take(["楼型候选", "building_types"], [])
    lx_list = []
    for row in (lx_raw or []):
        if not isinstance(row, dict):
            continue
        code = str(row.get("楼型", row.get("编号", ""))).strip().upper()
        if not code:
            continue
        if code not in LOUXING_HOUSEHOLDS:
            raise InputError(f"楼型 {code} 不在支持范围 {list(LOUXING_HOUSEHOLDS)}")
        combo = row.get("户型组合")
        if isinstance(combo, str):
            combo = _parse_mix_text(combo)
        elif isinstance(combo, list):
            combo = [{"面积段": num(c.get("面积段")), "数量": int(num(c.get("数量"), 0) or 0)}
                     for c in combo if isinstance(c, dict)]
        else:
            combo = []
            for i in range(1, 5):
                a = num(row.get(f"面积段{i}"))
                c = num(row.get(f"数量{i}"))
                if a is not None and c:
                    combo.append({"面积段": a, "数量": int(c)})
        combo = [c for c in combo if c["面积段"] and c["数量"] > 0]
        if not combo:
            raise InputError(f"楼型 {code} 缺少户型组合（示例：100x2+120x2）")
        lx_list.append({
            "楼型": code,
            "户型组合": combo,
            "层数": num(row.get("层数"), None),
            "楼栋数": num(row.get("楼栋数"), None),
            "楼栋数下限": num(row.get("楼栋数下限"), None),
            "楼栋数上限": num(row.get("楼栋数上限"), None),
        })
    if not lx_list:
        raise InputError("楼型候选为空（至少提供 1 个楼型及其户型组合）")
    cfg["楼型候选"] = lx_list

    opt_raw = take(["寻优参数", "options"], {}) or {}
    if not isinstance(opt_raw, dict):
        opt_raw = {}
    w_raw = opt_raw.get("权重") or {}
    cfg["寻优参数"] = {
        "面积下浮下限": num(opt_raw.get("面积下浮下限"), 0.03),
        "配比偏差上限": num(opt_raw.get("配比偏差上限"), 0.05),
        "密度利用下限": num(opt_raw.get("密度利用下限"), 0.80),
        "方案数上限": int(num(opt_raw.get("方案数上限"), 5) or 5),
        "权重": {
            "面积利用率": num(w_raw.get("面积利用率"), 0.40),
            "配比符合度": num(w_raw.get("配比符合度"), 0.25),
            "户均达成度": num(w_raw.get("户均达成度"), 0.20),
            "集约度": num(w_raw.get("集约度"), 0.15),
        },
    }
    cfg["外部条件"] = {k: v for k, v in raw.items() if not isinstance(v, (list, dict))}
    return cfg, assumptions


def audit_input(raw: dict) -> dict:
    items = []
    total_w = 0.0
    got_w = 0.0
    for spec in FIELD_SPECS:
        grp, name, lvl, keys, impact, src = spec
        supplied = spec_supplied(raw, spec)
        w = LEVEL_WEIGHT.get(lvl, 1.0)
        total_w += w
        if supplied:
            got_w += w
        items.append({
            "分组": grp, "条件": name, "级别": lvl,
            "是否提供": "已提供" if supplied else "未提供",
            "缺失影响": "" if supplied else impact,
            "建议来源": "" if supplied else src,
        })
    score = round(got_w / total_w * 100, 1) if total_w else 0.0
    must_missing = [i for i in items if i["级别"] == "必填" and i["是否提供"] == "未提供"]
    imp_missing = [i for i in items if i["级别"] == "重要" and i["是否提供"] == "未提供"]
    ext_total = sum(1 for i in items if i["级别"] == "外部")
    ext_supplied = sum(1 for i in items if i["级别"] == "外部" and i["是否提供"] == "已提供")
    if must_missing:
        grade, desc = "L0 不可测算", "必填条件缺失，只能补条件，不能出数"
    elif imp_missing or ext_supplied <= 2:
        grade, desc = "L1 估算级", "可出总量指标与楼型组合方向，不能作为报批依据"
    elif ext_supplied < ext_total:
        grade, desc = "L2 方案级", "可用于方案阶段比选与内部对账，报批前需补外部口径"
    else:
        grade, desc = "L3 报批级", "外部口径齐备，可与报批指标表逐项对账"
    return {
        "完备度得分": score, "等级": grade, "等级说明": desc,
        "必填缺失数": len(must_missing), "重要缺失数": len(imp_missing),
        "外部条件覆盖": f"{ext_supplied}/{ext_total}",
        "明细": items,
        "待补充": [i for i in items if i["是否提供"] == "未提供" and i["级别"] in ("必填", "重要", "外部")],
    }


def consistency_checks(cfg: dict, base: dict, buildings: list) -> list:
    """自洽性校验：输入条件之间是否互相矛盾。"""
    out = []
    sa = cfg["用地面积"]
    density = cfg["建筑密度"]
    f_max = math.floor(base["极限层数"] + UNIT_LEVEL)

    def add(no, level, item, conclusion, action):
        out.append({"编号": no, "级别": level, "校验事项": item,
                    "结论": conclusion, "建议处置": action})

    if base["极限层数"] < 3:
        add("C1", "高", "极限层数",
            f"极限层数仅 {fmt(base['极限层数'])} 层，住宅楼型无法成立",
            "复核限高、层高、架空层高度、室内外高差、女儿墙取值")
    else:
        add("C1", "通过", "极限层数",
            f"极限层数 {fmt(base['极限层数'])} 层，可取整为 {f_max} 层", "—")

    theoretical = sa * density * f_max
    need = base["总计容面积"]
    if theoretical < need:
        add("C2", "高", "容积率—建筑密度—限高自洽",
            f"理论可建面积 {fmt(theoretical)} m² < 总计容面积 {fmt(need)} m²，缺口 {fmt(need - theoretical)} m²",
            "三者矛盾：提高限高、提高建筑密度或降低容积率；亦需确认是否含地下/半地下计容或架空层计容口径")
    else:
        add("C2", "通过", "容积率—建筑密度—限高自洽",
            f"理论可建面积 {fmt(theoretical)} m² ≥ 总计容面积 {fmt(need)} m²，条件自洽", "—")

    if base["住宅计容面积"] <= 0:
        add("C3", "高", "住宅计容面积",
            f"住宅计容面积 {fmt(base['住宅计容面积'])} m² ≤ 0",
            "商业、公配、物业、配电房面积之和已超过总计容面积，需重新分配")
    else:
        add("C3", "通过", "住宅计容面积", f"住宅计容面积 {fmt(base['住宅计容面积'])} m²", "—")

    sup = cfg["商业面积"] + cfg["公共配套面积"] + cfg["物业用房面积"] + cfg["配电房和开闭站面积"]
    if base["总计容面积"] > 0:
        ratio = sup / base["总计容面积"]
        if ratio > 0.15:
            add("C4", "中", "配套类面积占比",
                f"商业+公配+物业+配电房合计 {fmt(sup)} m²，占计容 {ratio * 100:.1f}%",
                "占比偏高，复核配套规模是否确有依据")
        else:
            add("C4", "通过", "配套类面积占比", f"合计 {fmt(sup)} m²，占计容 {ratio * 100:.1f}%", "—")

    if cfg["保障房"]["启用"]:
        if base["保障房计容面积"] >= base["住宅计容面积"]:
            add("C5", "高", "保障房计容面积", "保障房计容面积不小于住宅计容面积",
                "复核保障房面积比例或输入面积")
        else:
            add("C5", "通过", "保障房计容面积",
                f"保障房计容面积 {fmt(base['保障房计容面积'])} m²，商品房计容面积 {fmt(base['商品房计容面积'])} m²",
                "—")
        if cfg["保障房"]["标准层户数"] <= 0 or cfg["保障房"]["户均面积"] <= 0:
            add("C5b", "中", "保障房参数",
                "保障房标准层户数或户均面积为 0，保障房基底面积与户数不可用",
                "补充保障房标准层户数与户均面积")
    else:
        add("C5", "通过", "保障房", "输入条件为无保障房", "—")

    mix = cfg["户型配比"]["明细"]
    s = sum(d["值"] for d in mix)
    if abs(s - 1.0) > 0.02:
        add("C6", "中", "配比合计",
            f"{cfg['户型配比']['口径']}合计为 {s:.4f}，与 1.0 偏差较大",
            "确认配比是否漏项；系统按合计值归一化计算")
    else:
        add("C6", "通过", "配比合计", f"{cfg['户型配比']['口径']}合计 {s:.4f}", "—")

    mix_areas = {d["面积段"] for d in mix}
    stray = sorted({c["面积段"] for b in buildings for c in b["户型组合"]} - mix_areas)
    if stray:
        add("C7", "中", "楼型户型与配比一致性",
            f"楼型中出现的面积段 {stray} 不在户型配比表中",
            "确认是配比漏项还是楼型选错；未纳入配比的户型无法参与配比对账")
    else:
        add("C7", "通过", "楼型户型与配比一致性", "楼型所用面积段均在户型配比表内", "—")

    infeasible = [b["楼型"] for b in buildings if b["最大楼栋数"] < 1]
    if infeasible:
        add("C8", "中", "楼型可行性",
            f"楼型 {infeasible} 按现条件最多 0 栋，实际不可用",
            "该楼型标准层面积或层数偏大，需减少层数、调整户型组合或剔除")
    else:
        add("C8", "通过", "楼型可行性",
            "各楼型最大楼栋数：" + "、".join(f"{b['楼型']}={b['最大楼栋数']:.2f}" for b in buildings), "—")
    return out


def compute_base(cfg: dict) -> dict:
    sa = cfg["用地面积"]
    density = cfg["建筑密度"]
    jk = cfg["架空层"]
    bz = cfg["保障房"]
    bonus = cfg["面积奖励"]

    base = {}
    base["总计容面积"] = sa * cfg["容积率"]
    base["住宅计容面积"] = (base["总计容面积"] - cfg["公共配套面积"] - cfg["物业用房面积"]
                         - cfg["配电房和开闭站面积"] - cfg["商业面积"])
    base["极限层数"] = ((cfg["限高"] - jk["高度"] - cfg["室内外高差"] - cfg["女儿墙"]) / cfg["层高"]
                    if cfg["层高"] else 0.0)
    base["极限层数取整"] = float(math.floor(base["极限层数"] + UNIT_LEVEL)) if base["极限层数"] > 0 else 0.0
    base["住宅基底面积上限"] = sa * density - cfg["非住宅基底面积"]
    base["架空层面积"] = base["住宅基底面积上限"] * jk["架空率"] if jk["启用"] else 0.0

    if bz["启用"]:
        base["保障房计容面积"] = (base["总计容面积"] * bz["面积比例"]
                            if bz["模式"] == "面积比例" else bz["输入面积"])
    else:
        base["保障房计容面积"] = 0.0
    base["商品房计容面积"] = base["住宅计容面积"] - base["保障房计容面积"]

    base["商品房奖励面积"] = base["商品房计容面积"] * bonus["商品房奖励系数"]
    base["保障房奖励面积"] = base["保障房计容面积"] * bonus["保障房奖励系数"]
    base["架空层奖励面积"] = base["架空层面积"] * bonus["架空层奖励系数"]
    base["总奖励面积"] = base["商品房奖励面积"] + base["保障房奖励面积"] + base["架空层奖励面积"]

    base["商品房面积"] = base["商品房计容面积"] + base["商品房奖励面积"] + base["架空层奖励面积"]
    base["保障房面积"] = base["保障房计容面积"] + base["保障房奖励面积"]
    base["保障房基底面积"] = ((bz["标准层户数"] * bz["户均面积"] + FOOTPRINT_EXTRA_PER_BUILDING)
                        if bz["启用"] else 0.0)

    details = list(cfg["户型配比"]["明细"])
    if cfg["户型配比"]["口径"] == "套数比":
        s = sum(d["值"] for d in details) or 1.0
        for d in details:
            d["套数比"] = d["值"] / s
    else:
        s = sum(d["值"] for d in details) or 1.0
        raw_units = {d["面积段"]: (d["值"] / s) / d["面积段"] for d in details}
        tot_units = sum(raw_units.values()) or 1.0
        for d in details:
            d["套数比"] = raw_units[d["面积段"]] / tot_units
    base["配比明细"] = details
    base["商品房户均面积"] = sum(d["套数比"] * d["面积段"] for d in details)
    base["商品房平均户数"] = (base["商品房面积"] / base["商品房户均面积"]
                        if base["商品房户均面积"] else 0.0)

    base["保障房户数"] = (base["保障房面积"] / bz["户均面积"]
                     if bz["启用"] and bz["户均面积"] > 0 else 0.0)
    base["保障房折算层数"] = (base["保障房面积"] / (bz["标准层户数"] * bz["户均面积"])
                        if bz["启用"] and bz["标准层户数"] > 0 and bz["户均面积"] > 0 else 0.0)

    base["商品房屋顶机房面积"] = 0.0
    base["商品房阳台面积"] = 0.0
    base["商品房总建筑面积"] = (base["商品房计容面积"] + base["商品房奖励面积"]
                         + base["商品房屋顶机房面积"] + base["商品房阳台面积"])
    base["保障房总建筑面积"] = base["保障房计容面积"] + base["保障房奖励面积"]
    base["住宅总建筑面积"] = base["商品房总建筑面积"] + base["保障房总建筑面积"]
    return base


def compute_buildings(cfg: dict, base: dict) -> list:
    buildings = []
    f_max = int(base["极限层数取整"])
    for item in cfg["楼型候选"]:
        code = item["楼型"]
        n = LOUXING_HOUSEHOLDS[code]
        combo = item["户型组合"]
        cnt = sum(c["数量"] for c in combo)
        warn = None
        if cnt != n:
            warn = f"户型组合数量合计 {cnt}，与楼型 {code} 每层 {n} 户不一致"
        floor_area = sum(c["面积段"] * c["数量"] for c in combo)
        mean_house = floor_area / n if n else 0.0
        f = int(round(item["层数"])) if item["层数"] else f_max
        if f_max and f > f_max:
            warn = ((warn + "；") if warn else "") + f"层数 {f} 超过极限层数 {f_max}，已按 {f_max} 计"
            f = f_max
        if f < 1:
            f = 1
        nc_max = base["商品房面积"] / (floor_area * f) if (floor_area and f) else 0.0
        lo = int(math.floor(item["楼栋数下限"])) if item["楼栋数下限"] is not None else 0
        hi = (int(math.floor(item["楼栋数上限"])) if item["楼栋数上限"] is not None
              else int(math.floor(nc_max + UNIT_LEVEL)))
        if item["楼栋数"] is not None:
            lo = hi = int(round(item["楼栋数"]))
        lo = max(0, lo)
        hi = max(lo, min(hi, int(math.floor(max(nc_max, 0.0) + UNIT_LEVEL))))
        buildings.append({
            "楼型": code, "每层户数": n, "户型组合": combo,
            "户型组合文本": "+".join(f"{c['面积段']:g}x{c['数量']}" for c in combo),
            "标识": f"{code}-" + "+".join(f"{c['面积段']:g}x{c['数量']}" for c in combo),
            "层数": f, "标准层面积": floor_area, "户均面积": mean_house,
            "单栋户数": n * f, "最大楼栋数": nc_max,
            "楼栋数区间": [lo, hi], "告警": warn,
        })
    return buildings


def _tv_distance(calc: dict, target: dict) -> float:
    areas = set(calc) | set(target)
    return 0.5 * sum(abs(calc.get(a, 0.0) - target.get(a, 0.0)) for a in areas)


def solve(cfg: dict, base: dict, buildings: list) -> dict:
    opt = cfg["寻优参数"]
    target_area = base["商品房面积"]
    max_footprint = (cfg["用地面积"] * cfg["建筑密度"] - cfg["非住宅基底面积"]
                     - base["保障房基底面积"])
    mix_target = {d["面积段"]: d["套数比"] for d in base["配比明细"]}
    mix_areas = [d["面积段"] for d in base["配比明细"]]
    target_mean = base["商品房户均面积"]

    candidates = [b for b in buildings if b["楼栋数区间"][1] >= 1]
    if not candidates or target_area <= 0:
        return {"可行方案": [], "备选方案": [], "推荐方案": None, "搜索空间": 0,
                "可行解数量": 0, "楼型顺序": [],
                "提示": ["没有可用楼型或可建面积为 0，请先处理校验问题"]}

    span = 1
    for b in candidates:
        span *= (b["楼栋数区间"][1] - b["楼栋数区间"][0] + 1)
    stride = 1
    note = []
    if span > 200000:
        stride = max(1, int(round(span ** (1.0 / len(candidates)) / 12)))
        note.append(f"组合空间 {span} 过大，楼栋数按步长 {stride} 采样求解")

    results = []
    households = {a: 0.0 for a in mix_areas}
    n_units = len(candidates)

    def evaluate(acc):
        if acc["area"] <= 0:
            return
        area_ratio = acc["area"] / target_area
        if area_ratio > 1 + 1e-9:
            return
        if acc["footprint"] > max_footprint + 1e-6:
            return
        if area_ratio < 1 - opt["面积下浮下限"] - 1e-9:
            return
        total_fp = acc["footprint"] + cfg["非住宅基底面积"] + base["保障房基底面积"]
        cover_ratio = total_fp / cfg["用地面积"]
        if cover_ratio > cfg["建筑密度"] + 1e-9:
            return
        total_hh = sum(households.values())
        if total_hh <= 0:
            return
        calc_mix = {a: households[a] / total_hh for a in mix_areas}
        tv = _tv_distance(calc_mix, mix_target)
        calc_mean = sum(households[a] * a for a in mix_areas) / total_hh
        mean_gap = abs(calc_mean - target_mean) / target_mean if target_mean else 0.0
        density_use = cover_ratio / cfg["建筑密度"] if cfg["建筑密度"] else 0.0
        hi_sum = sum(b["楼栋数区间"][1] for b in candidates) or 1
        intensity = 1 - acc["buildings"] / hi_sum
        w = opt["权重"]
        score = 100.0 * (
            w["面积利用率"] * clip(area_ratio)
            + w["配比符合度"] * clip(1 - tv)
            + w["户均达成度"] * clip(1 - mean_gap)
            + w["集约度"] * clip(intensity)
        )
        results.append({
            "score": score, "楼栋数": list(acc["nc"]),
            "商品房面积汇总": acc["area"], "面积利用率": area_ratio,
            "总基底面积": total_fp, "总建筑密度": cover_ratio, "密度利用率": density_use,
            "商品房户数汇总": total_hh, "计算套数比": calc_mix, "配比偏差": tv,
            "计算户均面积": calc_mean, "户均偏差率": mean_gap,
            "楼栋数汇总": acc["buildings"],
        })

    def dfs(i, area, footprint, nbuild, nc, hh):
        if i == n_units:
            evaluate({"area": area, "footprint": footprint, "buildings": nbuild,
                      "nc": nc, "hh": hh})
            return
        b = candidates[i]
        lo, hi = b["楼栋数区间"]
        for nc_i in range(lo, hi + 1, stride):
            add_area = b["标准层面积"] * b["层数"] * nc_i
            add_fp = b["标准层面积"] * nc_i
            if area + add_area > target_area * (1 + 1e-9):
                break
            if footprint + add_fp > max_footprint + 1e-6:
                break
            for c in b["户型组合"]:
                hh[c["面积段"]] = hh.get(c["面积段"], 0.0) + c["数量"] * b["层数"] * nc_i
            dfs(i + 1, area + add_area, footprint + add_fp, nbuild + nc_i, nc + [nc_i], hh)
            for c in b["户型组合"]:
                hh[c["面积段"]] -= c["数量"] * b["层数"] * nc_i

    dfs(0, 0.0, 0.0, 0, [], households)

    if not results:
        note.append(
            f"在现约束下没有可行组合：面积下浮下限为 {opt['面积下浮下限']:.1%}，"
            "即组合面积须落在可建面积的该比例之上。建议检查楼型层数与楼栋数区间、"
            "或补充更适配的楼型候选（尤其是层数更接近极限层数的楼型）")

    strict = [r for r in results if r["配比偏差"] <= opt["配比偏差上限"] + 1e-9]
    used = strict or results
    if results and not strict:
        note.append(f"现条件下无配比偏差 ≤ {opt['配比偏差上限']:.1%} 的方案，已放宽配比约束，结果需人工判断")

    used.sort(key=lambda r: (-r["score"], r["楼栋数汇总"], -r["面积利用率"]))
    seen = set()
    picked = []
    for r in used:
        key = tuple(r["楼栋数"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)
        if len(picked) >= opt["方案数上限"]:
            break

    return {
        "可行方案": picked, "备选方案": picked[1:],
        "推荐方案": picked[0] if picked else None,
        "搜索空间": span, "可行解数量": len(results),
        "提示": note, "楼型顺序": [b["标识"] for b in candidates],
    }


HEADER_FILL = PatternFill("solid", fgColor="D9E1F2") if HAS_OPENPYXL else None
HEAD_FONT = Font(bold=True) if HAS_OPENPYXL else None
THIN = Border(*[Side(style="thin", color="B0B0B0")] * 4) if HAS_OPENPYXL else None


def _unit_hint(k):
    if k == "商品房户均面积":
        return "m²/户"
    if k in ("极限层数", "极限层数取整", "保障房折算层数"):
        return "层"
    if k in ("商品房平均户数", "保障房户数"):
        return "户"
    if "面积" in k or "墙" in k or "高差" in k:
        return "m²"
    return ""


def _write_sheet(wb, title, header, rows, widths=None, notes=None):
    ws = wb.create_sheet(title)
    r = 1
    for note in (notes or []):
        ws.cell(row=r, column=1, value=note).font = Font(italic=True, color="555555")
        r += 1
    if notes:
        r += 1
    head_row = r
    for j, h in enumerate(header, start=1):
        c = ws.cell(row=head_row, column=j, value=h)
        c.font = HEAD_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = THIN
    for i, row in enumerate(rows, start=head_row + 1):
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = THIN
            if isinstance(v, float):
                c.number_format = "#,##0.00"
    if widths:
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    return ws


def build_workbook(path: Path, cfg, base, buildings, audit, checks, sol):
    wb = Workbook()
    wb.remove(wb.active)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    src = f"口径来源：{KOUJING_VERSION}；生成时间：{stamp}"

    _write_sheet(wb, "01_输入条件与完整度", ["分组", "条件", "级别", "是否提供", "缺失影响", "建议来源"],
                 [[i["分组"], i["条件"], i["级别"], i["是否提供"], i["缺失影响"], i["建议来源"]]
                  for i in audit["明细"]],
                 widths=[12, 22, 8, 10, 54, 36],
                 notes=[src,
                        f"完备度得分：{audit['完备度得分']} / 100　数据等级：{audit['等级']}　{audit['等级说明']}"])

    order = ["总计容面积", "住宅计容面积", "极限层数", "极限层数取整", "住宅基底面积上限", "架空层面积",
             "保障房计容面积", "商品房计容面积", "商品房奖励面积", "保障房奖励面积", "架空层奖励面积",
             "总奖励面积", "商品房面积", "保障房面积", "保障房基底面积", "商品房户均面积",
             "商品房平均户数", "保障房户数", "保障房折算层数", "商品房总建筑面积",
             "保障房总建筑面积", "住宅总建筑面积"]
    _write_sheet(wb, "02_测算指标表", ["指标", "数值", "单位"],
                 [[k, round2(base[k]), _unit_hint(k)] for k in order],
                 widths=[26, 18, 10], notes=[src])

    _write_sheet(wb, "03_楼型参数表",
                 ["楼型", "户型组合", "每层户数", "标准层面积", "户均面积", "层数", "单栋户数",
                  "最大楼栋数", "取用区间", "告警"],
                 [[b["楼型"], b["户型组合文本"], b["每层户数"], round2(b["标准层面积"]),
                   round2(b["户均面积"]), b["层数"], b["单栋户数"], round2(b["最大楼栋数"]),
                   f"{b['楼栋数区间'][0]}—{b['楼栋数区间'][1]}", b["告警"] or ""] for b in buildings],
                 widths=[10, 22, 10, 14, 12, 8, 12, 12, 12, 42], notes=[src])

    rows = []
    for idx, s in enumerate(sol["可行方案"], start=1):
        rows.append([
            f"方案{idx}" + ("（推荐）" if idx == 1 else ""),
            "、".join(f"{c} {n}栋" for c, n in zip(sol["楼型顺序"], s["楼栋数"]) if n > 0) or "—",
            round2(s["商品房面积汇总"]), round2(s["面积利用率"] * 100), s["楼栋数汇总"],
            round2(s["总基底面积"]), round2(s["总建筑密度"] * 100), round2(s["密度利用率"] * 100),
            round2(s["商品房户数汇总"]), round2(s["配比偏差"] * 100),
            round2(s["计算户均面积"]), round2(s["score"]),
        ])
    _write_sheet(wb, "04_方案比选",
                 ["方案", "楼栋配置", "商品房面积汇总", "面积利用率%", "楼栋数合计", "总基底面积",
                  "总建筑密度%", "密度利用率%", "户数合计", "配比偏差%", "计算户均面积", "综合得分"],
                 rows, widths=[16, 34, 16, 13, 12, 14, 13, 13, 12, 12, 14, 11],
                 notes=[src] + list(sol["提示"]))

    rec = sol.get("推荐方案")
    _write_sheet(wb, "05_配比对账", ["面积段", "输入套数比%", "推荐方案计算套数比%", "偏差（百分点）"],
                 [[d["面积段"], round2(d["套数比"] * 100),
                   round2(rec["计算套数比"].get(d["面积段"], 0.0) * 100) if rec else 0.0,
                   round2((rec["计算套数比"].get(d["面积段"], 0.0) - d["套数比"]) * 100) if rec else 0.0]
                  for d in base["配比明细"]],
                 widths=[12, 16, 24, 18], notes=[src, "仅对推荐方案对账；其余方案见 04_方案比选"])

    rows = []
    if rec:
        for code, nc in zip(sol["楼型顺序"], rec["楼栋数"]):
            if nc <= 0:
                continue
            b = next(x for x in buildings if x["标识"] == code)
            rows.append([b["楼型"], b["户型组合文本"], b["层数"], nc, round2(b["标准层面积"]),
                         round2(b["标准层面积"] * b["层数"] * nc), round2(b["单栋户数"] * nc)])
    _write_sheet(wb, "06_推荐方案明细", ["楼型", "户型组合", "层数", "楼栋数", "标准层面积", "建筑面积", "户数"],
                 rows, widths=[10, 22, 8, 10, 14, 16, 12], notes=[src])

    _write_sheet(wb, "07_合规与自洽性校验", ["编号", "级别", "校验事项", "结论", "建议处置"],
                 [[c["编号"], c["级别"], c["校验事项"], c["结论"], c["建议处置"]] for c in checks],
                 widths=[10, 8, 24, 60, 50], notes=[src])

    _write_sheet(wb, "08_待补充条件清单", ["分组", "条件", "级别", "缺失影响", "建议来源"],
                 [[i["分组"], i["条件"], i["级别"], i["缺失影响"], i["建议来源"]] for i in audit["待补充"]],
                 widths=[12, 22, 8, 54, 36],
                 notes=[src, "补充顺序建议：必填 → 重要 → 外部条件"])
    wb.save(path)
    return path


def _upgrade_hint(audit):
    ext = [i["条件"] for i in audit["明细"] if i["级别"] == "外部" and i["是否提供"] == "未提供"]
    return "、".join(ext) if ext else "无（外部条件已齐备）"


def _model_hint(grade):
    if grade.startswith("L1"):
        return "建议只用于方案早期总量判断与楼型方向筛选，不用于对外承诺或成本锁定。"
    if grade.startswith("L2"):
        return "可用于方案阶段多方案比选与内部对账；报批前需补外部口径并逐条对齐规划细则。"
    if grade.startswith("L3"):
        return "可与报批指标表逐项对账，建议同时保留本报告作为口径留痕。"
    return "条件不足，先补条件再测算。"


def build_report_md(cfg, base, buildings, audit, checks, sol, source_path: str, assumptions=None) -> str:
    assumptions = list(assumptions or [])
    L = []
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    jk = cfg["架空层"]
    bz = cfg["保障房"]
    bonus = cfg["面积奖励"]

    L.append(f"# {cfg['项目名称']}　可研阶段用地测算与楼型组合建议报告")
    L.append("")
    L.append(f"- 输入文件：`{source_path}`")
    L.append(f"- 生成时间：{stamp}")
    L.append(f"- 计算口径：{KOUJING_VERSION}")
    L.append(f"- 数据等级：**{audit['等级']}**（完备度 {audit['完备度得分']}/100）　{audit['等级说明']}")
    L.append("")
    L.append("> 本报告数值由输入条件按固定公式链计算得出，未使用估计值。第二章列出的自动补值项须复核后方可作为报批依据。")
    L.append("")

    L.append("## 一、输入条件")
    L.append("")
    L.append("| 条件 | 取值 |")
    L.append("| --- | --- |")
    L.append(f"| 用地面积 | {fmt(cfg['用地面积'])} m² |")
    L.append(f"| 容积率 | {cfg['容积率']:g} |")
    L.append(f"| 限高 | {fmt(cfg['限高'])} m |")
    L.append(f"| 建筑密度 | {cfg['建筑密度']:.4f}（{cfg['建筑密度'] * 100:.2f}%） |")
    L.append(f"| 层高 / 室内外高差 / 女儿墙 | {cfg['层高']:g} / {cfg['室内外高差']:g} / {cfg['女儿墙']:g} m |")
    L.append(f"| 商业 / 公共配套 / 物业用房 / 配电房 | {fmt(cfg['商业面积'])} / {fmt(cfg['公共配套面积'])} / "
             f"{fmt(cfg['物业用房面积'])} / {fmt(cfg['配电房和开闭站面积'])} m² |")
    L.append(f"| 非住宅基底面积 | {fmt(cfg['非住宅基底面积'])} m² |")
    L.append(f"| 架空层 | {'有，高度 %g m，架空率 %g' % (jk['高度'], jk['架空率']) if jk['启用'] else '无'} |")
    L.append(f"| 保障房 | {('有，按%s，户均 %g m²，标准层 %g 户' % (bz['模式'], bz['户均面积'], bz['标准层户数'])) if bz['启用'] else '无'} |")
    L.append(f"| 奖励系数（商品房 / 保障房 / 架空层） | {bonus['商品房奖励系数']:g} / "
             f"{bonus['保障房奖励系数']:g} / {bonus['架空层奖励系数']:g} |")
    L.append(f"| 户型配比口径 | {cfg['户型配比']['口径']} |")
    L.append("")
    L.append("| 面积段(m²) | " + " | ".join(f"{d['面积段']:g}" for d in base["配比明细"]) + " |")
    L.append("| --- | " + " | ".join("---" for _ in base["配比明细"]) + " |")
    L.append("| 套数比 | " + " | ".join(f"{d['套数比'] * 100:.2f}%" for d in base["配比明细"]) + " |")
    L.append("")

    L.append("## 二、输入条件完整度体检与补充建议")
    L.append("")
    L.append(f"完备度得分 **{audit['完备度得分']}/100**，数据等级 **{audit['等级']}**；"
             f"必填缺 {audit['必填缺失数']} 项，重要缺 {audit['重要缺失数']} 项，外部条件覆盖 {audit['外部条件覆盖']}。")
    L.append("")
    if audit["待补充"]:
        L.append("| 级别 | 待补充条件 | 缺失影响 | 建议来源 |")
        L.append("| --- | --- | --- | --- |")
        for i in audit["待补充"]:
            L.append(f"| {i['级别']} | {i['条件']} | {i['缺失影响']} | {i['建议来源']} |")
    else:
        L.append("所有必填、重要与外部条件均已提供，可直接进入报批对账。")
    L.append("")
    L.append(f"**提升到更高等级还需补充**：{_upgrade_hint(audit)}")
    L.append("")
    if assumptions:
        L.append("**自动补值项（按 0 计，须复核）**：")
        L.append("")
        for a in assumptions:
            L.append(f"- {a}")
        L.append("")

    L.append("## 三、测算模型建议")
    L.append("")
    L.append("**模型类型**：指标倒推 + 楼型组合枚举比选。")
    L.append("")
    L.append("**模型逻辑**：由用地条件倒推总计容面积与住宅计容面积 → 按配建与奖励口径还原可建面积 → "
             "由限高链推出极限层数 → 在楼型候选与楼栋数空间内枚举组合 → 按面积利用率、配比符合度、"
             "户均达成度、集约度四项打分，输出推荐方案与备选方案。")
    L.append("")
    L.append("**关键公式链**：")
    L.append("")
    L.append("| 环节 | 公式 |")
    L.append("| --- | --- |")
    L.append("| 总计容面积 | 用地面积 × 容积率 |")
    L.append("| 住宅计容面积 | 总计容面积 − 商业 − 公共配套 − 物业用房 − 配电房和开闭站 |")
    L.append("| 极限层数 | (限高 − 架空层高度 − 室内外高差 − 女儿墙) ÷ 层高，向下取整 |")
    L.append("| 商品房计容面积 | 住宅计容面积 − 保障房计容面积 |")
    L.append("| 商品房面积 | 商品房计容面积 + 商品房奖励面积 + 架空层奖励面积 |")
    L.append("| 商品房户均面积 | Σ(套数比 × 面积段) |")
    L.append("| 楼型标准层面积 | Σ(户型面积段 × 该户型数量) |")
    L.append("| 楼型最大楼栋数 | 商品房面积 ÷ (标准层面积 × 层数) |")
    L.append("| 组合建筑面积 | Σ(标准层面积 × 层数 × 楼栋数) |")
    L.append("| 总建筑密度 | (商品房基底 + 保障房基底 + 非住宅基底) ÷ 用地面积 |")
    L.append("")
    L.append("**模型适用边界**：本模型为物理指标模型，不含日照、退线、消防、地下车库、货值与成本；"
             "容积率与建筑密度按上限假定，实际方案须叠加上述专项校核。")
    L.append("")
    L.append("**模型选用建议**：")
    L.append("")
    L.append(f"- 当前数据等级 {audit['等级']}：{_model_hint(audit['等级'])}")
    L.append("- 需货值比选时，补入成本售价假设，模型扩展为「物理指标 + 货值」双维比选。")
    L.append("- 需报批对账时，补入规划细则版本与赠送面积口径，并按细则逐条核对奖励系数。")
    L.append("")

    L.append("## 四、指标测算结果")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("| --- | --- |")
    for k in ["总计容面积", "住宅计容面积", "极限层数", "住宅基底面积上限", "架空层面积",
              "保障房计容面积", "商品房计容面积", "商品房奖励面积", "保障房奖励面积",
              "架空层奖励面积", "商品房面积", "保障房面积", "保障房基底面积", "商品房户均面积",
              "商品房平均户数", "保障房户数", "商品房总建筑面积", "保障房总建筑面积", "住宅总建筑面积"]:
        unit = _unit_hint(k) or ""
        L.append(f"| {k} | {fmt(base[k])} {unit} |")
    L.append("")

    L.append("## 五、楼型参数")
    L.append("")
    L.append("| 楼型 | 户型组合 | 每层户数 | 标准层面积 | 户均面积 | 层数 | 单栋户数 | 最大楼栋数 | 取用区间 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for b in buildings:
        L.append(f"| {b['楼型']} | {b['户型组合文本']} | {b['每层户数']} | {fmt(b['标准层面积'])} | "
                 f"{fmt(b['户均面积'])} | {b['层数']} | {b['单栋户数']} | {fmt(b['最大楼栋数'])} | "
                 f"{b['楼栋数区间'][0]}—{b['楼栋数区间'][1]} |")
    warns = [f"{b['楼型']}：{b['告警']}" for b in buildings if b["告警"]]
    if warns:
        L.append("")
        L.append("**参数告警**：" + "；".join(warns))
    L.append("")

    L.append("## 六、楼型组合方案比选")
    L.append("")
    for t in sol["提示"]:
        L.append(f"> {t}")
    if sol["提示"]:
        L.append("")
    if sol["可行方案"]:
        L.append("评分权重：面积利用率 40% + 配比符合度 25% + 户均达成度 20% + 集约度 15%。")
        L.append("")
        L.append("| 方案 | 楼栋配置 | 商品房面积汇总 | 面积利用率 | 楼栋数 | 总建筑密度 | 户数合计 | 配比偏差 | 综合得分 |")
        L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for idx, s in enumerate(sol["可行方案"], start=1):
            txt = "、".join(f"{c} {n} 栋" for c, n in zip(sol["楼型顺序"], s["楼栋数"]) if n > 0) or "—"
            tag = "方案1（推荐）" if idx == 1 else f"方案{idx}"
            L.append(f"| {tag} | {txt} | {fmt(s['商品房面积汇总'])} | {s['面积利用率'] * 100:.2f}% | "
                     f"{s['楼栋数汇总']} | {s['总建筑密度'] * 100:.2f}% | {fmt(s['商品房户数汇总'])} | "
                     f"{s['配比偏差'] * 100:.2f}% | {s['score']:.1f} |")
        L.append("")
        rec = sol["推荐方案"]
        L.append(f"**推荐方案明细**（商品房面积汇总 {fmt(rec['商品房面积汇总'])} m²，占可建面积 "
                 f"{rec['面积利用率'] * 100:.2f}%，建筑密度 {rec['总建筑密度'] * 100:.2f}% / 上限 "
                 f"{cfg['建筑密度'] * 100:.2f}%）")
        L.append("")
        L.append("| 楼型 | 户型组合 | 层数 | 楼栋数 | 标准层面积 | 建筑面积 | 户数 |")
        L.append("| --- | --- | --- | --- | --- | --- | --- |")
        for code, nc in zip(sol["楼型顺序"], rec["楼栋数"]):
            if nc <= 0:
                continue
            b = next(x for x in buildings if x["标识"] == code)
            L.append(f"| {b['楼型']} | {b['户型组合文本']} | {b['层数']} | {nc} | {fmt(b['标准层面积'])} | "
                     f"{fmt(b['标准层面积'] * b['层数'] * nc)} | {fmt(b['单栋户数'] * nc)} |")
        L.append("")
        L.append("**配比对账**（推荐方案）")
        L.append("")
        L.append("| 面积段(m²) | 输入套数比 | 计算套数比 | 偏差（百分点） |")
        L.append("| --- | --- | --- | --- |")
        for d in base["配比明细"]:
            a = d["面积段"]
            calc = rec["计算套数比"].get(a, 0.0)
            L.append(f"| {a:g} | {d['套数比'] * 100:.2f}% | {calc * 100:.2f}% | {(calc - d['套数比']) * 100:+.2f} |")
        L.append("")
        L.append(f"剩余未用商品房面积 {fmt(base['商品房面积'] - rec['商品房面积汇总'])} m²"
                 f"（占 {100 - rec['面积利用率'] * 100:.2f}%），可通过提升层数、增加楼栋或调整户型组合消化。")
    else:
        L.append("现有输入条件下未找到满足面积与密度约束的合法组合，请先处理第七章校验项。")
    L.append("")

    L.append("## 七、合规与自洽性校验")
    L.append("")
    L.append("| 编号 | 级别 | 校验事项 | 结论 | 建议处置 |")
    L.append("| --- | --- | --- | --- | --- |")
    for c in checks:
        L.append(f"| {c['编号']} | {c['级别']} | {c['校验事项']} | {c['结论']} | {c['建议处置']} |")
    L.append("")

    L.append("## 八、口径提示（与原程序差异）")
    L.append("")
    L.append("1. **保障房户数**：原程序中 `保障房户数 = 保障房面积 ÷ (标准层户数 × 户均面积)`，该式量纲实为「层数」。"
             "本报告按 `保障房面积 ÷ 户均面积` 计户数，并另列「保障房折算层数」保留原值。")
    L.append("2. **商品房基底面积**：程序使用说明写「(n × 户均面积 + 20) × 楼栋数」，"
             "实际代码为 `标准层面积 × 楼栋数`（未含 +20）。本报告与代码一致，+20 仅用于预估最大楼栋数。")
    L.append("3. **阳台与屋顶机房**：本版按 0 计（程序注释说明户配表面积已含机房与阳台）；"
             "若地方细则单独计容，需在奖励系数中体现。")
    L.append("")

    L.append("## 九、下一步建议")
    L.append("")
    if audit["待补充"]:
        L.append("1. 按第二章清单补齐条件，优先必填项，其次重要项，最后外部条件。")
    L.append("2. 对第二章列出的自动补值项逐项复核；确认后重跑，报告数值随之更新。")
    high = [c for c in checks if c["级别"] == "高"]
    if high:
        L.append(f"3. 优先处理 {len(high)} 项高等级校验问题：" +
                 "；".join(f"{c['编号']} {c['校验事项']}" for c in high) + "。")
    L.append("4. 推荐方案需经方案专业校核日照、退线、消防与地下车库后，方可作为报批基础。")
    L.append("5. 需货值比选时，补充成本售价假设后重跑，模型可扩展为货值维度。")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"*本报告由 keyan-calculation skill 自动生成；口径源：{KOUJING_VERSION}。*")
    return "\n".join(L)


def write_template(path: Path):
    if not HAS_OPENPYXL:
        raise InputError("生成 xlsx 模板需要 openpyxl")
    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("用地条件")
    ws.append(["参数", "数值", "说明"])
    for c in ws[1]:
        c.font = HEAD_FONT
        c.fill = HEADER_FILL
    for r in [
        ("项目名称", "示例项目A", "报表与报告标题"),
        ("用地面积", 36172.58, "m²，来自挂牌文件或规划设计条件通知书"),
        ("容积率", 2.0, "区间时取上限验算"),
        ("限高", 60.0, "m"),
        ("建筑密度", 0.28, "输入小数，如 0.28"),
        ("层高", 2.9, "m"),
        ("商业面积", 500.0, "m²"),
        ("非住宅基底面积", 500.0, "m²，商业与配套基底"),
        ("公共配套面积", 685.0, "m²"),
        ("物业用房面积", 200.0, "m²"),
        ("配电房和开闭站面积", 400.0, "m²"),
        ("室内外高差", 0.15, "m"),
        ("女儿墙", 0.55, "m"),
        ("架空层启用", "无", "有 / 无"),
        ("架空层高度", 3.3, "m，仅在启用时填写"),
        ("架空率", 0.3, "小数，仅在启用时填写"),
        ("保障房启用", "无", "有 / 无"),
        ("保障房模式", "面积比例", "面积比例 / 输入面积"),
        ("保障房面积比例", 0.05, "小数"),
        ("保障房输入面积", 0.0, "m²，仅在「输入面积」模式填写"),
        ("保障房户均面积", 50.0, "m²/户"),
        ("保障房标准层户数", 4, "户"),
        ("商品房奖励系数", 0.0, "小数，如 0.03"),
        ("保障房奖励系数", 0.0, "小数"),
        ("架空层奖励系数", 0.0, "小数"),
        ("配比口径", "套数比", "套数比 / 面积比"),
    ]:
        ws.append(list(r))
    for col, w in zip("ABC", (24, 16, 40)):
        ws.column_dimensions[col].width = w

    ws2 = wb.create_sheet(SHEET_HAOHU)
    ws2.append(["面积段", "套数比", "面积比"])
    for c in ws2[1]:
        c.font = HEAD_FONT
        c.fill = HEADER_FILL
    for a, p in [(80, 0.2), (90, 0.2), (100, 0.2), (110, 0.2), (120, 0.2)]:
        ws2.append([a, p, None])
    for col in "ABC":
        ws2.column_dimensions[col].width = 12

    ws3 = wb.create_sheet(SHEET_LOUXING)
    ws3.append(["楼型", "户型组合", "层数", "楼栋数下限", "楼栋数上限"])
    for c in ws3[1]:
        c.font = HEAD_FONT
        c.fill = HEADER_FILL
    for r in [("T8", "80x2+90x4+120x2", None, 0, None),
              ("T4", "90x2+100x2", None, 0, None),
              ("T4", "100x2+120x2", None, 0, None),
              ("T2", "110x2", None, 0, None)]:
        ws3.append(list(r))
    for col, w in zip("ABCDE", (10, 24, 10, 14, 14)):
        ws3.column_dimensions[col].width = w

    ws4 = wb.create_sheet(SHEET_WAI_BU)
    ws4.append(["参数", "数值"])
    for c in ws4[1]:
        c.font = HEAD_FONT
        c.fill = HEADER_FILL
    for r in [("规划细则版本", ""), ("日照要求", ""), ("退线要求", ""), ("车位配比", ""),
              ("得房率上限", ""), ("赠送面积口径", ""), ("地下室指标", ""), ("装配式要求", ""),
              ("成本售价假设", "")]:
        ws4.append(list(r))
    ws4.column_dimensions["A"].width = 20
    ws4.column_dimensions["B"].width = 60

    ws5 = wb.create_sheet("寻优参数")
    ws5.append(["参数", "数值"])
    for c in ws5[1]:
        c.font = HEAD_FONT
        c.fill = HEADER_FILL
    for r in [("面积下浮下限", 0.03), ("配比偏差上限", 0.05), ("密度利用下限", 0.80), ("方案数上限", 5)]:
        ws5.append(list(r))
    ws5.column_dimensions["A"].width = 20
    ws5.column_dimensions["B"].width = 12

    wb.save(path)
    return path


def cmd_check(args):
    raw = read_input(Path(args.input))
    cfg, assumptions = normalize(raw)
    audit = audit_input(raw)
    base = compute_base(cfg)
    buildings = compute_buildings(cfg, base)
    checks = consistency_checks(cfg, base, buildings)
    print(json.dumps({
        "项目名称": cfg["项目名称"],
        "输入文件": args.input,
        "完备度得分": audit["完备度得分"],
        "数据等级": audit["等级"],
        "等级说明": audit["等级说明"],
        "自动补值项": assumptions,
        "待补充": audit["待补充"],
        "校验问题": [c for c in checks if c["级别"] in ("高", "中")],
        "关键指标": {k: round2(base[k]) for k in
                 ["总计容面积", "住宅计容面积", "极限层数", "商品房面积", "保障房面积", "商品房户均面积"]},
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def _prepare(args):
    raw = read_input(Path(args.input))
    cfg, assumptions = normalize(raw)
    base = compute_base(cfg)
    buildings = compute_buildings(cfg, base)
    audit = audit_input(raw)
    checks = consistency_checks(cfg, base, buildings)
    sol = solve(cfg, base, buildings)
    return raw, cfg, assumptions, base, buildings, audit, checks, sol


def cmd_calc(args):
    _raw, cfg, assumptions, base, buildings, audit, checks, sol = _prepare(args)
    out = {
        "项目名称": cfg["项目名称"],
        "数据等级": audit["等级"],
        "完备度得分": audit["完备度得分"],
        "自动补值项": assumptions,
        "关键指标": {k: round2(base[k]) for k in
                 ["总计容面积", "住宅计容面积", "极限层数", "商品房计容面积", "商品房面积",
                  "保障房面积", "商品房户均面积", "商品房平均户数"]},
        "楼型参数": [{k: (round2(v) if isinstance(v, float) else v)
                  for k, v in b.items() if k != "户型组合"} for b in buildings],
        "推荐方案": None,
        "备选方案": [],
        "提示": sol["提示"],
        "校验问题": [c for c in checks if c["级别"] in ("高", "中")],
    }
    rec = sol.get("推荐方案")
    if rec:
        out["推荐方案"] = {
            "楼栋配置": {c: n for c, n in zip(sol["楼型顺序"], rec["楼栋数"])},
            "商品房面积汇总": round2(rec["商品房面积汇总"]),
            "面积利用率": round2(rec["面积利用率"] * 100),
            "总建筑密度": round2(rec["总建筑密度"] * 100),
            "密度上限": round2(cfg["建筑密度"] * 100),
            "户数合计": round2(rec["商品房户数汇总"]),
            "配比偏差": round2(rec["配比偏差"] * 100),
            "计算户均面积": round2(rec["计算户均面积"]),
            "综合得分": round2(rec["score"]),
        }
        out["备选方案"] = [
            {"楼栋配置": {c: n for c, n in zip(sol["楼型顺序"], s["楼栋数"])},
             "商品房面积汇总": round2(s["商品房面积汇总"]),
             "面积利用率": round2(s["面积利用率"] * 100),
             "配比偏差": round2(s["配比偏差"] * 100),
             "综合得分": round2(s["score"])}
            for s in sol["备选方案"]
        ]
    if args.outdir:
        d = Path(args.outdir)
        d.mkdir(parents=True, exist_ok=True)
        out["报表文件"] = str(build_workbook(
            d / f"{cfg['项目名称']}_测算数据报表.xlsx", cfg, base, buildings, audit, checks, sol))
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_report(args):
    _raw, cfg, assumptions, base, buildings, audit, checks, sol = _prepare(args)
    d = Path(args.outdir)
    d.mkdir(parents=True, exist_ok=True)
    xlsx = build_workbook(d / f"{cfg['项目名称']}_测算数据报表.xlsx",
                          cfg, base, buildings, audit, checks, sol)
    md_path = d / f"{cfg['项目名称']}_可研测算与楼型组合建议报告.md"
    md_path.write_text(build_report_md(cfg, base, buildings, audit, checks, sol, args.input, assumptions),
                       encoding="utf-8")
    csv_path = d / f"{cfg['项目名称']}_关键指标.csv"
    keys = ["总计容面积", "住宅计容面积", "极限层数", "住宅基底面积上限", "架空层面积",
            "保障房计容面积", "商品房计容面积", "商品房奖励面积", "保障房奖励面积",
            "架空层奖励面积", "商品房面积", "保障房面积", "保障房基底面积", "商品房户均面积",
            "商品房平均户数", "保障房户数", "商品房总建筑面积", "保障房总建筑面积", "住宅总建筑面积"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["指标", "数值"])
        for k in keys:
            w.writerow([k, round2(base[k])])
    print(json.dumps({
        "项目名称": cfg["项目名称"],
        "数据等级": audit["等级"],
        "完备度得分": audit["完备度得分"],
        "报表文件": str(xlsx),
        "报告文件": str(md_path),
        "指标文件": str(csv_path),
        "推荐方案": (dict(zip(sol["楼型顺序"], sol["推荐方案"]["楼栋数"])) if sol.get("推荐方案") else None),
        "高等级校验问题": [c["编号"] + " " + c["校验事项"] for c in checks if c["级别"] == "高"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_template(args):
    print(json.dumps({"模板文件": str(write_template(Path(args.out)))}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="可研阶段用地测算与楼型组合寻优")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="输入条件完整度体检与补充建议")
    p.add_argument("--input", required=True)
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("calc", help="指标测算与楼型组合寻优")
    p.add_argument("--input", required=True)
    p.add_argument("--outdir", default=None)
    p.set_defaults(func=cmd_calc)
    p = sub.add_parser("report", help="输出数据报表与测算报告")
    p.add_argument("--input", required=True)
    p.add_argument("--outdir", required=True)
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("template", help="生成 xlsx 输入模板")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_template)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except InputError as e:
        print(json.dumps({"ok": False, "错误": str(e)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
