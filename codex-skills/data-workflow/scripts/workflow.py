#!/usr/bin/env python3
"""dataflow-workflow：解析/校验/构造 dataflow-studio 工作流（Flow JSON）。

用法：
  python3 workflow.py parse   <flow.json>                         # 结构化打印流水线
  python3 workflow.py validate <flow.json>                        # 校验 schema/语义
  python3 workflow.py author  <spec.json> [--name N] [--out o.json]   # 由声明式 spec 构建 Flow
  python3 workflow.py inspect <flow.json> [--limit 200]           # 对输入数据源跑 dataflow quality

Flow JSON schema（与 dataflow-studio 一致）：
  { id, name, nodes:[{id,type(input|transform|output),transformType?,position{x,y},config,label}],
    edges:[{id,from,to,port?}], pinnedNodeIds:[], createdAt, updatedAt }
"""

import json
import os
import subprocess
import sys
import uuid
import time

KNOWN_TRANSFORMS = {
    "filter_rows", "filter_columns", "fill_nulls", "fill_column", "deduplicate",
    "trim", "cast_case", "find_replace", "split_column", "explode", "column_merge",
    "type_cast", "rename", "sort", "join", "concat", "time_derive",
    "case_when", "add_column",
}
KNOWN_INPUT_TYPES = {
    "csv", "excel", "folder", "sql", "sqlite3", "mysql", "postgres",
    "google_sheets", "http_api", "clipboard", "feishu",
}
JOIN_TYPES = {"join", "concat"}


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- validate ----------
def validate_flow(flow):
    errors = []
    if not isinstance(flow, dict):
        return False, ["顶层必须是对象"]
    for k in ("id", "name", "nodes", "edges", "pinnedNodeIds"):
        if k not in flow:
            errors.append(f"缺少字段: {k}")
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        errors.append("nodes/edges 必须是数组")
        return False, errors
    ids = set()
    for i, n in enumerate(nodes):
        nid = n.get("id")
        if not nid:
            errors.append(f"节点[{i}] 缺 id")
            continue
        if nid in ids:
            errors.append(f"节点 id 重复: {nid}")
        ids.add(nid)
        ntype = n.get("type")
        if ntype not in ("input", "transform", "output"):
            errors.append(f"节点[{i}] type 非法: {ntype}")
        if ntype == "transform":
            tt = n.get("transformType")
            if tt not in KNOWN_TRANSFORMS:
                errors.append(f"节点[{i}] transformType 非法: {tt}")
        if ntype == "input":
            ctype = (n.get("config") or {}).get("type")
            if ctype not in KNOWN_INPUT_TYPES:
                errors.append(f"节点[{i}] 输入类型非法: {ctype}")
        pos = n.get("position") or {}
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            errors.append(f"节点[{i}] 缺 position.x/y")
        if not isinstance(n.get("config"), dict):
            errors.append(f"节点[{i}] config 必须是对象")
    for i, e in enumerate(edges):
        if e.get("from") not in ids or e.get("to") not in ids:
            errors.append(f"边[{i}] 引用了不存在的节点: {e.get('from')}->{e.get('to')}")
    # join 语义：join/concat 节点至少 2 条入边
    for n in nodes:
        if n.get("transformType") in JOIN_TYPES:
            indeg = sum(1 for e in edges if e.get("to") == n.get("id"))
            if indeg < 2:
                errors.append(f"join节点 {n.get('id')} 只连了 {indeg} 条入边（需≥2）")
    return len(errors) == 0, errors


