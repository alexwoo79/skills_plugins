# 一份方案，四个出口：calls / .scr / DXF / SVG

以前同一张图有几条各写各的出口：`wall_plan.py` 出调用序列，`plan_to_scr.py` 出脚本，
描图脚本自己写 DXF——结果 calls 里有门窗结构，DXF 里只剩墙矩形和原始线work。

现在统一走 `wall_plan.build_plan()`：墙、门、窗、轴网、尺寸、楼梯都先变成**同一份调用序列**，
再由 `scripts/plan_render.py` 翻译成四个后端。预览看到的和落进 CAD 的因此是同一套图元。

```text
plan.json ──► wall_plan.build_plan() ──► calls
                                      ├──► plan.scr（一次 COM 调用）
                                      ├──► plan.dxf（不过 COM）
                                      └──► preview.svg（浏览器里看）
```

## 命令

```bash
# 四个后端一次出全
python3 scripts/plan_render.py plan.json \
    --out-calls calls.json --out-scr plan.scr \
    --out-dxf plan.dxf --out-svg preview.svg

# 门窗写成图块（DXF 后端）
python3 scripts/plan_render.py plan.json --out-dxf plan.dxf --blocks

# 输入用米，输出仍是毫米
python3 scripts/plan_render.py plan.json --unit m --out-svg preview.svg
```

stdout 是一个 JSON：`summary`（图元数、轴网/尺寸/楼梯数、待复核数）、`warnings`、
`review`（逐条待确认项）和各后端的写入统计。没有指定任何输出时，JSON 里带完整的 `calls_payload`。

## 后端选择

| 后端 | 经过 COM | 适用 | 注意 |
| --- | --- | --- | --- |
| `calls.json` | 是（逐条） | 十几条图元、需要逐步检查 | 图元多了慢，改用 `.scr` |
| `plan.scr` | 一次 | 几十到几千条、整批落图 | 用 `cad_direct.py script --file` 执行 |
| `plan.dxf` | 否 | 上千条、CAD 不在手边、要给合作方 | `open_drawing` 后另存 DWG |
| `preview.svg` | 否 | 方案确认、在 Mac 上审图 | 不需要装 CAD |

SVG 预览按图层着色：墙黑、门绿、窗蓝、轴网灰虚线、尺寸橙、楼梯紫。
`A-ROOM` 和 `A-TRACE` 也会渲染。标注画成"尺寸线 + 端点 + 数值"，用于确认，不是最终出图样式。

## spec 扩展

除了 `walls`，plan.json 可以带三个可选块。它们都可以省略，省略时输出与旧版完全一致。

### 轴网 `axis`

```json
{
  "axis": {
    "x": [0, 3600, 7200, 12000],
    "y": [0, 4800, 8000],
    "labels": {"x": ["1", "2", "3", "4"], "y": ["A", "B", "C"]},
    "extend": 1500,
    "bubble_radius": 400,
    "text_height": 300
  }
}
```

- 只给一部分时，另一方向默认从墙中心线自动推导（`"auto": true`）。
- 轴号默认 x 向为 1/2/3…、y 向为 A/B/C…；`labels` 可覆盖。
- 轴线伸出墙外 `extend`，轴号圆圈画在下方（x 轴）和左侧（y 轴）。

### 自动尺寸 `dims`

```json
{"dims": {"offset": 1200, "textheight": 250, "overall": true, "sides": "xy"}}
```

- 位置默认取墙中心线（有 `axis` 时也可以显式给 `x`/`y`），逐段成链，外加一道总尺寸。
- `sides` 控制画在哪几边（`x` 下边、`y` 左边），`overall` 关掉就只画分段。
- 尺寸调用走 `add_dimension`；`.scr` 里另写 `DIMTXT`，DXF 里用 `CADSKILL` 标注样式。

### 楼梯 `stairs`

```json
{"stairs": [
  {"origin": [2000, 2000], "angle": 90, "steps": 12,
   "tread": 260, "width": 1200, "label": "楼梯"}
]}
```

`origin` 是第一条踏步线的中点，`angle` 是上行方向。生成踏步线、走向箭头和文字（`A-STAR`）。
`steps` 2–60、`tread` 100–400mm、`width` 600–4000mm，超出直接报错。

### 待复核标记 `needs_review`

洞口可以带：

```json
{"kind": "door", "width": 900, "center": 3000,
 "needs_review": true, "review_reason": "未找到可信开启弧"}
```

`build_plan` 会把它汇总到输出的 `review` 数组和 `summary.needs_review`；
SVG 在对应门的合页位置画红色虚线圈和 `?`。

## 门窗图块（DXF 后端）

`--blocks` 会把 `A-DOOR` / `A-WINDOW` 的基础图元替换成 INSERT：

- 门：4 个单开图块（合页 start/end × 开向 positive/negative），单位半径、按门宽缩放；
- 窗：1 个三线图块，按 `(窗宽, 墙厚)` 非等比缩放；
- 插入点分别是合页点和洞口起点，旋转角取墙方向。

图块只影响 DXF。`cad_direct.py` 的绘图分派只覆盖基础图元，没有建块/插块命令，
所以 calls / `.scr` 仍用基础图元；需要块时用 DXF，或在 CAD 里 `BEDIT` 整理。

## 验收

```bash
python3 tests/test_plan_render.py
```

测试锁住了：四个后端的图元数一致；DXF 里门窗弧/线齐全；图块模式恰好替换 25 个图元为 10 个
INSERT；SVG 是合法 XML 且有 5 段门弧、总尺寸文字和待复核圈。
