#!/usr/bin/env python3
"""DataFlow cleaning via MCP (stdio JSON-RPC, no LLM-written code).

Usage:
  python3 dataflow.py quality --source /abs/data.csv
  python3 dataflow.py schema  --source /abs/data.csv
  python3 dataflow.py run     --source /abs/data.csv [--transforms '[...]'] [--out /abs/out.csv]
  python3 dataflow.py call <tool> [--json '{"arg":"val"}']   # (--json omitted → read from stdin)

The driver talks to a compiled `dataflow-mcp` binary and returns JSON on stdout.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid

PROTOCOL_VERSION = "2025-03-26"


def _resolve_launcher():
    """Return the dataflow-mcp binary path (or launcher) and extra env."""
    env = os.environ.get("DATAFLOW_MCP_BIN")
    if env and os.path.exists(env):
        return env, None
    base = os.path.expanduser("~/.dataflow/mcp")
    for name in ("dataflow-mcp", "dataflow-mcp.exe"):
        binary = os.path.join(base, name)
        if os.path.exists(binary):
            return binary, None
    # fallback: repo dev build
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", ".."))
    candidates = [
        os.path.join(repo, "src-tauri", "target", "debug", "dataflow-mcp"),
        os.path.join(repo, "src-tauri", "target", "release", "dataflow-mcp"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c, None
    raise SystemExit("未找到 dataflow-mcp，请设置 DATAFLOW_MCP_BIN 或先构建（cargo build --release）")


class _Session:
    """Single-process MCP session; multiple tool calls share the in-proc state."""

    def __init__(self):
        cmd, _extra = _resolve_launcher()
        self.proc = subprocess.Popen(
            [cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ,
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
        self._send({
            "jsonrpc": "2.0",
            "id": i,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })
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
        return {"__error__": "timeout waiting for dataflow-mcp"}

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


def _extract(response):
    if response is None:
        return {"__error__": "no response from dataflow-mcp"}
    if "error" in response:
        return {"__error__": response["error"]}
    result = response.get("result")
    if isinstance(result, dict):
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
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


def _expand(path):
    return os.path.abspath(os.path.expanduser(path))


def _print(data):
    text = json.dumps(data, ensure_ascii=False, indent=2)
    sys.stdout.write(text + "\n")
    if isinstance(data, dict) and data.get("__error__"):
        sys.exit(1)


def cmd_quality(args):
    s = _Session()
    _print(s.call("dataflow_quality", {"source": _expand(args.source)}))
    s.close()


def cmd_schema(args):
    s = _Session()
    _print(s.call("dataflow_schema", {"source": _expand(args.source)}))
    s.close()


def _detect_input_type(path):
    if os.path.isdir(path):
        return "folder"
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("csv", "tsv"):
        return "csv"
    if ext in ("xlsx", "xls"):
        return "excel"
    return "csv"


def _detect_format(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return "xlsx" if ext in ("xlsx", "xls") else "csv"


def _build_flow_json(source, transforms, out, name):
    """把单源清洗管线映射成 dataflow-studio 可导入的 Flow JSON。
    join 会新增右表 input 节点（input-b）；concat 会把 sources 各生成一个 input。
    """
    def nid():
        return str(uuid.uuid4())

    def input_node(path, label=None, y=90):
        return {
            "id": nid(), "type": "input", "position": {"x": 40, "y": y},
            "config": {"type": _detect_input_type(path), "path": path},
            "label": label or os.path.basename(path),
        }

    nodes, edges = [], []
    src_node = input_node(source, label=os.path.basename(source), y=90)
    nodes.append(src_node)
    prev = src_node["id"]
    x = 320
    row = 0
    extra_source_nodes = {}

    for t in transforms or []:
        tt = t.get("type")
        cfg = {k: v for k, v in t.items() if k != "type"}
        node_id = nid()
        # 逐个处理 join/concat 的额外输入
        if tt == "join" and cfg.get("right"):
            rid = nid()
            rnode = {
                "id": rid, "type": "input", "position": {"x": 40, "y": 190},
                "config": {"type": _detect_input_type(cfg["right"]), "path": cfg["right"]},
                "label": os.path.basename(cfg["right"]),
            }
            nodes.append(rnode)
            cfg = {k: v for k, v in cfg.items() if k != "right"}
            extra_source_nodes[tt] = rid
        elif tt == "concat" and cfg.get("sources"):
            extra_source_nodes[tt] = []
            for s_ in cfg["sources"]:
                sid = nid()
                snode = {
                    "id": sid, "type": "input", "position": {"x": 40, "y": 190},
                    "config": {"type": _detect_input_type(s_), "path": s_},
                    "label": os.path.basename(s_),
                }
                nodes.append(snode)
                extra_source_nodes[tt].append(sid)
            cfg = {k: v for k, v in cfg.items() if k != "sources"}

        nodes.append({
            "id": node_id, "type": "transform", "transformType": tt,
            "position": {"x": x, "y": 90 + row * 40},
            "config": cfg, "label": tt,
        })
        if tt == "join":
            edges.append({"id": nid(), "from": prev, "to": node_id, "port": "left"})
            edges.append({"id": nid(), "from": extra_source_nodes["join"], "to": node_id, "port": "input-b"})
        elif tt == "concat":
            edges.append({"id": nid(), "from": prev, "to": node_id})
            for sid in extra_source_nodes["concat"]:
                edges.append({"id": nid(), "from": sid, "to": node_id})
        else:
            edges.append({"id": nid(), "from": prev, "to": node_id})
        prev = node_id
        x += 270
        row += 1

    out_node = {
        "id": nid(), "type": "output", "position": {"x": x, "y": 90},
        "config": {"path": out, "format": _detect_format(out)}, "label": "导出",
    }
    nodes.append(out_node)
    edges.append({"id": nid(), "from": prev, "to": out_node["id"]})
    return {
        "id": str(uuid.uuid4()), "name": name, "nodes": nodes, "edges": edges,
        "pinnedNodeIds": [], "createdAt": int(time.time() * 1000),
        "updatedAt": int(time.time() * 1000),
    }


def cmd_run(args):
    source = _expand(args.source)
    out_dir = _expand(args.out_dir) if getattr(args, "out_dir", None) else None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    # 确定清洗输出文件
    if args.out:
        out_file = _expand(args.out)
        if out_dir and os.path.dirname(out_file) in ("", "."):
            out_file = os.path.join(out_dir, os.path.basename(out_file))
    else:
        base = os.path.splitext(os.path.basename(source))[0] or "output"
        out_file = os.path.join(out_dir, base + "_cleaned.csv") if out_dir else None
    arguments = {"source": source}
    transforms = []
    if args.transforms:
        try:
            transforms = json.loads(args.transforms)
            arguments["transforms"] = transforms
        except json.JSONDecodeError as e:
            _print({"__error__": f"transforms 不是合法 JSON: {e}"})
            return
    if out_file:
        arguments["output"] = out_file
    s = _Session()
    result = s.call("dataflow_pipeline", arguments)
    s.close()
    # 确定工作流 JSON 路径并生成
    wf_out = _expand(args.workflow_out) if getattr(args, "workflow_out", None) else None
    if wf_out and out_dir and os.path.dirname(wf_out) in ("", "."):
        wf_out = os.path.join(out_dir, os.path.basename(wf_out))
    elif not wf_out and out_dir:
        base = os.path.splitext(os.path.basename(source))[0] or "output"
        wf_out = os.path.join(out_dir, base + "_工作流.json")
    if wf_out:
        base = os.path.splitext(os.path.basename(source))[0] or "清洗"
        flow = _build_flow_json(source, transforms, out_file or "", base + " 清洗工作流")
        with open(wf_out, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False, indent=2)
        if isinstance(result, dict) and "__error__" not in result:
            result["workflow_out"] = wf_out
    _print(result)


def cmd_call(args):
    if args.json is not None:
        arguments = json.loads(args.json)
    else:
        raw = sys.stdin.read()
        arguments = json.loads(raw) if raw.strip() else {}
    if not isinstance(arguments, dict):
        _print({"__error__": "arguments must be a JSON object"})
        return
    s = _Session()
    _print(s.call(args.tool, arguments))
    s.close()


def _build_suggestions(q, pv):
    cols = q.get("columns", [])
    rows = pv.get("rows", [])
    out = {"columns": [], "global": []}
    dup = q.get("duplicate_rows", 0)
    if dup and dup > 0:
        out["global"].append({
            "issue": f"{dup} 行重复（ratio {q.get('duplicate_ratio', 0):.0%}）",
            "suggestion": "deduplicate（全部列 / 指定列）",
            "requires_confirm": True,
        })
    for c in cols:
        name = c["name"]
        dtype = c.get("dtype") or ""
        nr = c.get("null_ratio") or 0
        rec = {
            "column": name, "dtype": dtype, "null_ratio": round(nr, 4),
            "unique_count": c.get("unique_count"), "issues": [], "suggestions": [],
            "requires_confirm": False,
        }
        if nr > 0:
            rec["issues"].append(f"空值 {nr * 100:.0f}%")
            rec["suggestions"].append({"op": "fill_nulls", "config": {"column": name, "value": "0"},
                                       "note": "填充值请确认"})
            rec["requires_confirm"] = True
        vals = [str(r.get(name)) for r in rows if r.get(name) is not None]
        nonempty = [v for v in vals if v.strip()]
        low = dtype.lower()
        if low.startswith("str") and nonempty:
            if any(v != v.strip() for v in nonempty):
                rec["issues"].append("含首尾空格")
                rec["suggestions"].append({"op": "trim", "config": {"columns": [name]}})
            up = any(v.isupper() and not v.islower() for v in nonempty)
            lo = any(v.islower() and not v.isupper() for v in nonempty)
            mix = any(v.upper() != v.lower() and not v.isupper() and not v.islower() for v in nonempty)
            if up and (lo or mix):
                rec["issues"].append("大小写不一致")
                rec["suggestions"].append({"op": "cast_case", "config": {"columns": [name], "case": "lower"}})
        if low.startswith(("int", "float")) and rows:
            zeros = sum(1 for r in rows if isinstance(r.get(name), (int, float)) and r.get(name) == 0)
            if zeros:
                rec["issues"].append(f"{zeros} 个 0 值")
                rec["suggestions"].append({"op": "filter_rows", "config": {"column": name, "operator": ">", "value": "0"},
                                           "note": "可选，是否保留 0 值"})
                rec["requires_confirm"] = True
        if rec["issues"]:
            out["columns"].append(rec)
    return {"ok": True, "source": q.get("source"), "total_rows": q.get("total_rows"),
            "total_cols": q.get("total_cols"), **out}


def cmd_suggest(args):
    s = _Session()
    source = _expand(args.source)
    q = s.call("dataflow_quality", {"source": source})
    pv = s.call("dataflow_pipeline", {"source": source, "transforms": []})
    s.close()
    _print(_build_suggestions(q, pv))


def main():
    parser = argparse.ArgumentParser(description="DataFlow MCP driver")
    sub = parser.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quality", help="数据质量画像")
    q.add_argument("--source", required=True)
    q.set_defaults(fn=cmd_quality)

    sc = sub.add_parser("schema", help="表结构")
    sc.add_argument("--source", required=True)
    sc.set_defaults(fn=cmd_schema)

    sg = sub.add_parser("suggest", help="按列画像给出逐列清洗建议（含是否需确认）")
    sg.add_argument("--source", required=True)
    sg.set_defaults(fn=cmd_suggest)

    r = sub.add_parser("run", help="批量清洗管线")
    r.add_argument("--source", required=True)
    r.add_argument("--transforms", help='JSON 数组，如 \'[{"type":"filter_rows","column":"price","operator":">","value":"4"}]\'')
    r.add_argument("--out")
    r.add_argument("--out-dir", help="清洗结果与工作流导出的目录（不传 --out/--workflow-out 时用默认文件名）")
    r.add_argument("--workflow-out", help="同时生成 dataflow-studio 可导入的工作流 JSON 路径")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("call", help="细粒度工具调用")
    c.add_argument("tool")
    c.add_argument("--json", help='参数 JSON，或省略则从 stdin 读取')
    c.set_defaults(fn=cmd_call)

    args = parser.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
