---
name: cad-skill
description: 通过 CAD-MCP 在真实 CAD（Windows 上的 AutoCAD / 浩辰 / 中望）里绘图、查图、改图：把自然语言设计意图拆成图元步骤与 MCP 工具调用，产出可继续编辑的 DWG。用于建筑 / 室内 / 规划的平面示意、轴网、墙门窗、家具示意、文字标注、图层组织，以及“先确认方案、再分步落图”的制图流程；不做施工图深度、结构节点详图、BIM 或规范审查。
metadata:
  short-description: 自然语言驱动 CAD 出图（CAD-MCP）
---

# CAD 绘图（CAD-MCP）

链路固定为：**用户负责设计 → Codex 理解并拆成图元步骤 → CAD-MCP 调用 CAD → CAD 软件真正画图 → 存 DWG**。

CAD-MCP 只会基础图元（线/圆/弧/多段线/矩形/文字/填充/标注）+ 查询/编辑/图层/图纸管理。**没有**墙、门、窗、家具、轴网、拉伸、联动改图这类专业对象——建筑构件全靠图元组合，别按“有墙对象”去想。

调用前若不确定工具签名，先读 `references/cad-mcp-tools.md`，不要凭记忆编参数。

## 铁律

1. **先设计，后绘图**：用户确认布局参数前，不调用任何绘图工具；方案阶段只输出房间尺寸、位置（坐标或轴网）、房间关系、墙体控制点。
2. **单位默认 mm**：坐标、墙厚、门窗宽、文字高度、标注字高都用 mm。文字高度要显式给（房间名 250–350），`draw_text` 默认 2.5 在 mm 图纸里等于看不见。
3. **每个对象带 `layer`**：传 `layer` 会自动建层，且不会切走用户当前层（优先这样做，少用 `create_layer`——它会把该层设为当前层）。
4. **分步绘制、每步可检查**：顺序为 图层 → 轴网 → 外墙 → 内墙 → 门窗 → 家具 → 文字 → 尺寸 → 保存。每步画完用 `screenshot` 自检并请用户查看，墙线错了后面全错。
5. **改图靠重画，不靠“拉伸联动”**：明确新尺寸 → 重画相关区域 → 更新相邻边界与标注 → 再检查。不要假设已有对象能被整体拉伸。
6. **破坏性操作先说清**：`erase_entity`、`new_drawing`、`open_drawing`、`close_drawing`、覆盖式 `save_drawing`、`send_command` 执行前说明影响。
7. **不假装**：CAD-MCP 未接入或调用失败时如实说明，并改为交付几何方案 / 坐标表，不要描述成“已画好”。

## 按需读参考

| 情况 | 读 |
| --- | --- |
| 还没接入 CAD、连接报错、环境与安装 | `references/setup-windows.md` |
| 要调用具体工具、确认参数与返回值 | `references/cad-mcp-tools.md` |
| 画建筑平面：图层、构件画法、分步顺序、Prompt 模板、AGENTS.md | `references/drafting-conventions.md` |
| 直墙带洞口（门/窗）的坐标容易算错 | 先跑 `scripts/wall_plan.py` 生成调用序列 |
| 图元多、嫌慢，或要判断该不该改 CAD-MCP 源码 | `references/performance.md` |
| 要装/改服务端本身 | 本仓库 `cad-mcp/` 本地副本（勿联网拉上游代码），改动见 `cad-mcp/PATCHES.md` |

## 工具速查

绘图：`draw_line` `draw_circle` `draw_arc` `draw_ellipse` `draw_polyline` `draw_rectangle` `draw_text` `draw_hatch` `add_dimension`
查询（AI 的“眼睛”）：`list_layers` `list_entities` `get_entity_properties` `screenshot`
编辑：`erase_entity` `move_entity` `rotate_entity` `scale_entity` `copy_entity` `mirror_entity` `offset_entity` `array_linear_entity` `array_polar_entity` `undo` `redo`
图层与图纸：`create_layer` `set_current_layer` `new_drawing` `open_drawing` `close_drawing` `save_drawing`
图块与逃生舱：`create_block` `insert_block` `list_blocks` `send_command`
批量落图（本地扩展）：`draw_script`

坐标一律 `[x, y]`（可带 z）；绘图类工具返回实体句柄，改图先用 `list_entities` 拿 handle。

