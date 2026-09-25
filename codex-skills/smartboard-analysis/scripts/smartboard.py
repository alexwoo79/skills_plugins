#!/usr/bin/env python3
"""Channel B driver for smartboard-mcp (stdio JSON-RPC, no MCP tool discovery).

Usage:
  python3 smartboard.py run --path /abs/data.csv [--skill-id industry-x] [--title T] [--out O] [--json-out J]
  python3 smartboard.py call <tool_name> [--json '{"arg": "val"}']
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

PROTOCOL_VERSION = "2025-03-26"


def _resolve_launcher():
    """Return the smartboard-mcp launcher (or binary) plus optional extra env."""
    env = os.environ.get("SMARTBOARD_MCP_SH")
    if env and os.path.exists(env):
        return env, None
    bin_env = os.environ.get("SMARTBOARD_MCP_BIN")
    if bin_env and os.path.exists(bin_env):
        return bin_env, None
    base = os.path.expanduser("~/.smartboard/mcp")
    launcher = os.path.join(base, "smartboard-mcp.sh")
    if os.path.exists(launcher):
        return launcher, None
    for name in ("smartboard-mcp", "smartboard-mcp.exe"):
        binary = os.path.join(base, name)
        if os.path.exists(binary):
            extra = {
                "SMARTBOARD_SKILLS_DIR": os.environ.get("SMARTBOARD_SKILLS_DIR", os.path.join(base, "skills")),
                "SMARTBOARD_WEB_DIR": os.environ.get("SMARTBOARD_WEB_DIR", os.path.join(base, "web")),
                "LLM_CONFIG_PATH": os.environ.get("LLM_CONFIG_PATH", os.path.expanduser("~/.smartboard/llm_config.json")),
            }
            return binary, extra
    raise SystemExit("未找到 smartboard-mcp，请先运行安装脚本（macOS/Linux: install.sh；Windows: install.ps1）")


def _launch():
    cmd, extra = _resolve_launcher()
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return subprocess.Popen(
        [cmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _call_tool(tool_name, arguments, timeout=180):
    proc = _launch()

    def send(obj):
        proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "codex", "version": "1.0"},
        },
    })
    proc.stdout.readline()
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    send({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })

    response = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == 2:
            response = obj
            break

    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=5)

    stderr = proc.stderr.read() if proc.stderr else ""
    return response, stderr


def _extract(response):
    if response is None:
        return {"__error__": "no response from smartboard-mcp"}
    if "error" in response:
        return {"__error__": response["error"]}
    result = response.get("result")
    if isinstance(result, dict):
        content = result.get("content") or []
        texts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = "\n".join(texts)
        if joined:
            try:
                return json.loads(joined)
            except json.JSONDecodeError:
                out = dict(result)
                out["__raw_content__"] = joined
                return out
        return result
    return {"__raw__": result}


def cmd_run(args):
    arguments = {"path": args.path}
    if args.skill_id:
        arguments["skill_id"] = args.skill_id
    if args.filter:
        arguments["filter"] = args.filter
    if args.title:
        arguments["title"] = args.title
    if args.out:
        arguments["out"] = args.out
    else:
        ts = time.strftime("%Y%m%d-%H%M%S")
        arguments["out"] = os.path.join(os.getcwd(), f"smartboard-dashboard-{ts}.html")
    if args.json_out:
        arguments["json_out"] = args.json_out
    for p in (arguments.get("out"), arguments.get("json_out")):
        if p:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)

    response, stderr = _call_tool("smartboard_run_pipeline", arguments)
    data = _extract(response)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if stderr.strip():
        print("STDERR: " + stderr.strip(), file=sys.stderr)
    if isinstance(data, dict) and data.get("ok") is True:
        return 0
    return 1


def cmd_call(args):
    if args.json is not None:
        arguments = json.loads(args.json)
    else:
        raw = sys.stdin.read()
        arguments = json.loads(raw) if raw.strip() else {}
    if not isinstance(arguments, dict):
        arguments = {"__error__": "arguments must be a JSON object"}

    response, stderr = _call_tool(args.tool, arguments)
    data = _extract(response)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if stderr.strip():
        print("STDERR: " + stderr.strip(), file=sys.stderr)
    if isinstance(data, dict) and "__error__" in data:
        return 1
    return 0


class _Session:
    """单进程 MCP 会话：多步工具调用共享活动数据集。"""

    def __init__(self):
        cmd, extra = _resolve_launcher()
        env = dict(os.environ)
        if extra:
            env.update(extra)
        self.proc = subprocess.Popen(
            [cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        self._next_id = 1
        self._send({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "codex", "version": "1.0"},
            },
        })
        self.proc.stdout.readline()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def call(self, name, args=None, timeout=180):
        i = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == i:
                return _extract(obj)
        return {"__error__": "timeout waiting for smartboard-mcp"}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass


def _read_csv_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _parse_cond(s):
    """解析单个条件：列名 运算符 值（与 MCP 端语法一致）。"""
    s = s.strip()
    if not s:
        return None
    if " in " in s:
        idx = s.find(" in ")
        return {"column": s[:idx].strip(), "op": "in", "value": s[idx + 4:].strip()}
    for sep in (" ~ ", " ～ ", " contains "):
        if sep in s:
            idx = s.find(sep)
            return {"column": s[:idx].strip(), "op": "contains", "value": s[idx + len(sep):].strip()}
    for op_str in ("!=", ">=", "<=", "=", ">", "<"):
        idx = s.find(op_str)
        if idx < 0:
            continue
        col = s[:idx].strip()
        if not col:
            continue
        val = s[idx + len(op_str):].strip()
        op = {"=": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[op_str]
        return {"column": col, "op": op, "value": val}
    return None


def _row_match(row, colmap, cond):
    """单行是否满足单个条件（列名大小写不敏感，数值列按数值比较）。"""
    col = cond["column"]
    actual = colmap.get(_norm_exact(col), col)
    if actual not in row:
        return False
    raw = row.get(actual)
    val = cond["value"]
    op = cond["op"]
    try:
        num_val = float(val.replace(",", ""))
        num_ok = True
    except ValueError:
        num_val = None
        num_ok = False

    def _num(raw):
        if raw is None:
            return None
        try:
            return float(str(raw).replace(",", "").strip())
        except ValueError:
            return None

    if op in ("eq", "ne", "gt", "gte", "lt", "lte") and num_ok:
        cell = _num(raw)
        if cell is None:
            return False
        return {
            "eq": cell == num_val,
            "ne": cell != num_val,
            "gt": cell > num_val,
            "gte": cell >= num_val,
            "lt": cell < num_val,
            "lte": cell <= num_val,
        }[op]

    text = str(raw).strip() if raw is not None else ""
    if op == "eq":
        return text == val
    if op == "ne":
        return text != val
    if op in ("gt", "gte", "lt", "lte"):
        return {"gt": text > val, "gte": text >= val, "lt": text < val, "lte": text <= val}[op]
    if op == "contains":
        return val in text
    if op == "in":
        clean = val.replace("[", "").replace("]", "").replace("(", "").replace(")", "").replace('"', "").replace("'", "")
        items = [x.strip() for x in re.split(r"[,，、;]", clean) if x.strip()]
        for item in items:
            try:
                iv = float(item.replace(",", ""))
            except ValueError:
                iv = None
            if iv is not None:
                if _num(raw) == iv:
                    return True
            elif text == item:
                return True
        return False
    return False


def _apply_filter_rows(rows, expr):
    """按筛选表达式过滤行：& 为 AND 组，| 为组内 OR（与 MCP 端一致）。"""
    if not expr or not expr.strip():
        return rows
    colmap = {}
    for r in rows:
        for k in r:
            colmap.setdefault(_norm_exact(k), k)

    def matches(row):
        for group in expr.split("&"):
            group = group.strip()
            if not group:
                continue
            or_parts = [p.strip() for p in group.split("|") if p.strip()]
            hits = [_row_match(row, colmap, c) for c in (_parse_cond(p) for p in or_parts) if c]
            if not hits:
                continue
            if not any(hits):
                return False
        return True

    return [r for r in rows if matches(r)]


def _col_aggs(rows, columns):
    aggs = {}
    for c in columns:
        vals = []
        for r in rows:
            raw = (r.get(c) or "").strip()
            if raw == "":
                continue
            try:
                vals.append(float(raw))
            except ValueError:
                pass
        if vals:
            aggs[c] = {
                "sum": sum(vals),
                "mean": sum(vals) / len(vals),
                "count": len(vals),
                "min": min(vals),
                "max": max(vals),
            }
    return aggs


def _norm_exact(s):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (s or "").lower())


_FUZZY_SUFFIX = re.compile(r"(id|amount|count|number|value|rate|ratio|total|num|qty|amt)$")


def _norm_fuzzy(s):
    n = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (s or "").lower())
    return _FUZZY_SUFFIX.sub("", n)


def _exact_col(name, columns):
    ne = _norm_exact(name)
    for c in columns:
        if _norm_exact(c) == ne:
            return c
    return None


def _match_col(token, columns):
    c = _exact_col(token, columns)
    if c:
        return c
    tf = _norm_fuzzy(token)
    if not tf:
        return None
    best = None
    for c in columns:
        cf = _norm_fuzzy(c)
        if cf and (tf == cf or cf.startswith(tf) or cf.endswith(tf) or tf.startswith(cf) or tf.endswith(cf)):
            if best is None or len(cf) > len(_norm_fuzzy(best)):
                best = c
    return best


def _eval_logic(logic, columns, aggs):
    if not logic:
        return None
    expr = logic
    for fn, col in re.findall(r"(SUM|COUNT|AVG|MIN|MAX)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", logic, re.I):
        c = _match_col(col, columns)
        if not c or c not in aggs:
            return None
        fn_u = fn.upper()
        if fn_u == "AVG":
            val = aggs[c]["mean"]
        elif fn_u == "MIN":
            val = aggs[c]["min"]
        elif fn_u == "MAX":
            val = aggs[c]["max"]
        else:
            val = aggs[c]["sum"]
        expr = re.sub(re.escape(fn + "(" + col + ")"), str(val), expr, flags=re.I)
    if not re.fullmatch(r"[\d\s\.\+\-\*/\(\)]*", expr):
        return None
    try:
        return eval(expr, {"__builtins__": {}}, {})
    except Exception:
        return None


def _compute_metrics(metrics, columns, aggs):
    out = {}
    for m in metrics:
        mid = m.get("metricId", "")
        if not mid:
            continue
        c = _exact_col(mid, columns)
        if c and c in aggs:
            if "rate" in mid.lower() or m.get("unit") == "%":
                out[mid] = aggs[c]["mean"]
            else:
                out[mid] = aggs[c]["sum"]
            continue
        v = _eval_logic(m.get("calculationLogic", ""), columns, aggs)
        if v is not None:
            out[mid] = v
    return out


def _detect_date_column(columns):
    for c in columns:
        n = c.get("name", "")
        if n.lower() in ("date", "month", "time", "day") or n in ("时间", "月份", "日期"):
            return n
    return None


def _looks_like_id(name):
    n = name.lower()
    return any(k in n for k in ("id", "编号", "单号", "uuid", "code", "序号"))


def _pick_dimension(columns, date_col):
    string_cols = [c["name"] for c in columns if c.get("type") == "STRING" and c["name"] != date_col]
    for p in ("Course", "Campus", "Class", "Channel", "Teacher", "Category", "Product", "Region"):
        if p in string_cols:
            return p
    for c in string_cols:
        if not _looks_like_id(c):
            return c
    return string_cols[0] if string_cols else None


def _pick_top_metric(columns):
    names = [c["name"] for c in columns]
    for p in ("Payment", "Revenue", "Sales", "Amount"):
        if p in names:
            return p
    floats = [c["name"] for c in columns if c.get("type") == "FLOAT"]
    if floats:
        return floats[0]
    nums = [c["name"] for c in columns if c.get("type") in ("FLOAT", "INTEGER")]
    return nums[0] if nums else None


def _cac_mom_change(rows, colnames):
    spend = _match_col("ChannelSpend", colnames) or _match_col("Channel", colnames)
    enroll = _match_col("EnrolledStudents", colnames) or _match_col("Enrolled", colnames)
    date_col = "Month" if "Month" in colnames else _detect_date_column([{"name": n, "type": "STRING"} for n in colnames])
    if not (spend and enroll and date_col):
        return None
    per = {}
    for r in rows:
        k = r.get(date_col)
        try:
            s = float(r.get(spend) or 0)
            e = float(r.get(enroll) or 0)
        except ValueError:
            continue
        per.setdefault(k, [0.0, 0.0])
        per[k][0] += s
        per[k][1] += e
    cacs = []
    for k in sorted(per):
        s, e = per[k]
        cacs.append(s / e if e else None)
    cacs = [x for x in cacs if x is not None]
    if len(cacs) >= 2 and cacs[-2]:
        return (cacs[-1] - cacs[-2]) / cacs[-2]
    return None


def _top_dim_share(rows, dim, metric):
    if not dim or not metric:
        return None
    totals = {}
    for r in rows:
        try:
            v = float(r.get(metric) or 0)
        except ValueError:
            continue
        totals[r.get(dim)] = totals.get(r.get(dim), 0.0) + v
    if not totals:
        return None
    total = sum(totals.values())
    top_name = max(totals, key=totals.get)
    return (totals[top_name] / total if total else 0.0), top_name


def _fmt_money(v):
    if v is None:
        return "-"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.1f} 万"
    return f"{v:,.0f}"


def _fmt_pct(v):
    if v is None:
        return "-"
    return f"{v * 100:.1f}%"


def _metric_labels(skill):
    labels = {}
    for m in (skill or {}).get("metrics", []) or []:
        mid = m.get("metricId")
        if mid:
            labels[mid] = m.get("businessName", mid)
    return labels


def _is_rate_metric(mid, m):
    return m.get("unit") == "%" or "%" in (m.get("typicalRange") or "") or mid.lower().endswith(("rate", "conversion", "consumption", "fill"))


def _rule_display_value(rule, val):
    cond = rule.get("condition", {})
    mid = cond.get("metricId", "")
    compare = cond.get("compare", "absolute")
    if compare in ("mom_change", "ratio_to_total"):
        return f"{val * 100:.1f}%"
    if mid.lower().endswith(("rate", "conversion", "consumption", "fill")) or "Rate" in mid or "Conversion" in mid:
        return f"{val * 100:.1f}%"
    return f"{val:,.0f}"


def _evaluate_rules(rules, metrics, extra):
    alerts = []
    for r in (rules or {}).get("rules", []) or []:
        cond = r.get("condition", {})
        mid = cond.get("metricId", "")
        op = cond.get("operator", "")
        th = cond.get("threshold", 0)
        compare = cond.get("compare", "absolute")
        if compare == "absolute":
            val = metrics.get(mid)
            if val is None:
                continue
            hit = (val < th) if op == "lt" else (val > th) if op == "gt" else False
        elif compare == "mom_change":
            val = extra.get("cac_mom_change")
            if val is None:
                continue
            hit = val > th
        elif compare == "ratio_to_total":
            pair = extra.get("top_dim_share")
            if not pair:
                continue
            val = pair[0]
            hit = val > th
        else:
            continue
        if hit:
            alerts.append({"rule": r, "value": val, "display": _rule_display_value(r, val)})
    return alerts


def _render_alert(a):
    r = a["rule"]
    d = a["display"]
    title = r.get("titleTemplate", r.get("name", "")).replace("{value}", d).replace("{abs(change)}", d)
    sugg = r.get("suggestionTemplate", "").replace("{value}", d)
    return f"{title}。{sugg}" if sugg else title


def _core_conclusion(metrics, alerts, top_metric, labels):
    seg = []
    if top_metric and metrics.get(top_metric) is not None:
        seg.append(f"{labels.get(top_metric, top_metric)}合计 {_fmt_money(metrics[top_metric])}")
    for mid in ("RenewalRate", "RefundRate", "AttendanceRate", "CompletionRate", "ClassHourConsumption", "TrialConversionRate"):
        if metrics.get(mid) is not None:
            seg.append(f"{labels.get(mid, mid)} {_fmt_pct(metrics[mid])}")
    base = "样本整体经营" + ("，" + "、".join(seg) if seg else "")
    if alerts:
        crit = [a for a in alerts if a["rule"].get("severity") == "critical"]
        if crit:
            base += f"；其中 {len(crit)} 项关键预警（{'、'.join(a['rule'].get('name', '') for a in crit)}）需优先处理。"
        else:
            base += f"；存在 {len(alerts)} 项预警，建议按优先级跟进。"
    else:
        base += "，主要指标处于正常区间。"
    return base


def _key_findings(total_rows, columns, metrics, grouped, ts, top_metric, labels):
    out = [f"数据规模：{total_rows} 条记录、{len(columns)} 个字段。"]
    if top_metric and metrics.get(top_metric) is not None:
        out.append(f"{labels.get(top_metric, top_metric)}合计 {_fmt_money(metrics[top_metric])}。")
    g = (grouped or {}).get("grouped") or {}
    top = g.get("top") or []
    if top:
        top_str = "、".join(f"{x['name']} {_fmt_money(x['value'])}" for x in top[:5])
        out.append(f"{g.get('dimension', '维度')} Top：{top_str}。")
    if ts:
        mx, mn = ts.get("max_period"), ts.get("min_period")
        if mx and mn:
            out.append(f"时间趋势：峰值 {mx.get('period')}（{_fmt_money(mx.get('value'))}）、谷值 {mn.get('period')}（{_fmt_money(mn.get('value'))}）。")
        fast = ts.get("fastest_growth_periods") or []
        slow = ts.get("slowest_growth_periods") or []
        if fast:
            out.append(f"增长最快 {fast[0].get('period')}（环比 +{fast[0].get('mom_change_pct', 0):.1f}%）。")
        if slow:
            out.append(f"回落明显 {slow[0].get('period')}（环比 {slow[0].get('mom_change_pct', 0):.1f}%）。")
    return out


def _attribution(grouped, alerts, top_dim, top_metric):
    lines = []
    g = (grouped or {}).get("grouped") or {}
    top = g.get("top") or []
    dim = g.get("dimension") or top_dim
    if top and top_metric:
        lines.append(f"{dim} 维度上，{top[0]['name']} 的 {top_metric} 最高，为 {_fmt_money(top[0]['value'])}；该差异反映结构分布，不代表因果关系。")
    for a in alerts:
        if a["rule"].get("condition", {}).get("compare") == "ratio_to_total":
            lines.append(f"{a['display']}，产品线依赖集中（相关观察，非因果）。")
    lines.append("说明：本报告仅描述相关性与结构差异，未设置对照组，不进行因果推断；科目/校区/老师对比可能受生源结构等混杂因素影响。")
    return lines


def _risks(total_rows, columns, metrics):
    risks = []
    if total_rows < 100:
        risks.append(f"样本量仅 {total_rows} 条，统计显著性有限，结论宜谨慎外推。")
    risks.append("出勤率/完课率等比率列为行级预聚合值，按行平均，未按人次/课时加权，可能与明细口径有偏差。")
    risks.append("比率指标采用「SUM(分子)/SUM(分母)」聚合口径；明细级口径不同会改变结果。")
    risks.append("数据未含对照组与实验设计，不能据此推断因果。")
    return risks


def _actions(alerts):
    order = {"critical": 0, "warning": 1, "info": 2}
    prio = {"critical": "P0", "warning": "P1", "info": "P2"}
    sorted_alerts = sorted(alerts, key=lambda a: order.get(a["rule"].get("severity"), 9))
    return [f"- [{prio.get(a['rule'].get('severity'), 'P2')}] {a['rule'].get('suggestionTemplate', '')}" for a in sorted_alerts]


def _appendix(skill, metrics, columns):
    lines = ["### 指标口径"]
    ms = (skill or {}).get("metrics", []) or []
    shown = False
    for m in ms:
        mid = m.get("metricId", "")
        if mid in metrics:
            val = metrics[mid]
            if _is_rate_metric(mid, m):
                valstr = _fmt_pct(val)
            elif m.get("unit") == "元":
                valstr = _fmt_money(val)
            else:
                valstr = f"{val:,.0f}" if abs(val - round(val)) < 1e-9 else f"{val:,.2f}"
            lines.append(f"- {m.get('businessName', mid)}（{mid}）= {m.get('calculationLogic', '-')}；典型区间 {m.get('typicalRange', '-')}；实测 {valstr}。")
            shown = True
    if not shown:
        lines.append("- 未关联行业技能包，无行业口径。")
    lines.append("")
    lines.append("### 计算说明")
    lines.append("- 比率类指标先对分子、分母列聚合后再相除，避免行级相除再平均。")
    lines.append("- 百分比按 0–1 原始值 ×100 展示；金额按「万/亿」四舍五入展示。")
    lines.append("- 数值证据来自 smartboard-mcp 工具返回与原始数据聚合，未做任何编造。")
    return lines


def _compose_report(title, total_rows, columns, metrics, grouped, ts, alerts, skill, top_metric, top_dim):
    labels = _metric_labels(skill)
    parts = [
        f"# {title} — 洞察报告",
        "",
        "## 一、核心结论",
        "",
        _core_conclusion(metrics, alerts, top_metric, labels),
        "",
        "## 二、关键发现",
        "",
    ]
    for f in _key_findings(total_rows, columns, metrics, grouped, ts, top_metric, labels):
        parts.append(f"- {f}")
    for a in alerts:
        parts.append(f"- **{a['rule'].get('name', '')}**：{_render_alert(a)}")
    parts += ["", "## 三、归因分析（相关关系，非因果）", ""]
    parts += _attribution(grouped, alerts, top_dim, top_metric)
    parts += ["", "## 四、风险与局限", ""]
    for r in _risks(total_rows, columns, metrics):
        parts.append(f"- {r}")
    parts += ["", "## 五、行动建议", ""]
    actions = _actions(alerts)
    if actions:
        parts += actions
    else:
        parts.append("- 未触发预警规则，暂无强制行动项。")
    parts += ["", "## 六、数据附录", ""]
    parts += _appendix(skill, metrics, columns)
    return "\n".join(parts)


def cmd_report(args):
    s = _Session()
    try:
        tmp_dir = tempfile.gettempdir()
        ts = int(time.time())
        tmp_cfg = args.json_out or os.path.join(tmp_dir, f"smartboard-cfg-{ts}.json")
        tmp_html = os.path.join(tmp_dir, f"smartboard-pipe-{ts}.html")
        for p in (args.out, args.json_out, args.report_out):
            if p:
                os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        pipe_args = {"path": args.path, "json_out": tmp_cfg, "out": tmp_html}
        if args.skill_id:
            pipe_args["skill_id"] = args.skill_id
        if args.filter:
            pipe_args["filter"] = args.filter
        if args.title:
            pipe_args["title"] = args.title
        pipe = s.call("smartboard_run_pipeline", pipe_args)
        if not isinstance(pipe, dict) or not pipe.get("ok"):
            raise SystemExit(f"分析管线失败: {json.dumps(pipe, ensure_ascii=False)}")

        with open(tmp_cfg, encoding="utf-8") as f:
            cfg = json.load(f)
        kpis = (cfg.get("config") or {}).get("kpis", [])
        charts = (cfg.get("config") or {}).get("charts", [])
        title = (cfg.get("config") or {}).get("title") or args.title or "SmartBoard 看板"
        columns = pipe.get("columns", [])
        total_rows = pipe.get("total_rows", 0)
        colnames = [c["name"] for c in columns]

        skill = None
        if args.skill_id:
            skill = s.call("smartboard_get_skill", {"skill_id": args.skill_id})

        rows = _apply_filter_rows(_read_csv_rows(args.path), args.filter or "")
        aggs = _col_aggs(rows, colnames)
        metrics = _compute_metrics((skill or {}).get("metrics", []) or [], colnames, aggs)

        date_col = _detect_date_column(columns)
        top_dim = _pick_dimension(columns, date_col)
        top_metric = _pick_top_metric(columns)
        grouped = None
        if top_dim and top_metric:
            grouped = s.call("smartboard_run_analysis", {"analysis_name": "summary", "target_column": top_metric, "group_column": top_dim})
        tseries = None
        if date_col and top_metric:
            tseries = s.call("smartboard_run_analysis", {"analysis_name": "timeseries", "target_column": top_metric, "date_column": date_col, "period": "month"})

        extra = {
            "cac_mom_change": _cac_mom_change(rows, colnames),
            "top_dim_share": _top_dim_share(rows, top_dim, top_metric),
        }
        alerts = _evaluate_rules((skill or {}).get("rules", {}) or {}, metrics, extra)
        md = _compose_report(title, total_rows, columns, metrics, grouped, tseries, alerts, skill, top_metric, top_dim)

        out = args.out or os.path.join(os.getcwd(), f"smartboard-report-{time.strftime('%Y%m%d-%H%M%S')}.html")
        out = os.path.abspath(out)
        out_dir = os.path.dirname(out)
        html = s.call("smartboard_build_html_dashboard", {"title": title, "kpis": kpis, "charts": charts, "insights": md, "out_dir": out_dir})
        html_path = (html or {}).get("html_path")
        if html_path and os.path.abspath(html_path) != out:
            shutil.copyfile(html_path, out)
            try:
                os.remove(html_path)
            except OSError:
                pass
        elif html_path:
            out = html_path

        report_path = None
        if args.report_out:
            report_path = os.path.abspath(args.report_out)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(md)

        for p in (tmp_html,):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        if not args.json_out and os.path.exists(tmp_cfg):
            try:
                os.remove(tmp_cfg)
            except OSError:
                pass

        result = {
            "ok": True,
            "total_rows": total_rows,
            "columns": columns,
            "kpi_count": len(kpis),
            "chart_count": len(charts),
            "html_path": out,
            "report_path": report_path,
            "json_path": os.path.abspath(args.json_out) if args.json_out else None,
            "alert_count": len(alerts),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description="Channel B driver for smartboard-mcp")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run smartboard_run_pipeline")
    p_run.add_argument("--path", required=True, help="absolute path to CSV/Excel")
    p_run.add_argument("--skill-id", dest="skill_id")
    p_run.add_argument("--filter", dest="filter", help="筛选表达式，如 region = 华东 & channel = 线上")
    p_run.add_argument("--title")
    p_run.add_argument("--out", help="absolute HTML output path")
    p_run.add_argument("--json-out", dest="json_out", help="absolute JSON output path")
    p_run.set_defaults(func=cmd_run)

    p_call = sub.add_parser("call", help="call any smartboard_* tool")
    p_call.add_argument("tool", help="tool name, e.g. smartboard_load_data")
    p_call.add_argument("--json", help="JSON object of arguments; omit to read from stdin")
    p_call.set_defaults(func=cmd_call)

    p_report = sub.add_parser("report", help="generate HTML dashboard with a six-section insight report")
    p_report.add_argument("--path", required=True, help="absolute path to CSV/Excel")
    p_report.add_argument("--skill-id", dest="skill_id", help="industry skill id, e.g. industry-education")
    p_report.add_argument("--filter", dest="filter", help="筛选表达式，如 region = 华东 & channel = 线上")
    p_report.add_argument("--title")
    p_report.add_argument("--out", help="absolute HTML output path")
    p_report.add_argument("--json-out", dest="json_out", help="absolute JSON config output path")
    p_report.add_argument("--report-out", dest="report_out", help="optional standalone Markdown report path")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