# ---------- parse ----------
def topo_order(nodes, edges):
    nid = {n["id"]: i for i, n in enumerate(nodes)}
    indeg = {i: 0 for i in range(len(nodes))}
    adj = {i: [] for i in range(len(nodes))}
    for e in edges:
        if e["from"] in nid and e["to"] in nid:
            a, b = nid[e["from"]], nid[e["to"]]
            adj[a].append(b)
            indeg[b] += 1
    q = [i for i in range(len(nodes)) if indeg[i] == 0]
    order = []
    while q:
        i = q.pop(0)
        order.append(i)
        for j in adj[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                q.append(j)
    return order  # 可能不含环；有环时仅返回可达部分


def parse_flow(flow):
    nodes = flow["nodes"]
    edges = flow["edges"]
    order = topo_order(nodes, edges)
    id2node = {n["id"]: n for n in nodes}
    # 入边/出边
    in_edges = {n["id"]: [] for n in nodes}
    out_edges = {n["id"]: [] for n in nodes}
    for e in edges:
        in_edges.setdefault(e["to"], []).append(e)
        out_edges.setdefault(e["from"], []).append(e)

    steps = []
    inputs = [n for n in nodes if n["type"] == "input"]
    outputs = [n for n in nodes if n["type"] == "output"]
    for i in order:
        n = nodes[i]
        cfg = n.get("config") or {}
        label = n.get("label") or ""
        if n["type"] == "input":
            steps.append({"step": "input", "label": label, "type": cfg.get("type"), "path": cfg.get("path")})
        elif n["type"] == "output":
            steps.append({"step": "output", "label": label, "format": cfg.get("format"), "path": cfg.get("path")})
        else:
            ups = [in_edges[n["id"]][0]["from"] if in_edges[n["id"]] else None]
            port = in_edges[n["id"]][0].get("port") if in_edges[n["id"]] else None
            steps.append({
                "step": "transform", "label": label, "transformType": n["transformType"],
                "config": flatten_config(n["transformType"], cfg),
                "from": ups[0],
            })
    return {
        "name": flow.get("name"),
        "inputs": [{"label": n.get("label"), "type": (n.get("config") or {}).get("type"), "path": (n.get("config") or {}).get("path")} for n in inputs],
        "outputs": [{"label": n.get("label"), "path": (n.get("config") or {}).get("path"), "format": (n.get("config") or {}).get("format")} for n in outputs],
        "pipeline": steps,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def flatten_config(tt, cfg):
    # 仅展示人可读的字段
    keys = ["column", "columns", "value", "operator", "replace", "find", "old_name", "new_name",
            "target_type", "ascending", "left_on", "right_on", "how", "diagonal", "source_column",
            "delimiter", "direction", "part_count", "output_column", "separator", "case",
            "default", "whens", "parts", "keep", "format"]
    out = {}
    for k in keys:
        if k in cfg:
            out[k] = cfg[k]
    if not out and tt == "join":
        for k in ("left_on", "right_on", "how"):
            if k in cfg:
                out[k] = cfg[k]
    return out


# ---------- author ----------
def author_from_spec(spec, name=None, out=None):
    nodes_spec = spec.get("nodes", [])
    edges_spec = spec.get("edges", [])
    if not nodes_spec:
        raise SystemExit("spec 需要 nodes 数组")
    ids = [str(uuid.uuid4()) for _ in nodes_spec]
    # 计算拓扑深度用于布局
    depth = {i: 0 for i in range(len(nodes_spec))}
    adj = {i: [] for i in range(len(nodes_spec))}
    indeg = {i: 0 for i in range(len(nodes_spec))}
    for e in edges_spec:
        a, b = int(e["from"]), int(e["to"])
        adj[a].append(b)
        indeg[b] += 1
    changed = True
    while changed:
        changed = False
        for a in range(len(nodes_spec)):
            for b in adj[a]:
                if depth[b] < depth[a] + 1:
                    depth[b] = depth[a] + 1
                    changed = True
    col_rows = {}
    for i in range(len(nodes_spec)):
        col_rows.setdefault(depth[i], 0)
        col_rows[depth[i]] += 1
    row_idx = {}
    for i in range(len(nodes_spec)):
        row_idx[i] = col_rows[depth[i]] - 1
        col_rows[depth[i]] -= 1

    nodes = []
    for i, s in enumerate(nodes_spec):
        ntype = s.get("type")
        node = {
            "id": ids[i],
            "type": ntype,
            "position": {"x": 100 + depth[i] * 260.0, "y": 60 + row_idx[i] * 120.0},
            "config": s.get("config", {}),
            "label": s.get("label") or default_label(ntype, s),
        }
        if ntype == "transform":
            node["transformType"] = s.get("transformType")
        nodes.append(node)

    edges = []
    for e in edges_spec:
        a, b = int(e["from"]), int(e["to"])
        port = e.get("port")
        # join/concat 且目标有多入边时，自动分配 left/input-b
        if nodes[b].get("transformType") in JOIN_TYPES and port is None:
            n_in = sum(1 for x in edges_spec if int(x["to"]) == b)
            if n_in > 1:
                # 第一个入边视为 left，后续 input-b
                first = next(x["from"] for x in edges_spec if int(x["to"]) == b)
                port = "left" if str(a) == str(first) else "input-b"
        edges.append({"id": str(uuid.uuid4()), "from": ids[a], "to": ids[b], **({"port": port} if port else {})})

    flow = {
        "id": str(uuid.uuid4()),
        "name": name or spec.get("name") or "新建工作流",
        "nodes": nodes,
        "edges": edges,
        "pinnedNodeIds": [],
        "createdAt": int(time.time() * 1000),
        "updatedAt": int(time.time() * 1000),
    }
    return flow


def default_label(ntype, s):
    if ntype == "input":
        return (s.get("config") or {}).get("type") or "输入"
    if ntype == "output":
        return "导出"
    return (s.get("transformType") or "变换")


# ---------- inspect（调用 dataflow skill 的 quality） ----------
def _run_quality(driver, path):
    try:
        r = subprocess.run(
            [sys.executable, driver, "quality", "--source", path],
            capture_output=True, text=True, timeout=180,
        )
        return json.loads(r.stdout)
    except Exception as e:  # noqa
        return {"__error__": str(e)}


def _inspect_folder(dirpath, driver, limit):
    exts = (".csv", ".tsv", ".xlsx", ".xls")
    try:
        files = sorted(
            f for f in os.listdir(dirpath) if f.lower().endswith(exts)
        )
    except OSError as e:
        return {"path": dirpath, "type": "folder", "error": f"无法读取目录: {e}"}
    total = len(files)
    if total == 0:
        return {"path": dirpath, "type": "folder", "error": "文件夹无表格文件", "total_files": 0}
    use = files[:limit] if limit else files
    cols = {}
    file_rows = []
    rows_total = 0
    dup_total = 0
    for f in use:
        fp = os.path.join(dirpath, f)
        q = _run_quality(driver, fp)
        if "__error__" in q:
            continue
        file_rows.append({"file": f, "rows": q.get("total_rows"),
                          "cols": q.get("total_cols"), "duplicate_rows": q.get("duplicate_rows")})
        rows_total += q.get("total_rows", 0)
        dup_total += q.get("duplicate_rows", 0)
        for c in q.get("columns", []):
            d = cols.setdefault(c["name"], {"null": 0, "uniq": 0, "files": 0, "dtype": c.get("dtype")})
            d["null"] += c.get("null_count", 0)
            d["uniq"] += c.get("unique_count", 0)
            d["files"] += 1
    col_profile = [
        {"name": k, "dtype": v["dtype"], "files_seen": v["files"],
         "null_total": v["null"], "unique_total": v["uniq"]}
        for k, v in sorted(cols.items())
    ]
    return {
        "path": dirpath, "type": "folder", "total_files": total,
        "inspected_files": len(file_rows), "total_rows": rows_total,
        "duplicate_rows": dup_total, "files_sampled": file_rows, "columns": col_profile,
    }


def inspect_sources(flow, limit=200):
    driver = os.path.expanduser("~/.codex/skills/data-clean/scripts/dataflow.py")
    results = []
    for n in flow.get("nodes", []):
        if n.get("type") != "input":
            continue
        cfg = n.get("config") or {}
        p = cfg.get("path")
        if not p:
            continue
        itype = cfg.get("type")
        if itype == "folder":
            results.append(_inspect_folder(p, driver, limit))
            continue
        if not os.path.exists(p):
            results.append({"path": p, "type": itype, "error": "路径不存在"})
            continue
        results.append({"path": p, "type": itype, "quality": _run_quality(driver, p)})
    return results


def _run_schema(driver, path):
    try:
        r = subprocess.run(
            [sys.executable, driver, "schema", "--source", path],
            capture_output=True, text=True, timeout=180,
        )
        return json.loads(r.stdout)
    except Exception as e:  # noqa
        return {"__error__": str(e)}


def _trace_input(start_id, flow, seen=None):
    """沿主入边一直回溯到 input 节点（best-effort）。"""
    if seen is None:
        seen = set()
    if start_id in seen:
        return None
    seen.add(start_id)
    nodes = {n["id"]: n for n in flow["nodes"]}
    node = nodes.get(start_id)
    if not node:
        return None
    if node["type"] == "input":
        return node
    ins = [e for e in flow["edges"] if e.get("to") == start_id]
    if not ins:
        return None
    # 优先选 port 为 None/left 的主输入
    ins.sort(key=lambda e: 1 if e.get("port") == "input-b" else 0)
    return _trace_input(ins[0]["from"], flow, seen)


def join_key_check(flow, driver):
    """对每个 join/config，实测左右 join 键列类型，检查是否一致。"""
    out = []
    for n in flow.get("nodes", []):
        if n.get("transformType") not in JOIN_TYPES:
            continue
        cfg = n.get("config") or {}
        left_on, right_on = cfg.get("left_on"), cfg.get("right_on")
        ins = [e for e in flow.get("edges", []) if e.get("to") == n["id"]]
        left_edge = next((e for e in ins if e.get("port") in (None, "left")), None)
        right_edge = next((e for e in ins if e.get("port") == "input-b"), None)

        def side(edge, key):
            if not edge or not key:
                return None, "缺少 join 键/入边"
            # 若直连上游就是该键的 type_cast，则用其目标类型（已同型处理）
            src = next((n for n in flow.get("nodes", []) if n["id"] == edge["from"]), None)
            scfg = (src.get("config") or {}) if src else {}
            if src and src.get("transformType") == "type_cast" and scfg.get("column") == key:
                dtype = {
                    "string": "String", "str": "String", "utf8": "String",
                    "int": "Int64", "i64": "Int64", "integer": "Int64",
                    "float": "Float64", "f64": "Float64", "double": "Float64",
                    "bool": "Boolean", "boolean": "Boolean",
                    "date": "Date", "datetime": "Datetime",
                }.get((scfg.get("target_type") or "").lower())
                return dtype, "已有 type_cast"
            inode = _trace_input(edge["from"], flow)
            if not inode:
                return None, "回溯不到输入节点"
            ccfg = inode.get("config") or {}
            p = ccfg.get("path")
            if not p or not os.path.exists(p):
                return None, f"输入不可用: {p}"
            if ccfg.get("type") == "folder":
                return None, "folder 输入(目录)，逐文件类型待核"
            s = _run_schema(driver, p)
            if "__error__" in s:
                return None, f"schema 失败: {s['__error__']}"
            col = next((c for c in s.get("columns", []) if c.get("name") == key), None)
            if not col:
                return None, f"列 {key} 非直连输入列(可能经派生)"
            return col.get("dtype"), None

        lt, ln = side(left_edge, left_on)
        rt, rn = side(right_edge, right_on)
        consistent = (lt == rt) if (lt and rt) else None
        out.append({
            "join": n.get("label") or "join",
            "left_on": left_on, "right_on": right_on,
            "left_type": lt, "right_type": rt,
            "consistent": consistent,
            "left_note": ln, "right_note": rn,
        })
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]
    path = sys.argv[2]
    if cmd == "parse":
        print(json.dumps(parse_flow(_load(path)), ensure_ascii=False, indent=2))
    elif cmd == "validate":
        flow = _load(path)
        ok, errs = validate_flow(flow)
        warnings = []
        driver = os.path.expanduser("~/.codex/skills/data-clean/scripts/dataflow.py")
        jks = join_key_check(flow, driver) if flow.get("nodes") else []
        for jk in jks:
            if jk["consistent"] is False:
                errs.append(
                    f"join【{jk['join']}】左右键类型不一致：{jk['left_on']}=L:{jk['left_type']} vs {jk['right_on']}=R:{jk['right_type']}；"
                    "请先加 type_cast 把两侧统一（通常转 string）"
                )
                ok = False
            elif jk["consistent"] is None:
                warnings.append(
                    f"join【{jk['join']}】两侧键类型待人工确认（{jk['left_note'] or '?'} / {jk['right_note'] or '?'}）"
                )
        print(json.dumps({
            "ok": ok, "errors": errs, "warnings": warnings, "join_keys": jks,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if ok else 1)
    elif cmd == "author":
        spec = _load(path)
        name = None
        out = None
        out_dir = None
        out_file = None
        args = sys.argv[3:]
        for i, a in enumerate(args):
            if a == "--name" and i + 1 < len(args):
                name = args[i + 1]
            if a == "--out" and i + 1 < len(args):
                out = args[i + 1]
            if a == "--out-dir" and i + 1 < len(args):
                out_dir = args[i + 1]
            if a == "--out-file" and i + 1 < len(args):
                out_file = args[i + 1]
        flow = author_from_spec(spec, name=name)
        # 把最后一个 output 节点的导出路径指到指定位置
        if out_dir or out_file:
            outs = [n for n in flow["nodes"] if n["type"] == "output"]
            if outs:
                fmt = (outs[-1].get("config") or {}).get("format", "xlsx")
                if out_file:
                    outs[-1]["config"]["path"] = out_file
                elif out_dir:
                    base = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "output")).strip("_") or "output"
                    outs[-1]["config"]["path"] = os.path.join(out_dir, f"{base}.{fmt}")
        text = json.dumps(flow, ensure_ascii=False, indent=2)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            print("written: " + out)
        else:
            print(text)
    elif cmd == "inspect":
        flow = _load(path)
        driver = os.path.expanduser("~/.codex/skills/data-clean/scripts/dataflow.py")
        print(json.dumps({
            "sources": inspect_sources(flow),
            "join_keys": join_key_check(flow, driver),
        }, ensure_ascii=False, indent=2))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
