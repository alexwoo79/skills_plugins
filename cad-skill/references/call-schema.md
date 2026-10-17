# 调用序列格式（`calls.json`）

`wall_plan.py` / `image_to_calls.py` / `plan_render.py` 产出的都是同一份结构：

```json
{
  "unit": "mm",
  "calls": [
    {"tool": "draw_polyline",
     "args": {"points": [[0, 0], [3600, 0], [3600, 240], [0, 240]],
              "closed": true, "layer": "A-WALL"}}
  ]
}
```

`cad_direct.py draw --calls` 也接受纯数组 `[{tool, args}, ...]`。
先跑 `--dry-run`，只校验不碰 CAD。

## 通用约定

- `Point = [x, y]` 或 `[x, y, z]`；`Points` 至少 2 个点；填充边界至少 3 个点。
- 单位由图纸决定；建筑图统一 mm。
- `layer` 可选，传了会自动建层（不切换当前层）；不传就落在当前层。
- `color` 可选（ACI 1–256 或中英文色名）；省略即随层。
- 除文字与标注外，其他图元可带 `lineweight`。
- 成功返回实体句柄；失败时整个 `draw` 输出 `ok=false`，逐条结果在 `results` 里。

## 绘图图元

| `tool` | 必填 `args` | 可选 | 说明 |
| --- | --- | --- | --- |
| `draw_line` | `start_point`, `end_point` | `layer/color/lineweight` | 直线 |
| `draw_circle` | `center`, `radius` | 同上 | 半径必须 > 0 |
| `draw_arc` | `center`, `radius`, `start_angle`, `end_angle` | 同上 | 角度为**度**，从 start 到 end **逆时针** |
| `draw_ellipse` | `center`, `major_axis`, `minor_axis` | `rotation`, 样式 | 长短轴均 > 0，minor ≤ major |
| `draw_polyline` | `points` | `closed`, 样式 | 墙身、轮廓用 `closed=true` |
| `draw_rectangle` | `corner1`, `corner2` | 样式 | 两对角点，本质是闭合多段线 |
| `draw_text` | `position`, `text` | `height=2.5`, `rotation=0`, `layer` | 高度是图形单位；mm 图纸必须显式给 |
| `draw_hatch` | `points` | `pattern_name="SOLID"`, `scale`, 样式 | 边界点集需闭合 |
| `add_dimension` | `start_point`, `end_point` | `text_position`, `textheight=5`, `layer` | 线性标注；不给位置时放在中点上方 +5 |

## 查询与编辑（命令行，不是调用序列）

调用序列只负责**画**。查询、改图、保存走 `cad_direct.py` 子命令：

```bash
cad_direct.py layers
cad_direct.py stats
cad_direct.py entities --type line --limit 50
cad_direct.py props --handle <handle>
cad_direct.py erase --handle <handle>
cad_direct.py move --handle <handle> --dx 600 --dy 0
cad_direct.py copy --handle <handle> --dx 3000 --dy 0
cad_direct.py offset --handle <handle> --distance 120
cad_direct.py mirror --handle <handle> --p1 0,0 --p2 0,5000
cad_direct.py rotate --handle <handle> --base 0,0 --angle 90
cad_direct.py scale --handle <handle> --base 0,0 --factor 1.5
cad_direct.py undo --times 1
cad_direct.py redo --times 1
```

## 能力边界

- 只有图元级能力：没有墙体/门窗/家具/轴网这类专业对象，也没有“拉伸并联动标注”。
- CLI 没有暴露块、阵列、关闭图纸、设置当前层等少数命令；需要时用
  `cad_direct.py send --command "_.<英文命令> ..."`，或按
  `scripts/cad_direct.py` 的 `dispatch()` 增加一个分支。
- `draw_hatch` 用逐图元模式可靠；`.scr` 批量脚本无法可靠选择填充边界，会被跳过并告警。