## 建筑意图 → 图元组合

| 意图 | 组合画法 |
| --- | --- |
| 墙体 | 每段墙一个闭合多段线（矩形截面），端部按转角延伸 t/2 近似接角；轴线转角处可再补角部填充 |
| 门 | 洞两侧墙端封边线（A-WALL）+ 门扇线 + 90° 圆弧（A-DOOR） |
| 窗 | 洞两侧墙端封边线（A-WALL）+ 洞口内外墙面线 + 中位窗线（A-WINDOW，三线示意） |
| 轴网 | `draw_line` 画轴线（A-GRID，中心线型）+ 圆圈 + 轴号文字 |
| 家具 | 矩形 / 多段线示意 + 文字名称（A-FURN），别做成精细图块 |
| 房间名 | `draw_text`，字高 250–350（A-TEXT），位置放房间形心附近，避开墙线 |
| 尺寸 | `add_dimension` 按轴网或墙体控制点成对给，字高 200–250（A-DIM），放在图形外侧 |

图层命名沿用建筑制图惯例：`A-WALL` `A-DOOR` `A-WINDOW` `A-DIM` `A-TEXT` `A-FURN` `A-GRID` `A-HATCH`。

## 直墙 + 洞口：用脚本算坐标

`scripts/wall_plan.py` 读一份 JSON 规格，输出可直接照抄的 MCP 调用序列（含 phases 分步），把墙截面、封边、门扇弧、窗三线都算好：

```bash
python3 scripts/wall_plan.py plan.json --pretty
```

```json
{"walls": [{"from": [0, 0], "to": [12000, 0], "thickness": 240,
  "openings": [{"kind": "door", "width": 1000, "center": 3000, "hinge": "start", "swing": "positive"},
               {"kind": "window", "width": 2400, "center": 7000}]}]}
```

脚本只做几何换算，不产生设计决策；洞口坐标与墙厚必须来自用户确认过的方案。

## 大批量落图：一次脚本，而不是几百次调用

逐条调用 MCP 工具时，每个图元都要走一次跨进程 COM 往返，其中大部分开销花在图层解析和撤销标记上。图元超过几十个就先批量：

```bash
python3 scripts/wall_plan.py plan.json > calls.json
python3 scripts/plan_to_scr.py calls.json --out plan.scr --dimtxt 250
```

落图首选 `draw_script`（本地扩展工具）：把脚本全文整段传进去，服务端落盘后交给 CAD 一次执行完，并回报 `completed` 与实体数前后差值——不用手工把文件拷到 CAD 机器。脚本默认整批一个撤销组、末尾 `ZOOM E`；若 `completed=false`（该 CAD 不支持哨兵变量）或想再核对，就看 `entities_after` 或 `list_entities`，再 `screenshot`，因为脚本执行是异步的。次选是把 `plan.scr` 拷过去在 CAD 里跑 `SCRIPT`，或 `send_command("_.SCRIPT C:\\path\\plan.scr")`。

细节、标注和填充再逐条用 MCP 工具补（`draw_hatch` 无法脚本化）。详见 `references/performance.md`。

## 修改与纠错

1. 先用 `list_entities`（可带 `entity_type` / `limit`）找到目标实体句柄，必要时 `get_entity_properties` 核对坐标。
2. 小改动优先 `move_entity` / `offset_entity` / `erase_entity`；成组或整区改动按新参数重画，比逐条挪更稳。
3. 画错了立刻 `undo`，不要靠继续画掩盖。
4. 改完更新受影响的标注和文字，并重新 `screenshot` 给用户确认。

## 交付前检查

- 图元都落在正确图层，没有全画在 `0` 层
- 没有明显重复线、重合对象、断开的墙线
- 文字与尺寸不压住主要建筑线
- 尺寸标注与用户确认的参数一致（墙厚、开间、门窗宽）
- 已保存为 DWG/DXF，且路径为绝对路径

## 边界

适合：简单平面示意、轴网、房间分隔、文字尺寸填充、按明确坐标反复重画、局部按新尺寸重画。

不适合承诺：复杂施工图、结构图、节点大样、复杂楼梯、幕墙、完整 BIM、规范自动审查，以及可靠的“挪墙 + 联动家具 + 更新标注”一次性批量改图。

立面 / 剖面线稿可以练，但难度明显高于平面，别和“画一条线”放在同一预期里。
