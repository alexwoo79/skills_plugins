---
name: data-workflow
description: 解析/校验/构造 dataflow-studio 桌面端工作流（Flow JSON），并复用 data-clean skill 探查实际数据、构造可导入 App 的工作流。当用户给出 .json 工作流文件、想看懂一条流水线、或希望"AI 解析→依据数据清洗→构造新工作流→回传 App 导入"的双向互动时使用。列名/数值必须来自 data-clean quality/schema，不得编造。
---

# dataflow-workflow：工作流（Flow JSON）双向互动

目标：让大模型**理解并重构 dataflow-studio 的工作流**，形成「App 导出 → AI 解析/清洗 → 构造新工作流 → 回传 App 导入」闭环。

## 依赖

- `data-clean` skill（清洗/质量画像）：`inspect` 会调用它探查数据。

## Flow JSON schema（与 App 一致）

```json
{
  "id": "uuid", "name": "工作流名",
  "nodes": [
    { "id": "uuid", "type": "input|transform|output",
      "transformType": "filter_rows|...（仅 transform）",
      "position": {"x": 0, "y": 0}, "config": {}, "label": "..." }
  ],
  "edges": [ { "id": "uuid", "from": "nodeId", "to": "nodeId", "port": "left|input-b" } ],
  "pinnedNodeIds": [], "createdAt": 0, "updatedAt": 0
}
```

节点类型：input / transform / output。input 的 config.type ∈ csv|excel|folder|sql|sqlite3|mysql|postgres|google_sheets|http_api|clipboard|feishu。output 的 config 含 path 与 format。

## 脚本命令

```bash
python3 ~/.codex/skills/data-workflow/scripts/workflow.py parse   <flow.json>
python3 ~/.codex/skills/data-workflow/scripts/workflow.py validate <flow.json>
python3 ~/.codex/skills/data-workflow/scripts/workflow.py author  <spec.json> --name N --out new.json
python3 ~/.codex/skills/data-workflow/scripts/workflow.py inspect <flow.json>
```

- parse：输出 name / inputs / outputs / pipeline（按拓扑顺序，含每个 transform 的 transformType + config）。
- validate：校验 schema 与语义，并做 **join 键类型只读检查**（两侧能判定且不一致 → `{ok:false}` + 建议 `type_cast`；无法判定如 folder/派生 → warnings），返回 `{ok, errors, warnings, join_keys}`。
- author：由声明式 spec 构建完整 Flow（自动 uuid、按拓扑深度布局、join 自动分配 left/input-b 端口）。edges 的 from/to 用节点索引。
- inspect：返回 `{sources, join_keys}`。`sources` 对每个 input 跑 dataflow quality（folder 会聚合目录内表格）；`join_keys` 实测每个 join 左右 join 键列类型，标出 `left_type/right_type/consistent` 是否一致，不一致则**须先 type_cast 同型**（走确认门禁）。

## 声明式 spec（author 用）

```json
{
  "name": "合并楼栋",
  "nodes": [
    {"type":"input","config":{"type":"excel","path":"/abs/a.xlsx"}},
    {"type":"input","config":{"type":"excel","path":"/abs/b.xlsx"}},
    {"type":"transform","transformType":"join","config":{"left_on":"楼号","right_on":"楼号","how":"inner"}},
    {"type":"transform","transformType":"deduplicate","config":{}},
    {"type":"output","config":{"path":"/abs/out.xlsx","format":"xlsx"}}
  ],
  "edges":[ {"from":0,"to":2,"port":"left"}, {"from":1,"to":2,"port":"input-b"}, {"from":2,"to":3}, {"from":3,"to":4} ]
}
```

join/concat 目标有两个入边时，脚本把第一个入边标 left、其余 input-b（也可显式给 port）。

## 建议工作流（双向互动）

1. 解析：parse flow.json 读懂现有流水线。
2. 数据探查：inspect flow.json 用 dataflow 跑 quality，找重复/空值/0 值/大小写等问题（列名数值以返回为准）。
3. 候选方案：用 `data-clean suggest` 得到**逐列**清洗建议（问题→建议 op→是否需确认），按列列出要新增/修改的清洗节点，附「做什么 / 影响列 / 理由」。
4. **逐列确认门禁**：逐列发给用户「确认/调整/跳过」；`requires_confirm:true` 的列（删行/去重/改值/合并/拆分）必须用户明确点头，未确认不加入定稿；低风险项（trim/cast_case/add_column 常量）可默认但需注明。
5. 构造与校验：用户确认后，用 author --name N --out 新flow.json 生成，再 validate，确认 {ok:true}。
6. 回传：把新flow.json 路径交给用户，App「导入工作流」即可打开。

## 铁律

1. 列名/数值来自 dataflow quality/schema，禁止编造。
2. join 节点必须两输入：写清 left_on / right_on / how，并有两入边。
3. author 的 edges.from/to 用节点索引；validate 校验用节点 id。
4. 只输出 App 体系内合法的 type / transformType / config；不清楚先 parse 参考现有 flow。
5. 交付：给出新流程 JSON 路径 + 简要改动（新增哪些节点、为什么）。
6. **去重须用户确认**：`inspect` 显示 duplicate_rows 只是“存在相同行”的信号，可能是**有意义的多条记录**（多楼栋同值、多文件同记录）。不得仅凭重复行就自动加 `deduplicate`；先问用户「是否去重、按哪些列去重」，确认后再加。其余会改变语义的清洗（如「把某列空值填 0」）也应先与用户确认，不自动拍板。
7. **join 左右关联列类型必须一致**：左表与右表的 join 键列要同类型，**禁止一侧为数值、另一侧为文本**（会漏配/错配）。构造 join 时先用 `inspect`/`data-clean schema` 确认两侧 join 列类型；若不一致，须先加 `type_cast` 把两侧统一（通常都转 string），且该 `type_cast` 属会改变语义的操作，需走确认门禁。不得在类型不一致时直接 join。
